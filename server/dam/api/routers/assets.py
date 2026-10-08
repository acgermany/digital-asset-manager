"""Asset detail (all stored metadata), tags, and library overview/map data."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ...core import locations, metadata, settings_store
from ...core.assets import normalize_tags, set_tags, set_tier
from ...core.projection import similarity_map
from ...db import get_session
from ...models import TIERS, Asset, Embedding, Event, File, Ingest, Review, Segment
from ..deps import mounts

router = APIRouter()


def _iso(dt):
    return dt.isoformat() if dt else None


def _asset_detail(session: Session, asset: Asset) -> dict:
    ms = mounts(session)
    host = lambda p: locations.to_host(p, ms)  # noqa: E731
    ingests = session.query(Ingest).filter(
        (Ingest.asset_id == asset.id) | (Ingest.duplicate_of_asset_id == asset.id)
    ).order_by(Ingest.id).all()
    embeddings = session.query(Embedding.model, Embedding.created_at, Embedding.source_hash) \
        .filter(Embedding.asset_id == asset.id).all()
    reviews = session.query(Review).filter(Review.asset_id == asset.id).order_by(Review.id).all()
    ingest_ids = [i.id for i in ingests]
    evts = session.query(Event).filter(
        (Event.asset_id == asset.id) | (Event.ingest_id.in_(ingest_ids) if ingest_ids else False)
    ).order_by(Event.id.desc()).limit(50).all()
    return {
        "asset": {
            "id": asset.id, "sha256": asset.sha256, "filesize": asset.filesize, "kind": asset.kind,
            "mime_type": asset.mime_type, "is_media": asset.is_media,
            "captured_at": _iso(asset.captured_at), "first_seen_at": _iso(asset.first_seen_at),
            "updated_at": _iso(asset.updated_at),
            "value_tier": asset.value_tier, "tier_source": asset.tier_source,
            "tier_confidence": asset.tier_confidence, "tier_reasons": asset.tier_reasons or [],
            "protected": asset.protected, "needs_backup": asset.needs_backup,
            "tags": asset.tags or [], "metadata_status": asset.metadata_status,
            "search_text": asset.search_text,
        },
        "files": [{
            "id": f.id, "filename": f.filename, "path": host(f.path), "source": f.source,
            "provenance": f.provenance, "filesize": f.filesize, "sha256": f.sha256,
            "created_at": _iso(f.created_at), "scanned_at": _iso(f.scanned_at),
            "marked_for_deletion": f.marked_for_deletion, "exists": os.path.isfile(f.path),
        } for f in asset.files],
        "ingests": [{
            "id": i.id, "filename": i.original_filename, "original_path": host(i.original_path),
            "current_path": host(i.current_path), "provenance": i.provenance,
            "provenance_meta": i.provenance_meta, "ingest_date": _iso(i.ingest_date),
            "completed_at": _iso(i.completed_at), "status": i.status, "decision": i.decision,
            "sha256": i.sha256, "filesize": i.filesize,
        } for i in ingests],
        "metadata": {
            "media_summary": metadata.summarize_mediainfo(asset.mediainfo),
            "exif": asset.exif or {},
            "document": asset.doc_features or {},
            "mediainfo": asset.mediainfo,
        },
        "embeddings": [{"model": m, "created_at": _iso(c), "source_hash": h} for m, c, h in embeddings],
        "segments": session.query(func.count(Segment.id)).filter(Segment.asset_id == asset.id).scalar(),
        "reviews": [{"id": r.id, "kind": r.kind, "status": r.status, "resolution": r.resolution,
                     "created_at": _iso(r.created_at), "resolved_at": _iso(r.resolved_at)} for r in reviews],
        "events": [{"ts": _iso(e.ts), "actor": e.actor, "action": e.action, "details": e.details}
                   for e in evts],
    }


def _get_asset(session: Session, asset_id: int) -> Asset:
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@router.get("/assets/{asset_id}")
def asset_detail(asset_id: int, session: Session = Depends(get_session)):
    return _asset_detail(session, _get_asset(session, asset_id))


@router.get("/files/{file_id}/details")
def file_details(file_id: int, session: Session = Depends(get_session)):
    f = session.get(File, file_id)
    if not f or not f.asset_id:
        raise HTTPException(404, "File not found")
    return {"file_id": file_id, **_asset_detail(session, _get_asset(session, f.asset_id))}


class AssetUpdate(BaseModel):
    tags: Optional[list[str]] = None
    value_tier: Optional[str] = None


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: int, body: AssetUpdate, session: Session = Depends(get_session)):
    asset = _get_asset(session, asset_id)
    if body.value_tier is not None:
        if body.value_tier not in TIERS:
            raise HTTPException(400, f"Tier must be one of {', '.join(TIERS)}")
        if body.value_tier != asset.value_tier:
            set_tier(session, asset, body.value_tier)
    if body.tags is not None and normalize_tags(body.tags) != (asset.tags or []):
        set_tags(session, asset, body.tags)
    session.commit()
    return _asset_detail(session, asset)


@router.get("/tags")
def list_tags(session: Session = Depends(get_session)):
    rows = session.execute(text("""
        SELECT tag, COUNT(*) FROM assets, jsonb_array_elements_text(tags) AS tag
        GROUP BY tag ORDER BY COUNT(*) DESC, tag LIMIT 500""")).all()
    return {"tags": [{"tag": t, "count": n} for t, n in rows]}


# ---------------------------------------------------------------- library overview / map

@router.get("/library/overview")
def overview(session: Session = Depends(get_session)):
    """Aggregates for the dashboard charts."""
    by_source = session.execute(text("""
        SELECT grp, kind, COUNT(*), COALESCE(SUM(filesize), 0) FROM (
            SELECT DISTINCT a.id, a.kind, a.filesize,
                   COALESCE(NULLIF(f.provenance, ''),
                            CASE f.source WHEN 'catalog' THEN 'Scanned' ELSE 'unspecified' END) AS grp
            FROM assets a JOIN files f ON f.asset_id = a.id) t
        GROUP BY grp, kind""")).all()
    groups: dict = {}
    for grp, kind, n, size in by_source:
        g = groups.setdefault(grp, {"group": grp, "count": 0, "bytes": 0, "kinds": {}})
        g["kinds"][kind] = {"count": n, "bytes": int(size)}
        g["count"] += n
        g["bytes"] += int(size)
    timeline = session.execute(text("""
        SELECT to_char(date_trunc('month', COALESCE(a.captured_at, a.first_seen_at)), 'YYYY-MM') AS month,
               COUNT(*), SUM(CASE WHEN a.captured_at IS NOT NULL THEN 1 ELSE 0 END)
        FROM assets a GROUP BY month ORDER BY month""")).all()
    tiers = dict(session.query(Asset.value_tier, func.count()).group_by(Asset.value_tier).all())
    return {
        "groups": sorted(groups.values(), key=lambda g: -g["bytes"]),
        "timeline": [{"month": m, "count": n, "captured": c} for m, n, c in timeline],
        "tiers": {t: tiers.get(t, 0) for t in TIERS},
    }


@router.get("/library/map")
def library_map(session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    data = similarity_map(session, cfg["embed_model"])
    if data is None:
        return {"model": cfg["embed_model"], "total": 0, "shown": 0, "points": []}
    return data
