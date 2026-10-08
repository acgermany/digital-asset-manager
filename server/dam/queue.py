"""Durable job queue on Postgres (FOR UPDATE SKIP LOCKED)."""
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import Job

STALE_AFTER = timedelta(minutes=10)


def now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(session: Session, kind: str, queue: str = "default", priority: int = 0,
            ingest_id: Optional[int] = None, asset_id: Optional[int] = None,
            payload: Optional[dict] = None, max_attempts: int = 3) -> Job:
    job = Job(kind=kind, queue=queue, priority=priority, ingest_id=ingest_id,
              asset_id=asset_id, payload=payload or {}, progress={},
              max_attempts=max_attempts)
    session.add(job)
    session.flush()
    return job


def claim(session: Session, queues: Iterable[str], worker_id: str) -> Optional[Job]:
    """Atomically take the next runnable job from the given queues."""
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
    """), {"worker": worker_id, "queues": list(queues)}).first()
    session.commit()
    if not row:
        return None
    return session.get(Job, row[0])


def heartbeat(session: Session, job: Job, progress: Optional[dict] = None) -> bool:
    """Refresh the lock; returns False if the job was asked to cancel."""
    session.refresh(job, ["status"])
    job.locked_at = now()
    if progress is not None:
        job.progress = dict(progress)
    session.commit()
    return job.status != "cancelling"


def complete(session: Session, job: Job) -> None:
    job.status = "cancelled" if job.status == "cancelling" else "done"
    job.finished_at = now()
    job.locked_by = None
    session.commit()


def fail(session: Session, job: Job, error: str, retry: bool = True) -> None:
    job.error = error[:4000]
    job.locked_by = None
    if retry and job.attempts < job.max_attempts:
        job.status = "queued"
        job.run_after = now() + timedelta(seconds=30 * 2 ** (job.attempts - 1))
    else:
        job.status = "failed"
        job.finished_at = now()
    session.commit()


def defer(session: Session, job: Job, reason: str, seconds: int = 60) -> None:
    """Put a job back without counting it as a failed attempt (e.g. waiting on the Mac)."""
    job.status = "queued"
    job.attempts = max(0, job.attempts - 1)
    job.waiting_reason = reason
    job.run_after = now() + timedelta(seconds=seconds)
    job.locked_by = None
    session.commit()


def requeue_stale(session: Session) -> int:
    """Return jobs whose worker died mid-run to the queue."""
    result = session.execute(text("""
        UPDATE jobs SET status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'queued' END,
                        locked_by = NULL
        WHERE status IN ('running', 'cancelling') AND locked_at < :cutoff
    """), {"cutoff": now() - STALE_AFTER})
    session.commit()
    return result.rowcount or 0
