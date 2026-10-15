"""Container entrypoint: API + watcher + workers in one mount namespace.

They share one container so network shares mounted at runtime (Configure page)
are visible to every process.
"""
import logging
import os
import signal
import subprocess
import sys
import time

from sqlalchemy.exc import OperationalError

log = logging.getLogger("dam.run")


def wait_for_db() -> None:
    from .db import init_db
    for attempt in range(60):
        try:
            init_db()
            return
        except OperationalError:
            log.info("waiting for database (%s)", attempt + 1)
            time.sleep(2)
    raise SystemExit("database never became available")


def embedded_worker_commands(environ) -> dict:
    """The worker pools inside the app container.

    The app container always runs the API, the hot-folder watcher and the coordinator,
    plus two embedded worker pools so a single-container install processes jobs on its own:
      core: ingest, scan, render (non-AI)  — EMBEDDED_CORE_CONCURRENCY slots
      ai:   embed, vision, speech          — EMBEDDED_AI_CONCURRENCY slots
    Separate pools mean AI jobs waiting on the Mac never take the slots a hot-folder drop needs.
    Scale out with more worker containers (docker compose up --scale worker=N).
    EMBEDDED_WORKER_CONCURRENCY=0 turns both off (all work left to worker containers).
    """
    from .worker import AI_QUEUES, CORE_QUEUES
    if environ.get("EMBEDDED_WORKER_CONCURRENCY", environ.get("WORKERS")) == "0":
        return {}
    pools = {
        "core": (int(environ.get("EMBEDDED_CORE_CONCURRENCY") or 3), CORE_QUEUES),
        "ai": (int(environ.get("EMBEDDED_AI_CONCURRENCY") or 2), AI_QUEUES),
    }
    return {f"worker-{pool}": [sys.executable, "-m", "dam.worker", "--embedded", "--name", f"app-{pool}",
                               "--concurrency", str(n), "--queues", ",".join(queues)]
            for pool, (n, queues) in pools.items() if n > 0}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # status polls every few seconds
    wait_for_db()

    from sqlalchemy import text

    from .core.mounts import mount_all
    from .db import SessionLocal
    with SessionLocal() as session:
        mount_all(session)
        # The previous app container's embedded worker died with it: hand its jobs back now
        # instead of waiting for the stall timeout. Jobs held by other workers are untouched.
        resumed = session.execute(text("""
            UPDATE jobs SET status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'queued' END,
                            locked_by = NULL
            WHERE status IN ('running', 'cancelling')
              AND (locked_by IS NULL
                   OR locked_by IN (SELECT id FROM workers WHERE embedded)
                   OR locked_by NOT IN (SELECT id FROM workers))""")).rowcount
        session.execute(text("UPDATE workers SET status = 'stopped', running = 0 WHERE embedded AND status <> 'stopped'"))
        session.commit()
        if resumed:
            log.info("resumed %d interrupted job(s)", resumed)

    commands = {
        "api": [sys.executable, "-m", "uvicorn", "dam.api.app:app", "--host", "0.0.0.0", "--port", "8000"],
        "watcher": [sys.executable, "-m", "dam.watcher"],
        "coordinator": [sys.executable, "-m", "dam.coordinator"],
        **embedded_worker_commands(os.environ),
    }
    procs = {name: subprocess.Popen(cmd) for name, cmd in commands.items()}
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for p in procs.values():
            p.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not stopping:
        for name, p in list(procs.items()):
            if p.poll() is not None and not stopping:
                log.warning("%s exited with %s; restarting", name, p.returncode)
                time.sleep(2)
                procs[name] = subprocess.Popen(commands[name])
        time.sleep(1)
    for p in procs.values():
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    main()
