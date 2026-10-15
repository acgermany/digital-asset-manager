"""Resolving review items."""
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Asset, Ingest, Review
from . import events
from .assets import build_search_text, set_tier
from .files import unique_path, verified_move_to
from .ingest import library_path, library_ready, record_library_file

RESOLUTIONS = {
    "exact_name_differs": ("delete_incoming", "keep_both"),
    "name_collision": ("delete_incoming", "keep_both"),
    "classification": ("confirm", "set_tier"),
}


def resolve(session: Session, review: Review, resolution: str, cfg: dict,
            tier: Optional[str] = None, actor: str = "user") -> Review:
    if review.status != "open":
        raise ValueError("Review is already resolved")
    allowed = RESOLUTIONS.get(review.kind, ())
    if resolution not in allowed:
        raise ValueError(f"Resolution must be one of: {', '.join(allowed)}")

    if review.kind in ("exact_name_differs", "name_collision"):
        ingest = session.get(Ingest, review.ingest_id)
        path = ingest.current_path if ingest else None
        if resolution == "delete_incoming":
            if path and os.path.isfile(path):
                os.unlink(path)
            ingest.current_path = None
            if review.kind == "exact_name_differs":
                ingest.status = "duplicate_deleted"
                events.log(session, "duplicate_removed", actor=actor, ingest_id=ingest.id,
                           asset_id=review.asset_id, removal="deleted_after_review")
            else:
                ingest.status = "discarded"
                events.log(session, "collision_incoming_deleted", actor=actor, ingest_id=ingest.id,
                           asset_id=review.asset_id, library_path=review.details.get("library_path"))
                _drop_if_orphaned(session, review)
        else:  # keep_both: file the incoming copy next to the existing one, as `name (1).ext`
            library = cfg.get("library")
            if not library_ready(library):
                raise ValueError("Library is not available")
            if not path or not os.path.isfile(path):
                raise ValueError("The held file is no longer in the review folder")
            asset = session.get(Asset, review.asset_id)
            when = asset.captured_at or ingest.ingest_date
            target = review.details.get("library_path") or library_path(
                library, cfg.get("hotfolder"), ingest, when)
            dest = verified_move_to(path, unique_path(target), ingest.sha256)
            record_library_file(session, asset, dest, ingest.filesize, ingest.provenance)
            ingest.current_path = dest
            ingest.status = "accepted"
            session.flush()
            session.refresh(asset)
            asset.search_text = build_search_text(asset, session)
            events.log(session, "duplicate_kept", actor=actor, ingest_id=ingest.id,
                       asset_id=asset.id, library_path=dest)

    elif review.kind == "classification":
        asset = session.get(Asset, review.asset_id)
        if resolution == "set_tier":
            if not tier:
                raise ValueError("tier is required")
            set_tier(session, asset, tier, actor=actor)
        else:
            asset.tier_source = "user"
            events.log(session, "tier_confirmed", actor=actor, asset_id=asset.id, tier=asset.value_tier)

    review.status = "resolved"
    review.resolution = resolution if resolution != "set_tier" else f"set_tier:{tier}"
    review.resolved_by = actor
    review.resolved_at = datetime.now(timezone.utc)
    session.commit()
    return review


def _drop_if_orphaned(session: Session, review: Review) -> None:
    """An asset created only for a discarded collision has no copy anywhere: remove it."""
    asset = session.get(Asset, review.asset_id) if review.asset_id else None
    if asset is None or asset.files:
        return
    others = session.query(Ingest).filter(Ingest.asset_id == asset.id, Ingest.id != review.ingest_id,
                                          Ingest.status.in_(("queued", "processing", "accepted",
                                                             "duplicate_review", "collision_review"))).count()
    if others:
        return
    review.asset_id = None
    session.query(Ingest).filter(Ingest.asset_id == asset.id).update({"asset_id": None})
    session.query(Ingest).filter(Ingest.duplicate_of_asset_id == asset.id).update({"duplicate_of_asset_id": None})
    session.flush()
    session.delete(asset)
