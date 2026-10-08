"""Hot-folder ingest: register -> hash -> dedupe -> metadata/classify -> library."""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import queue
from ..config import settings as env
from ..models import PROTECTED_TIERS, Asset, File, Ingest, Review
from . import events, metadata
from .assets import build_search_text
from .classify import classify
from .files import kind_for, local_move, mime_for, name_key, verified_move
from .hashing import sha256_file

log = logging.getLogger(__name__)

ACTIVE = ("queued", "processing")
SIDECAR_MAX_BYTES = 64 * 1024


class Deferred(Exception):
    """The job can't run yet (e.g. the library is not mounted); retry later."""


class IngestError(Exception):
    """Permanent failure for this ingest."""


def library_ready(library: Optional[str]) -> bool:
    return bool(library) and os.path.isfile(os.path.join(library, env.library_marker))


def provenance_for(hotfolder: str, path: str) -> str:
    rel = os.path.relpath(path, hotfolder)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "unspecified"


def read_sidecar(path: str) -> Optional[dict]:
    sidecar = path + ".json"
    try:
        if os.path.getsize(sidecar) > SIDECAR_MAX_BYTES:
            return None
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"value": data}
    except (OSError, ValueError):
        return None


def should_register(session: Session, path: str, size: int, mtime: float) -> bool:
    """Skip files already queued/processing, or that already failed unchanged."""
    rows = session.query(Ingest.status, Ingest.filesize, Ingest.original_mtime).filter(
        Ingest.original_path == path,
        Ingest.status.in_(ACTIVE + ("failed",)),
    ).all()
    for status, fsize, fmtime in rows:
        if status in ACTIVE:
            return False
        if status == "failed" and fsize == size and fmtime == mtime:
            return False
    return True


def register(session: Session, hotfolder: str, path: str, size: int, mtime: float) -> Ingest:
    ingest = Ingest(
        original_filename=os.path.basename(path),
        original_path=path,
        original_mtime=mtime,
        provenance=provenance_for(hotfolder, path),
        provenance_meta=read_sidecar(path),
        filesize=size,
        status="queued",
        current_path=path,
    )
    session.add(ingest)
    session.flush()
    queue.enqueue(session, "ingest", ingest_id=ingest.id, priority=10)
    events.log(session, "ingest_registered", ingest_id=ingest.id, path=path, provenance=ingest.provenance)
    return ingest


def _library_dir(library: str, provenance: str, when: datetime) -> str:
    safe = provenance.replace("/", "_").strip() or "unspecified"
    return os.path.join(library, safe, f"{when:%Y}", f"{when:%m}")


def _existing_copy_available(session: Session, asset: Asset, ingest_id: int) -> bool:
    if any(os.path.isfile(f.path) for f in asset.files):
        return True
    # Another ingest of the same content is mid-flight: its file is the copy.
    in_flight = session.query(Ingest).filter(
        Ingest.asset_id == asset.id, Ingest.id != ingest_id, Ingest.status == "processing"
    ).all()
    return any(i.current_path and os.path.isfile(i.current_path) for i in in_flight)


def _known_names(session: Session, asset: Asset, ingest_id: int) -> set:
    names = {name_key(f.filename) for f in asset.files}
    rows = session.query(Ingest.original_filename).filter(
        Ingest.asset_id == asset.id, Ingest.id != ingest_id,
        Ingest.status.in_(("accepted", "processing")),
    ).all()
    names.update(name_key(r[0]) for r in rows)
    return names


def record_library_file(session: Session, asset: Asset, path: str, size: int, provenance: str) -> File:
    """Add (or, when restoring a lost copy to the same path, refresh) a library File row."""
    f = session.query(File).filter_by(path=path).one_or_none()
    if f is None:
        f = File(path=path)
        session.add(f)
    f.asset_id, f.filename, f.filesize, f.sha256 = asset.id, os.path.basename(path), size, asset.sha256
    f.source, f.provenance, f.marked_for_deletion = "library", provenance, False
    return f


def extract_metadata(session: Session, asset: Asset, path: str, rel_path: str, threshold: float) -> Optional[Review]:
    """Fill mediainfo/EXIF/doc features and tier. Returns a review if a human should confirm."""
    kind = asset.kind
    if kind in ("image", "video", "audio", "other"):
        info = metadata.mediainfo(path)
        if info and metadata.is_media(info):
            asset.mediainfo = info
            asset.is_media = True
            if kind == "other":
                tracks = metadata.media_tracks(info)
                asset.kind = kind = "video" if "Video" in tracks else "audio"
        elif kind in ("image",) and info:
            asset.mediainfo = info
    if kind in ("image", "video", "audio"):
        asset.exif = metadata.exif(path)
        asset.captured_at = metadata.captured_at(asset.exif)
    doc_text = metadata.document_text(path) if kind == "document" else None

    result = classify(kind, os.path.basename(path), rel_path, exif=asset.exif,
                      media_summary=metadata.summarize_mediainfo(asset.mediainfo),
                      doc_text=doc_text, threshold=threshold)
    asset.value_tier = result.tier
    asset.tier_source = "rule"
    asset.tier_confidence = result.confidence
    asset.tier_reasons = result.reasons
    asset.protected = result.tier in PROTECTED_TIERS
    asset.needs_backup = result.tier in PROTECTED_TIERS
    if result.doc_features is not None:
        asset.doc_features = result.doc_features
    asset.metadata_status = "done"

    if result.needs_review:
        review = Review(kind="classification", asset_id=asset.id,
                        priority={"critical": 30, "precious": 20}.get(result.tier, 5),
                        details={"tier": result.tier, "confidence": result.confidence,
                                 "reasons": result.reasons},
                        suggestion={"tier": result.tier})
        session.add(review)
        return review
    return None


