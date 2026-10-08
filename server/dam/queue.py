"""Durable job queue on Postgres, shared by any number of workers.

- Claiming uses FOR UPDATE SKIP LOCKED, so each job goes to exactly one worker.
- Per-queue controls (queues table): paused queues are skipped; `max_running` is a
  cluster-wide cap enforced under a per-queue advisory lock at claim time.
- Workers refresh `locked_at` on their running jobs (heartbeat); jobs whose lock goes
  stale past the queue's `stall_seconds` are put back for another worker.
"""
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import Batch, Job

LIVE = ("queued", "running", "cancelling")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _queue_attempts(session: Session, queue: str) -> int:
    v = session.execute(text("SELECT max_attempts FROM queues WHERE name = :q"), {"q": queue}).scalar()
    return v or 3


# ---------------------------------------------------------------- enqueue

def enqueue(session: Session, kind: str, queue: str = "ingest", priority: int = 0,
            ingest_id: Optional[int] = None, asset_id: Optional[int] = None,
            payload: Optional[dict] = None, max_attempts: Optional[int] = None,
            batch_id: Optional[int] = None) -> Optional[Job]:
    """Add one job. For asset jobs, returns None if that asset already has a live job of this kind."""
    row = session.execute(text("""
        INSERT INTO jobs (kind, queue, status, priority, ingest_id, asset_id, batch_id, payload, progress,
                          attempts, max_attempts, run_after, created_at)
        VALUES (:kind, :queue, 'queued', :priority, :ingest_id, :asset_id, :batch_id,
                CAST(:payload AS jsonb), '{}'::jsonb, 0, :max_attempts, now(), now())
        ON CONFLICT (kind, asset_id) WHERE asset_id IS NOT NULL AND status IN ('queued','running','cancelling')
        DO NOTHING
        RETURNING id
    """), {"kind": kind, "queue": queue, "priority": priority, "ingest_id": ingest_id,
           "asset_id": asset_id, "batch_id": batch_id, "payload": _json(payload or {}),
           "max_attempts": max_attempts or _queue_attempts(session, queue)}).first()
    session.flush()
    return session.get(Job, row[0]) if row else None


def _json(value) -> str:
    import json
    return json.dumps(value)


TIER_PRIORITY = ("CASE a.value_tier WHEN 'critical' THEN 40 WHEN 'precious' THEN 30 "
                 "WHEN 'standard' THEN 20 WHEN 'low' THEN 10 ELSE 0 END")


def enqueue_assets(session: Session, kind: str, queue: str, where_sql: str, params: dict,
                   batch_id: Optional[int] = None, payload: Optional[dict] = None,
                   base_priority: int = 0) -> int:
    """Set-based fan-out: one job per matching asset (alias `a`), valuable first.
    Assets that already have a live job of this kind are skipped."""
    result = session.execute(text(f"""
        INSERT INTO jobs (kind, queue, status, priority, asset_id, batch_id, payload, progress,
                          attempts, max_attempts, run_after, created_at)
        SELECT :kind, :queue, 'queued', :base + {TIER_PRIORITY}, a.id, :batch_id,
               CAST(:payload AS jsonb), '{{}}'::jsonb, 0, :max_attempts, now(), now()
        FROM assets a WHERE {where_sql}
        ON CONFLICT (kind, asset_id) WHERE asset_id IS NOT NULL AND status IN ('queued','running','cancelling')
        DO NOTHING
    """), {"kind": kind, "queue": queue, "batch_id": batch_id, "payload": _json(payload or {}),
           "max_attempts": _queue_attempts(session, queue), "base": base_priority, **params})
    return result.rowcount or 0


# ---------------------------------------------------------------- batches

def create_batch(session: Session, kind: str, queue: str, where_sql: str, params: dict,
                 label: str, created_by: str = "user", payload: Optional[dict] = None) -> Batch:
    batch = Batch(kind=kind, label=label, status="running", created_by=created_by, payload=payload or {})
    session.add(batch)
    session.flush()
    batch.total = enqueue_assets(session, kind, queue, where_sql, params, batch_id=batch.id, payload=payload)
    if batch.total == 0:
        batch.status, batch.finished_at = "done", now()
    session.flush()
    return batch


