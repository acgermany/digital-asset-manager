"""Workflows: catalog scan, hot-folder ingests, reviews, job queue."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ... import queue
from ...core import events, locations, reviews as review_ops, settings_store
from ...db import get_session
from ...models import Asset, Ingest, Job, Review
from ..deps import mounts

router = APIRouter()


# ---------------------------------------------------------------- catalog scan

def _scan_status(job: Optional[Job], ms: list) -> dict:
    if job is None:
        return {"running": False, "phase": "idle", "scan_path": None, "progress": 0, "total": 0,
                "current_file": "", "errors": 0, "start_time": None, "end_time": None,
                "error": None, "cancelled": False}
    p = job.progress or {}
    running = job.status in ("queued", "running", "cancelling")
    phase = "stopping" if job.status == "cancelling" else (p.get("phase") or "discovering") if running else "idle"
    return {
        "job_id": job.id, "running": running, "phase": phase,
        "scan_path": locations.to_host(job.payload.get("path"), ms),
        "progress": p.get("progress", 0), "total": p.get("total", 0), "skipped": p.get("skipped", 0),
        "current_file": locations.to_host(p.get("current_file"), ms) or "",
        "errors": p.get("errors", 0),
        "start_time": job.created_at.isoformat() if job.created_at else None,
        "end_time": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error if job.status == "failed" else None,
        "cancelled": job.status == "cancelled",
    }


def _latest_scan(session: Session) -> Optional[Job]:
    return session.query(Job).filter_by(kind="catalog_scan").order_by(Job.id.desc()).first()


@router.get("/scan/status")
def scan_status(session: Session = Depends(get_session)):
    return _scan_status(_latest_scan(session), mounts(session))


@router.post("/scan/start")
def start_scan(scan_path: Optional[str] = None, workers: int = 2, session: Session = Depends(get_session)):
    ms = mounts(session)
    latest = _latest_scan(session)
    if latest and latest.status in ("queued", "running", "cancelling"):
        return {"error": "Scan already in progress"}
    if not scan_path:
        sources = settings_store.get(session, "catalog_sources") or []
        if not sources:
            raise HTTPException(400, "Choose a folder to scan, or add a catalog source in Configure")
        scan_path = sources[0]
    path = locations.to_container(scan_path, ms)
    if not path or not os.path.isdir(path):
        raise HTTPException(400, "That folder is not visible to the app")
    job = queue.enqueue(session, "catalog_scan", queue="scan", max_attempts=1,
                        payload={"path": path, "workers": max(1, min(workers, 8))})
    events.log(session, "catalog_scan_started", actor="user", path=path)
    session.commit()
    return {"status": "started", "path": locations.to_host(path, ms), "job_id": job.id}


@router.post("/scan/cancel")
def cancel_scan(session: Session = Depends(get_session)):
    job = _latest_scan(session)
    if not job or job.status not in ("queued", "running"):
        return {"error": "No scan in progress"}
    job.status = "cancelled" if job.status == "queued" else "cancelling"
    session.commit()
    return {"status": "cancelling"}


# ---------------------------------------------------------------- ingests

def _ingest_row(i: Ingest, ms: list) -> dict:
    return {
        "id": i.id, "asset_id": i.asset_id, "filename": i.original_filename,
        "original_path": locations.to_host(i.original_path, ms),
        "current_path": locations.to_host(i.current_path, ms),
        "provenance": i.provenance, "provenance_meta": i.provenance_meta,
        "ingest_date": i.ingest_date.isoformat() if i.ingest_date else None,
        "sha256": i.sha256, "filesize": i.filesize, "status": i.status, "step": i.step,
        "decision": i.decision, "duplicate_of_asset_id": i.duplicate_of_asset_id,
        "error": i.error, "completed_at": i.completed_at.isoformat() if i.completed_at else None,
        "value_tier": i.asset.value_tier if i.asset else None,
    }


@router.get("/ingests")
def list_ingests(status: Optional[str] = None, limit: int = 50, offset: int = 0,
                 session: Session = Depends(get_session)):
    q = session.query(Ingest)
    if status:
        q = q.filter(Ingest.status.in_(status.split(",")))
    total = q.count()
    rows = q.order_by(Ingest.id.desc()).limit(limit).offset(offset).all()
    counts = dict(session.query(Ingest.status, func.count()).group_by(Ingest.status).all())
    ms = mounts(session)
    return {"ingests": [_ingest_row(i, ms) for i in rows], "total": total, "counts": counts}


@router.post("/ingests/{ingest_id}/retry")
def retry_ingest(ingest_id: int, session: Session = Depends(get_session)):
    i = session.get(Ingest, ingest_id)
    if not i:
        raise HTTPException(404, "Ingest not found")
    if i.status != "failed":
        raise HTTPException(400, "Only failed ingests can be retried")
    if not os.path.isfile(i.current_path or i.original_path):
        raise HTTPException(400, "The file is no longer in the hot folder")
    i.status, i.error = "queued", None
    queue.enqueue(session, "ingest", queue="ingest", ingest_id=i.id, priority=10)
    session.commit()
    return _ingest_row(i, mounts(session))


# ---------------------------------------------------------------- reviews

def _review_row(r: Review, ms: list) -> dict:
    asset = r.asset
    details = dict(r.details or {})
    if "existing" in details:
        details["existing"] = [{**e, "path": locations.to_host(e.get("path"), ms)} for e in details["existing"]]
    if "library_path" in details:
        details["library_path"] = locations.to_host(details["library_path"], ms)
    return {
        "id": r.id, "kind": r.kind, "status": r.status, "priority": r.priority,
        "details": details, "suggestion": r.suggestion, "resolution": r.resolution,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "ingest": _ingest_row(r.ingest, ms) if r.ingest else None,
        "asset": {
            "id": asset.id, "kind": asset.kind, "value_tier": asset.value_tier,
            "tier_confidence": asset.tier_confidence, "tier_reasons": asset.tier_reasons,
            "files": [{"id": f.id, "filename": f.filename, "path": locations.to_host(f.path, ms)}
                      for f in asset.files],
        } if asset else None,
    }


@router.get("/reviews")
def list_reviews(kind: Optional[str] = None, status: str = "open", limit: int = 50, offset: int = 0,
                 session: Session = Depends(get_session)):
    q = session.query(Review).filter(Review.status == status)
    if kind:
        q = q.filter(Review.kind == kind)
    total = q.count()
    rows = q.order_by(Review.priority.desc(), Review.id).limit(limit).offset(offset).all()
    counts = dict(session.query(Review.kind, func.count()).filter(Review.status == "open")
                  .group_by(Review.kind).all())
    ms = mounts(session)
    return {"reviews": [_review_row(r, ms) for r in rows], "total": total, "counts": counts}


class ResolveBody(BaseModel):
    resolution: str
    tier: Optional[str] = None


@router.post("/reviews/{review_id}/resolve")
def resolve_review(review_id: int, body: ResolveBody, session: Session = Depends(get_session)):
    r = session.get(Review, review_id)
    if not r:
        raise HTTPException(404, "Review not found")
    try:
        review_ops.resolve(session, r, body.resolution, settings_store.all_settings(session), tier=body.tier)
    except ValueError as e:
        session.rollback()
        raise HTTPException(400, str(e))
    return _review_row(r, mounts(session))


# ---------------------------------------------------------------- jobs

@router.get("/jobs/summary")
def jobs_summary(session: Session = Depends(get_session)):
    rows = session.query(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status).all()
    summary: dict = {}
    for kind, status, n in rows:
        summary.setdefault(kind, {})[status] = n
    waiting = session.query(Job).filter(Job.status == "queued", Job.waiting_reason.isnot(None)).first()
    return {"jobs": summary, "waiting_reason": waiting.waiting_reason if waiting else None,
            "assets": session.query(func.count(Asset.id)).scalar()}
