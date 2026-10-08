"""Job worker: claims jobs from Postgres and runs them."""
import logging
import os
import socket
import time

from . import queue
from .config import settings as env
from .core import catalog, media, settings_store
from .core.ingest import Deferred, IngestError, process
from .db import SessionLocal
from .models import File, Ingest, Job

log = logging.getLogger("dam.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def run_ingest(session, job: Job) -> None:
    ingest = session.get(Ingest, job.ingest_id)
    if ingest is None:
        return
    cfg = settings_store.all_settings(session)
    try:
        process(session, ingest, cfg)
    except Exception as e:
        session.rollback()
        ingest = session.get(Ingest, job.ingest_id)
        if isinstance(e, Deferred):
            ingest.status, ingest.error = "queued", str(e)
            session.commit()
            raise
        final = isinstance(e, IngestError) or job.attempts >= job.max_attempts
        ingest.error = str(e)
        ingest.status = "failed" if final else "queued"
        session.commit()
        if final:
            raise IngestError(str(e)) from e
        raise


def run_catalog_scan(session, job: Job) -> None:
    def report(progress: dict) -> bool:
        return queue.heartbeat(session, job, progress)

    catalog.scan(session, job.payload["path"], int(job.payload.get("workers", 2)), report)


def run_proxy(session, job: Job) -> None:
    f = session.get(File, job.payload.get("file_id"))
    if f is None or not os.path.isfile(f.path):
        raise IngestError("Video file is not available")

    def report(fraction: float) -> bool:
        return queue.heartbeat(session, job, {"fraction": round(fraction, 3)})

    media.make_proxy(f.path, f.sha256 or str(f.id), report)


HANDLERS = {"ingest": run_ingest, "catalog_scan": run_catalog_scan, "proxy": run_proxy}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    queues = env.worker_queues
    log.info("worker %s on queues %s", WORKER_ID, ",".join(queues))
    last_stale_check = 0.0
    while True:
        session = SessionLocal()
        try:
            if time.monotonic() - last_stale_check > 60:
                queue.requeue_stale(session)
                last_stale_check = time.monotonic()
            job = queue.claim(session, queues, WORKER_ID)
            if job is None:
                time.sleep(1)
                continue
            handler = HANDLERS.get(job.kind)
            try:
                if handler is None:
                    raise IngestError(f"No handler for job kind {job.kind}")
                handler(session, job)
                queue.complete(session, job)
            except Deferred as e:
                session.rollback()
                queue.defer(session, session.get(Job, job.id), str(e), seconds=30)
            except IngestError as e:
                session.rollback()
                queue.fail(session, session.get(Job, job.id), str(e), retry=False)
            except Exception as e:  # noqa: BLE001
                log.exception("job %s failed", job.id)
                session.rollback()
                queue.fail(session, session.get(Job, job.id), f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001 — DB hiccup; back off
            log.exception("worker loop error")
            time.sleep(5)
        finally:
            session.close()


if __name__ == "__main__":
    main()
