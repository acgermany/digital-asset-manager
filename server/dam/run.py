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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # status polls every few seconds
    wait_for_db()

    from sqlalchemy import text

    from .core.mounts import mount_all
    from .db import SessionLocal
    with SessionLocal() as session:
        mount_all(session)
        # Every worker lives in this container, so anything still "running" was
        # interrupted by the restart: resume it now rather than after the stale timeout.
        resumed = session.execute(text("""
            UPDATE jobs SET status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE 'queued' END,
                            locked_by = NULL
            WHERE status IN ('running', 'cancelling')""")).rowcount
        session.commit()
        if resumed:
            log.info("resumed %d interrupted job(s)", resumed)

    workers = int(os.environ.get("WORKERS", "2"))
    commands = {
        "api": [sys.executable, "-m", "uvicorn", "dam.api.app:app", "--host", "0.0.0.0", "--port", "8000"],
        "watcher": [sys.executable, "-m", "dam.watcher"],
        "ai-worker": [sys.executable, "-m", "dam.ai_worker"],
        "vision-worker": [sys.executable, "-m", "dam.ai_worker", "--vision"],
        "speech-worker": [sys.executable, "-m", "dam.ai_worker", "--speech"],
        **{f"worker-{i}": [sys.executable, "-m", "dam.worker"] for i in range(workers)},
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