def batch_progress(session: Session, batch: Optional[Batch]) -> Optional[dict]:
    if batch is None:
        return None
    rows = session.execute(text("""
        SELECT status, progress->>'outcome' AS outcome, COUNT(*) FROM jobs
        WHERE batch_id = :b GROUP BY status, outcome"""), {"b": batch.id}).all()
    by_status: dict = {}
    outcomes: dict = {}
    for status, outcome, n in rows:
        by_status[status] = by_status.get(status, 0) + n
        if outcome:
            outcomes[outcome] = outcomes.get(outcome, 0) + n
    finished = sum(by_status.get(s, 0) for s in ("done", "failed", "cancelled"))
    waiting = session.execute(text("""
        SELECT waiting_reason FROM jobs WHERE batch_id = :b AND status = 'queued'
          AND waiting_reason IS NOT NULL ORDER BY id LIMIT 1"""), {"b": batch.id}).scalar()
    current = session.execute(text("""
        SELECT progress->>'current' FROM jobs WHERE batch_id = :b AND status = 'running'
        ORDER BY locked_at DESC LIMIT 1"""), {"b": batch.id}).scalar()
    status = batch.status
    if status == "running" and by_status.get("cancelling"):
        status = "cancelling"
    return {
        "id": batch.id, "kind": batch.kind, "label": batch.label, "status": status,
        "created_by": batch.created_by, "payload": batch.payload,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "progress": {"total": batch.total, "done": finished, "queued": by_status.get("queued", 0),
                     "running": by_status.get("running", 0) + by_status.get("cancelling", 0),
                     "failed": by_status.get("failed", 0), "cancelled": by_status.get("cancelled", 0),
                     "current": current, **outcomes},
        "waiting_reason": waiting,
    }


def cancel_batch(session: Session, batch: Batch) -> None:
    session.execute(text("""
        UPDATE jobs SET status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE 'cancelling' END,
                        finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END
        WHERE batch_id = :b AND status IN ('queued', 'running')"""), {"b": batch.id})
    batch.status = "cancelled"
    batch.finished_at = now()


def finish_batches(session: Session) -> None:
    session.execute(text("""
        UPDATE batches b SET status = 'done', finished_at = now()
        WHERE b.status = 'running' AND NOT EXISTS (
            SELECT 1 FROM jobs j WHERE j.batch_id = b.id AND j.status IN ('queued','running','cancelling'))"""))


# ---------------------------------------------------------------- claiming

def claim(session: Session, queues: Iterable[str], worker_id: str) -> Optional[Job]:
    """Take the next runnable job from the given queues, honouring pause and caps."""
    queues = list(queues)
    if not queues:
        return None
    configs = session.execute(text("""
        SELECT q.name, q.max_running FROM queues q
        WHERE q.name = ANY(:qs) AND q.enabled
          AND EXISTS (SELECT 1 FROM jobs j WHERE j.queue = q.name AND j.status = 'queued' AND j.run_after <= now())
    """), {"qs": queues}).all()
    eligible = []
    for name, cap in configs:
        if cap is None:
            eligible.append(name)
            continue
        # Serialise claims for capped queues so the cap holds across all workers.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"dam-queue:{name}"})
        running = session.execute(text(
            "SELECT COUNT(*) FROM jobs WHERE queue = :q AND status IN ('running','cancelling')"), {"q": name}).scalar()
        if running < cap:
            eligible.append(name)
    if not eligible:
        session.commit()
        return None
    row = session.execute(text("""
        UPDATE jobs SET status = 'running', locked_by = :worker, locked_at = now(),
                        attempts = attempts + 1, waiting_reason = NULL
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'queued' AND queue = ANY(:queues) AND run_after <= now()
            ORDER BY priority DESC, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id
    """), {"worker": worker_id, "queues": eligible}).first()
    session.commit()
    return session.get(Job, row[0]) if row else None


