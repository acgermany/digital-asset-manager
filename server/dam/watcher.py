"""Hot-folder watcher: registers files once their size/mtime stop changing.

Polling (not inotify) because the hot folder is written over SMB and may itself
be a network mount; a poll every couple of seconds is cheap.
"""
import logging
import os
import time

from .core import settings_store
from .core.files import is_ignored, is_sidecar
from .core.ingest import register, should_register
from .db import SessionLocal

log = logging.getLogger("dam.watcher")


def scan_once(session, hotfolder: str, seen: dict, stable_seconds: float) -> int:
    """One polling pass. `seen` maps path -> (size, mtime, first_seen_unchanged)."""
    now = time.monotonic()
    present = set()
    registered = 0
    for dirpath, dirnames, filenames in os.walk(hotfolder):
        rel_dir = os.path.relpath(dirpath, hotfolder)
        dirnames[:] = [d for d in dirnames
                       if not is_ignored(os.path.normpath(os.path.join(rel_dir, d)))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.normpath(os.path.join(rel_dir, name))
            if is_ignored(rel) or is_sidecar(path):
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            present.add(path)
            sig = (st.st_size, st.st_mtime)
            prev = seen.get(path)
            if prev is None or prev[:2] != sig:
                seen[path] = (*sig, now)
                continue
            if now - prev[2] < stable_seconds:
                continue
            if should_register(session, path, st.st_size, st.st_mtime):
                register(session, hotfolder, path, st.st_size, st.st_mtime)
                session.commit()
                registered += 1
                log.info("registered %s", path)
            seen[path] = (*sig, float("inf"))  # don't re-check until it changes
    for gone in set(seen) - present:
        del seen[gone]
    return registered


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    seen: dict = {}
    current = None
    while True:
        session = SessionLocal()
        try:
            cfg = settings_store.all_settings(session)
            hotfolder = cfg.get("hotfolder")
            if hotfolder != current:
                seen.clear()
                current = hotfolder
                log.info("watching %s", hotfolder or "(not configured)")
            if cfg.get("hotfolder_enabled") and hotfolder and os.path.isdir(hotfolder):
                scan_once(session, hotfolder, seen, float(cfg.get("stable_seconds") or 5))
        except Exception:  # noqa: BLE001 — keep watching
            log.exception("watch pass failed")
            session.rollback()
        finally:
            session.close()
        time.sleep(float(os.environ.get("POLL_INTERVAL", "2")))


if __name__ == "__main__":
    main()
