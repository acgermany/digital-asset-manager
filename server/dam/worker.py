"""Queue consumer. Run as many as you like, on as many machines as you like:

    python -m dam.worker --queues ingest,render --concurrency 4 --name render-1

Each worker registers itself in `workers`, heartbeats every few seconds (which also
keeps its running jobs' locks fresh), and applies overrides set in the app
(queues, concurrency, drain). SIGTERM stops claiming and hands running jobs back.
"""
import argparse
import logging
import os
import signal
import socket
import threading
import time
import uuid

from . import queue
from .core import settings_store
from .db import SessionLocal
from .models import Job, Worker
from .tasks import CHUNKED, HANDLERS, Defer, Permanent

log = logging.getLogger("dam.worker")
ALL_QUEUES = ["ingest", "scan", "render", "embed", "vision", "speech"]
HEARTBEAT = 10
VERSION = "0.4"


class WorkerProcess:
    def __init__(self, name: str, queues: list, concurrency: int, embedded: bool = False):
        host = socket.gethostname()
        self.id = f"{host}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.name = name or f"{host}-{os.getpid()}"
        self.hostname = host
        self.queues = queues
        self.concurrency = max(0, concurrency)
        self.embedded = embedded
        self.stopping = threading.Event()
        self.draining = False
        self.lock = threading.Lock()
        self.running = 0
        self.processed = 0
        self.failed = 0
        self.slots: list = []

    # ------------------------------------------------------------ registry

    def register(self) -> None:
        with SessionLocal() as s:
            s.add(Worker(id=self.id, name=self.name, hostname=self.hostname, version=VERSION,
                         queues=self.queues, concurrency=self.concurrency, status="running",
                         embedded=self.embedded))
            s.commit()

    def heartbeat(self) -> None:
        """Report in, keep job locks fresh, pick up overrides from the app."""
        with SessionLocal() as s:
            w = s.get(Worker, self.id)
            if w is None:  # row deleted in the app: re-register
                self.register()
                return
            if w.desired_queues is not None:
                self.queues = [q for q in w.desired_queues if q in ALL_QUEUES]
            if w.desired_concurrency is not None:
                self.concurrency = max(0, min(32, w.desired_concurrency))
            self.draining = w.status == "draining"
            with self.lock:
                running, processed, failed = self.running, self.processed, self.failed
            w.queues, w.concurrency, w.running = self.queues, self.concurrency, running
            w.processed, w.failed = processed, failed
            w.last_seen = queue.now()
            if w.status == "offline":
                w.status = "running"
            queue.touch_worker_jobs(s, self.id)
            s.commit()

    # ------------------------------------------------------------ job execution

    def run_one(self, session) -> bool:
        if self.draining or self.stopping.is_set():
            return False
        job = queue.claim(session, self.queues, self.id)
        if job is None:
            return False
        with self.lock:
            self.running += 1
        extra = []
        try:
            cfg = settings_store.all_settings(session)
            handler = HANDLERS.get(job.kind)
            if handler is None:
                raise Permanent(f"No handler for job kind '{job.kind}'")
            if job.kind in CHUNKED:
                extra = queue.claim_more(session, job, CHUNKED[job.kind])
                outcome = handler(session, job, cfg, extra)
            else:
                outcome = handler(session, job, cfg)
            for j in [job] + extra:
                queue.complete(session, j, outcome)
            with self.lock:
                self.processed += 1 + len(extra)
        except Defer as e:
            session.rollback()
            for j in [job] + extra:
                queue.defer(session, session.get(Job, j.id), str(e), seconds=e.seconds)
        except Permanent as e:
            session.rollback()
            for j in [job] + extra:
                queue.fail(session, session.get(Job, j.id), str(e), retry=False)
            with self.lock:
                self.failed += 1 + len(extra)
        except Exception as e:  # noqa: BLE001
            log.exception("%s job %s failed", job.kind, job.id)
            session.rollback()
            for j in [job] + extra:
                queue.fail(session, session.get(Job, j.id), f"{type(e).__name__}: {e}")
            with self.lock:
                self.failed += 1 + len(extra)
        finally:
            with self.lock:
                self.running -= 1
        return True

    def slot_loop(self, index: int) -> None:
        while not self.stopping.is_set():
            if index >= self.concurrency:      # concurrency was lowered: this slot idles
                self.stopping.wait(2)
                continue
            session = SessionLocal()
            try:
                worked = self.run_one(session)
            except Exception:  # noqa: BLE001 — DB hiccup; back off
                log.exception("slot %d error", index)
                worked = False
                time.sleep(5)
            finally:
                session.close()
            if not worked:
                self.stopping.wait(1)

    def ensure_slots(self) -> None:
        while len(self.slots) < self.concurrency:
            t = threading.Thread(target=self.slot_loop, args=(len(self.slots),), daemon=True,
                                 name=f"slot-{len(self.slots)}")
            t.start()
            self.slots.append(t)

    # ------------------------------------------------------------ lifecycle

    def run(self) -> None:
        self.register()
        log.info("worker %s (%s) consuming %s with concurrency %d",
                 self.name, self.id, ",".join(self.queues) or "nothing", self.concurrency)
        last_maintenance = 0.0
        while not self.stopping.is_set():
            try:
                self.heartbeat()
                self.ensure_slots()
                if time.monotonic() - last_maintenance > 30:
                    with SessionLocal() as s:
                        n = queue.requeue_stale(s)
                        if n:
                            log.info("reclaimed %d stalled job(s)", n)
                    last_maintenance = time.monotonic()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")
            self.stopping.wait(HEARTBEAT)
        self.shutdown()

    def stop(self, *_) -> None:
        log.info("stopping: no new jobs")
        self.stopping.set()

    def shutdown(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with self.lock:
                if self.running == 0:
                    break
            time.sleep(0.5)
        with SessionLocal() as s:
            released = queue.release(s, self.id)
            w = s.get(Worker, self.id)
            if w:
                w.status, w.running, w.last_seen = "stopped", 0, queue.now()
            s.commit()
        if released:
            log.info("handed %d running job(s) back to the queue", released)


def main() -> None:
    parser = argparse.ArgumentParser(description="DAM queue worker")
    parser.add_argument("--queues", default=os.environ.get("WORKER_QUEUES", ",".join(ALL_QUEUES)))
    parser.add_argument("--concurrency", type=int, default=int(os.environ.get("WORKER_CONCURRENCY", "2")))
    parser.add_argument("--name", default=os.environ.get("WORKER_NAME", ""))
    parser.add_argument("--embedded", action="store_true", help="the worker inside the app container")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from .db import init_db
    for attempt in range(60):          # standalone workers may start before the database
        try:
            init_db()
            break
        except Exception:  # noqa: BLE001
            log.info("waiting for database (%d)", attempt + 1)
            time.sleep(2)

    if not args.embedded:
        # Same network shares as the app (definitions + credentials come from the shared /data).
        from .core.mounts import mount_all
        with SessionLocal() as s:
            mount_all(s)

    queues = [q.strip() for q in args.queues.split(",") if q.strip() in ALL_QUEUES]
    w = WorkerProcess(args.name, queues, args.concurrency, embedded=args.embedded)
    signal.signal(signal.SIGTERM, w.stop)
    signal.signal(signal.SIGINT, w.stop)
    w.run()


if __name__ == "__main__":
    main()
