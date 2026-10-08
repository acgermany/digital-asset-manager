import os

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ...core import locations, settings_store
from ...core.ingest import library_ready
from ...db import get_session
from ...models import Asset, File, Ingest, Job, Review

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    cfg = settings_store.all_settings(session)
    return {
        "status": "ok",
        "hotfolder_ready": bool(cfg["hotfolder"]) and locations.is_dir(cfg["hotfolder"]),
        "library_ready": library_ready(cfg["library"]),
    }


@router.get("/stats")
def stats(session: Session = Depends(get_session)):
    total = session.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(filesize), 0) FROM files")).one()
    dup = session.execute(text("""
        SELECT COUNT(*), COALESCE(SUM(f.filesize), 0)
        FROM files f JOIN (SELECT asset_id FROM files GROUP BY asset_id HAVING COUNT(*) > 1) d
          ON d.asset_id = f.asset_id""")).one()
    wasted = session.execute(text("""
        SELECT COALESCE(SUM(total - one), 0) FROM (
          SELECT SUM(filesize) AS total, MIN(filesize) AS one
          FROM files WHERE asset_id IS NOT NULL GROUP BY asset_id HAVING COUNT(*) > 1) g""")).scalar()
    marked = session.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(filesize), 0) FROM files WHERE marked_for_deletion")).one()
    tiers = dict(session.query(Asset.value_tier, func.count()).group_by(Asset.value_tier).all())
    backup = session.query(func.count()).filter(Asset.needs_backup.is_(True)).scalar()
    open_reviews = dict(session.query(Review.kind, func.count())
                        .filter(Review.status == "open").group_by(Review.kind).all())
    ingests = dict(session.query(Ingest.status, func.count()).group_by(Ingest.status).all())
    jobs = dict(session.query(Job.status, func.count())
                .filter(Job.status.in_(("queued", "running"))).group_by(Job.status).all())
    return {
        "total_files": total[0], "total_size": total[1],
        "unique_files": session.query(func.count(func.distinct(File.asset_id))).scalar(),
        "duplicate_files": dup[0], "duplicate_size": dup[1], "wasted_space": wasted,
        "marked_for_deletion": marked[0], "marked_size": marked[1],
        "assets": session.query(func.count(Asset.id)).scalar(),
        "tiers": tiers, "needs_backup": backup,
        "open_reviews": open_reviews, "ingests": ingests, "jobs": jobs,
    }