def process(session: Session, ingest: Ingest, cfg: dict) -> None:
    """Run the whole pipeline for one ingest. Each step is safe to re-run."""
    path = ingest.current_path or ingest.original_path
    if ingest.status not in ACTIVE:
        return
    if not os.path.isfile(path):
        raise IngestError("File is no longer in the hot folder")
    library = cfg.get("library")
    if not library_ready(library):
        raise Deferred("Library is not available (not configured, or the drive/NAS is not mounted)")

    ingest.status, ingest.step, ingest.error = "processing", "hash", None
    session.commit()

    size = os.path.getsize(path)
    if size == 0:
        raise IngestError("Empty file (0 bytes) — nothing to ingest. If a copy is still running, "
                          "it will be picked up again once data arrives.")
    sha = sha256_file(path)
    ingest.sha256, ingest.filesize = sha, size
    ingest.step = "dedupe"

    # Serialise decisions for the same content across workers.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:s))"), {"s": sha})
    asset = session.query(Asset).filter_by(sha256=sha).one_or_none()
    hotfolder = cfg.get("hotfolder") or os.path.dirname(path)
    rel_path = os.path.relpath(ingest.original_path, hotfolder)

    resuming = asset is not None and ingest.asset_id == asset.id and ingest.decision in ("new", "restore")

    if resuming:
        # A previous attempt already decided; carry on from metadata/library.
        session.commit()
        if ingest.decision == "new" and asset.metadata_status != "done":
            ingest.step = "metadata"
            session.commit()
            extract_metadata(session, asset, path, rel_path,
                             cfg.get("tier_threshold", env.tier_confidence_threshold))
            session.commit()
    elif asset is not None:
        ingest.asset_id = ingest.duplicate_of_asset_id = asset.id
        same_name = name_key(ingest.original_filename) in _known_names(session, asset, ingest.id)
        available = _existing_copy_available(session, asset, ingest.id)

        if same_name and available:
            ingest.decision = "duplicate_exact"
            if cfg.get("delete_mode") == "trash":
                ingest.current_path = local_move(path, os.path.join(hotfolder, "_trash"), ingest.original_filename)
                action = "trashed"
            else:
                os.unlink(path)
                ingest.current_path = None
                action = "deleted"
            ingest.status, ingest.step = "duplicate_deleted", None
            ingest.completed_at = datetime.now(timezone.utc)
            events.log(session, "duplicate_removed", ingest_id=ingest.id, asset_id=asset.id,
                       removal=action, filename=ingest.original_filename, sha256=sha)
            session.commit()
            return

        if not same_name:
            ingest.decision = "duplicate_review"
            ingest.current_path = local_move(path, os.path.join(hotfolder, "_review"),
                                             f"{ingest.id}_{ingest.original_filename}")
            ingest.status, ingest.step = "duplicate_review", None
            ingest.completed_at = datetime.now(timezone.utc)
            existing = [{"file_id": f.id, "path": f.path, "filename": f.filename} for f in asset.files]
            session.add(Review(kind="exact_name_differs", ingest_id=ingest.id, asset_id=asset.id,
                               priority=40 if asset.protected else 10,
                               details={"incoming_name": ingest.original_filename,
                                        "existing": existing, "sha256": sha}))
            events.log(session, "duplicate_held_for_review", ingest_id=ingest.id, asset_id=asset.id,
                       incoming=ingest.original_filename)
            session.commit()
            return

        # Same name but no surviving copy: this arrival restores the asset.
        ingest.decision = "restore"
        session.commit()
    else:
        asset = Asset(sha256=sha, filesize=size, kind=kind_for(ingest.original_filename),
                      mime_type=mime_for(ingest.original_filename))
        session.add(asset)
        session.flush()
        ingest.asset_id = asset.id
        ingest.decision = "new"
        session.commit()  # releases the advisory lock

        ingest.step = "metadata"
        session.commit()
        extract_metadata(session, asset, path, rel_path, cfg.get("tier_threshold", env.tier_confidence_threshold))
        session.commit()

    ingest.step = "library"
    session.commit()
    when = asset.captured_at or ingest.ingest_date or datetime.now(timezone.utc)
    dest = verified_move(path, _library_dir(library, ingest.provenance, when), ingest.original_filename, sha)
    record_library_file(session, asset, dest, size, ingest.provenance)
    ingest.current_path = dest
    ingest.status, ingest.step = "accepted", None
    ingest.completed_at = datetime.now(timezone.utc)
    session.flush()
    session.refresh(asset)
    asset.search_text = build_search_text(asset, session)
    events.log(session, "ingest_accepted", ingest_id=ingest.id, asset_id=asset.id,
               library_path=dest, tier=asset.value_tier, decision=ingest.decision)
    session.commit()
