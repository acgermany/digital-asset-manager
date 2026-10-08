"""Processing: queue controls, worker registry, batches."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ... import queue
from ...core import events
from ...db import get_session
from ...models import Batch, QueueConfig, Worker
from ...worker import ALL_QUEUES

router = APIRouter()


def _iso(dt):
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------- queues

def _queue_stats(session: Session) -> dict:
    rows = session.execute(text("""
        SELECT queue,
               COUNT(*) FILTER (WHERE status = 'queued' AND run_after <= now())                AS ready,
               COUNT(*) FILTER (WHERE status = 'queued' AND run_after > now())                 AS waiting,
               COUNT(*) FILTER (WHERE status IN ('running', 'cancelling'))                    AS running,
               COUNT(*) FILTER (WHERE status = 'failed' AND finished_at > now() - interval '24 hours') AS failed_24h,
               COUNT(*) FILTER (WHERE status = 'done' AND finished_at > now() - interval '1 hour')     AS done_1h,
               MIN(created_at) FILTER (WHERE status = 'queued')                                AS oldest
        FROM jobs GROUP BY queue""")).all()
    out = {}
    for r in rows:
        out[r.queue] = {"ready": r.ready, "waiting": r.waiting, "running": r.running,
                        "failed_24h": r.failed_24h, "done_1h": r.done_1h, "oldest_queued": _iso(r.oldest)}
    reasons = session.execute(text("""
        SELECT DISTINCT ON (queue) queue, waiting_reason FROM jobs
        WHERE status = 'queued' AND waiting_reason IS NOT NULL ORDER BY queue, id""")).all()
    for q, reason in reasons:
        out.setdefault(q, {})["waiting_reason"] = reason
    return out


def _consumers(session: Session) -> dict:
    """How many live worker slots consume each queue."""
    counts: dict = {}
    for w in session.query(Worker).filter(Worker.status.in_(("running", "draining"))).all():
        if w.status == "draining":
            continue
        for q in w.queues or []:
            counts[q] = counts.get(q, 0) + w.concurrency
    return counts


@router.get("/queues")
def list_queues(session: Session = Depends(get_session)):
    stats = _queue_stats(session)
    consumers = _consumers(session)
    empty = {"ready": 0, "waiting": 0, "running": 0, "failed_24h": 0, "done_1h": 0, "oldest_queued": None}
    return {"queues": [{
        "name": q.name, "description": q.description, "enabled": q.enabled,
        "max_running": q.max_running, "max_attempts": q.max_attempts, "stall_seconds": q.stall_seconds,
        "consumers": consumers.get(q.name, 0), **empty, **stats.get(q.name, {}),
    } for q in sorted(session.query(QueueConfig).all(), key=lambda q: ALL_QUEUES.index(q.name)
                      if q.name in ALL_QUEUES else 99)]}


class QueueUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_running: Optional[int] = None
    unlimited: Optional[bool] = None      # true clears max_running
    max_attempts: Optional[int] = None
    stall_seconds: Optional[int] = None


@router.patch("/queues/{name}")
def update_queue(name: str, body: QueueUpdate, session: Session = Depends(get_session)):
    q = session.get(QueueConfig, name)
    if not q:
        raise HTTPException(404, "Unknown queue")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("unlimited"):
        q.max_running = None
    elif "max_running" in changes:
        if changes["max_running"] is not None and not 0 <= changes["max_running"] <= 256:
            raise HTTPException(400, "Max running must be between 0 and 256 (0 effectively pauses)")
        q.max_running = changes["max_running"]
    if "enabled" in changes:
        q.enabled = bool(changes["enabled"])
    if "max_attempts" in changes:
        if not 1 <= changes["max_attempts"] <= 100:
            raise HTTPException(400, "Retries must be between 1 and 100")
        q.max_attempts = changes["max_attempts"]
    if "stall_seconds" in changes:
        if not 30 <= changes["stall_seconds"] <= 86400:
            raise HTTPException(400, "Stall timeout must be between 30 seconds and 24 hours")
        q.stall_seconds = changes["stall_seconds"]
    events.log(session, "queue_changed", actor="user", queue=name, **changes)
    session.commit()
    return list_queues(session)


@router.post("/queues/{name}/retry-failed")
def retry_failed(name: str, session: Session = Depends(get_session)):
    n = session.execute(text("""
        UPDATE jobs SET status = 'queued', attempts = 0, error = NULL, run_after = now(), finished_at = NULL
        WHERE queue = :q AND status = 'failed'"""), {"q": name}).rowcount
    session.commit()
    return {"requeued": n}


@router.post("/queues/{name}/clear")
def clear_queued(name: str, session: Session = Depends(get_session)):
    """Cancel everything still waiting in a queue (running jobs finish)."""
    n = session.execute(text("""
        UPDATE jobs SET status = 'cancelled', finished_at = now()
        WHERE queue = :q AND status = 'queued'"""), {"q": name}).rowcount
    events.log(session, "queue_cleared", actor="user", queue=name, cancelled=n)
    session.commit()
    return {"cancelled": n}


# ---------------------------------------------------------------- workers

def _worker_dict(w: Worker) -> dict:
    return {"id": w.id, "name": w.name, "hostname": w.hostname, "version": w.version,
            "queues": w.queues or [], "concurrency": w.concurrency,
            "desired_queues": w.desired_queues, "desired_concurrency": w.desired_concurrency,
            "status": w.status, "running": w.running, "processed": w.processed, "failed": w.failed,
            "embedded": w.embedded, "started_at": _iso(w.started_at), "last_seen": _iso(w.last_seen)}


@router.get("/workers")
def list_workers(include_stopped: bool = False, session: Session = Depends(get_session)):
    q = session.query(Worker)
    if not include_stopped:
        q = q.filter(Worker.status.in_(("running", "draining", "offline")))
    workers = sorted(q.all(), key=lambda w: (not w.embedded, w.status != "running", w.name))
    return {"workers": [_worker_dict(w) for w in workers], "all_queues": ALL_QUEUES}


class WorkerUpdate(BaseModel):
    concurrency: Optional[int] = None
    queues: Optional[list[str]] = None
    status: Optional[str] = None          # running | draining
    reset: Optional[bool] = None          # drop overrides, go back to the worker's own settings


@router.patch("/workers/{worker_id}")
def update_worker(worker_id: str, body: WorkerUpdate, session: Session = Depends(get_session)):
    w = session.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "Worker not found")
    if body.reset:
        w.desired_concurrency = w.desired_queues = None
    if body.concurrency is not None:
        if not 0 <= body.concurrency <= 32:
            raise HTTPException(400, "Concurrency must be between 0 and 32")
        w.desired_concurrency = body.concurrency
    if body.queues is not None:
        bad = [q for q in body.queues if q not in ALL_QUEUES]
        if bad:
            raise HTTPException(400, f"Unknown queue(s): {', '.join(bad)}")
        w.desired_queues = body.queues
    if body.status is not None:
        if body.status not in ("running", "draining"):
            raise HTTPException(400, "Status must be running or draining")
        w.status = body.status
    events.log(session, "worker_changed", actor="user", worker=w.name,
               **body.model_dump(exclude_unset=True))
    session.commit()
    return _worker_dict(w)


@router.delete("/workers/{worker_id}")
def forget_worker(worker_id: str, session: Session = Depends(get_session)):
    w = session.get(Worker, worker_id)
    if not w:
        raise HTTPException(404, "Worker not found")
    if w.status in ("running", "draining"):
        raise HTTPException(400, "Stop or drain the worker first; only offline/stopped workers can be removed")
    session.delete(w)
    session.commit()
    return {"success": True}


# ---------------------------------------------------------------- batches

@router.get("/batches")
def list_batches(limit: int = 20, session: Session = Depends(get_session)):
    batches = session.query(Batch).order_by(Batch.id.desc()).limit(max(1, min(limit, 100))).all()
    return {"batches": [queue.batch_progress(session, b) for b in batches]}


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: int, session: Session = Depends(get_session)):
    b = session.get(Batch, batch_id)
    if not b:
        raise HTTPException(404, "Batch not found")
    if b.status != "running":
        raise HTTPException(400, "This batch is not running")
    queue.cancel_batch(session, b)
    session.commit()
    return queue.batch_progress(session, b)


class RenderBody(BaseModel):
    kinds: list[str] = ["image", "video"]


@router.post("/render")
def start_render(body: RenderBody, session: Session = Depends(get_session)):
    """Pre-build thumbnails, display images and video proxies across the library, so
    browsing never waits on ffmpeg. Already-rendered items finish instantly."""
    kinds = [k for k in body.kinds if k in ("image", "video")]
    if not kinds:
        raise HTTPException(400, "Choose images and/or videos")
    if session.query(Batch).filter_by(kind="render", status="running").first():
        raise HTTPException(400, "A render batch is already running")
    batch = queue.create_batch(session, "render", "render", "a.kind = ANY(:kinds)", {"kinds": kinds},
                               "Render previews")
    events.log(session, "render_batch_started", actor="user", batch_id=batch.id, total=batch.total)
    session.commit()
    return queue.batch_progress(session, batch)