def claim_more(session: Session, job: Job, limit: int) -> list:
    """Claim up to `limit` more queued jobs of the same kind and queue (for chunked work like
    embeddings, where one model call handles many items)."""
    if limit <= 0:
        return []
    rows = session.execute(text("""
        UPDATE jobs SET status = 'running', locked_by = :worker, locked_at = now(),
                        attempts = attempts + 1, waiting_reason = NULL
        WHERE id IN (
            SELECT id FROM jobs
            WHERE status = 'queued' AND kind = :kind AND queue = :queue AND run_after <= now()
            ORDER BY priority DESC, id
            FOR UPDATE SKIP LOCKED
            LIMIT :limit
        )
        RETURNING id
    """), {"worker": job.locked_by, "kind": job.kind, "queue": job.queue, "limit": limit}).all()
    session.commit()
    return [session.get(Job, r[0]) for r in rows]


# ---------------------------------------------------------------- running / finishing

def heartbeat(session: Session, job: Job, progress: Optional[dict] = None) -> bool:
    """Refresh the lock and progress; returns False if the job was asked to cancel."""
    session.refresh(job, ["status"])
    job.locked_at = now()
    if progress is not None:
        job.progress = dict(progress)
    session.commit()
    return job.status != "cancelling"


def touch_worker_jobs(session: Session, worker_id: str) -> None:
    session.execute(text("""
        UPDATE jobs SET locked_at = now()
        WHERE locked_by = :w AND status IN ('running', 'cancelling')"""), {"w": worker_id})


def complete(session: Session, job: Job, outcome: Optional[str] = None) -> None:
    job.status = "cancelled" if job.status == "cancelling" else "done"
    if outcome:
        job.progress = {**(job.progress or {}), "outcome": outcome}
    job.finished_at = now()
    job.locked_by = None
    session.commit()


def fail(session: Session, job: Job, error: str, retry: bool = True) -> None:
    job.error = error[:4000]
    job.locked_by = None
    if job.status == "cancelling":
        job.status, job.finished_at = "cancelled", now()
    elif retry and job.attempts < job.max_attempts:
        job.status = "queued"
        job.run_after = now() + timedelta(seconds=min(3600, 30 * 2 ** max(0, job.attempts - 1)))
    else:
        job.status = "failed"
        job.finished_at = now()
    session.commit()


def defer(session: Session, job: Job, reason: str, seconds: int = 60) -> None:
    """Put a job back without counting an attempt (e.g. waiting on the Mac)."""
    if job.status == "cancelling":
        job.status, job.finished_at, job.locked_by = "cancelled", now(), None
        session.commit()
        return
    job.status = "queued"
    job.attempts = max(0, job.attempts - 1)
    job.waiting_reason = reason
    job.run_after = now() + timedelta(seconds=seconds)
    job.locked_by = None
    session.commit()


def release(session: Session, worker_id: str) -> int:
    """Hand a stopping worker's running jobs back to the queue."""
    result = session.execute(text("""
        UPDATE jobs SET status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'queued' END,
                        locked_by = NULL, attempts = GREATEST(attempts - 1, 0)
        WHERE locked_by = :w AND status IN ('running', 'cancelling')"""), {"w": worker_id})
    session.commit()
    return result.rowcount or 0


def requeue_stale(session: Session) -> int:
    """Return jobs whose worker stopped heartbeating (per-queue stall timeout) to the queue,
    mark silent workers offline, and close finished batches."""
    result = session.execute(text("""
        UPDATE jobs j SET status = CASE WHEN j.status = 'cancelling' THEN 'cancelled' ELSE 'queued' END,
                          locked_by = NULL
        FROM queues q
        WHERE q.name = j.queue AND j.status IN ('running', 'cancelling')
          AND j.locked_at < now() - make_interval(secs => q.stall_seconds)"""))
    session.execute(text("""
        UPDATE workers SET status = 'offline', running = 0
        WHERE status IN ('running', 'draining') AND last_seen < now() - interval '5 minutes'"""))
    finish_batches(session)
    session.commit()
    return result.rowcount or 0
