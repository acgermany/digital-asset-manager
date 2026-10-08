"""Catalog scan: hash files in place (read-only) and record them as File rows."""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..models import Asset, File
from .files import is_ignored, kind_for, mime_for
from .hashing import sha256_file

log = logging.getLogger(__name__)
BATCH = 100


def _discover(root: str, should_stop: Callable[[], bool], on_count: Callable[[int], None]) -> list:
    found: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        if should_stop():
            break
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "#", "$", "@"))]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if not is_ignored(rel):
                found.append(os.path.join(dirpath, name))
        on_count(len(found))
    return found


def _upsert(session: Session, rows: list) -> None:
    now = datetime.now(timezone.utc)
    for r in rows:
        asset = session.query(Asset).filter_by(sha256=r["sha256"]).one_or_none()
        if asset is None:
            asset = Asset(sha256=r["sha256"], filesize=r["filesize"], kind=kind_for(r["filename"]),
                          mime_type=mime_for(r["filename"]))
            session.add(asset)
            session.flush()
        f = session.query(File).filter_by(path=r["path"]).one_or_none()
        if f is None:
            session.add(File(asset_id=asset.id, path=r["path"], filename=r["filename"],
                             filesize=r["filesize"], sha256=r["sha256"], source="catalog"))
        else:
            f.asset_id, f.sha256, f.filesize, f.scanned_at = asset.id, r["sha256"], r["filesize"], now
    session.commit()


def scan(session: Session, root: str, workers: int,
         report: Callable[[dict], bool]) -> dict:
    """Scan `root`. `report(progress)` returns False to cancel."""
    state = {"phase": "discovering", "total": 0, "progress": 0, "errors": 0, "skipped": 0,
             "current_file": "", "scan_path": root}
    cancelled = {"v": False}

    def stop() -> bool:
        return cancelled["v"]

    def on_count(n: int) -> None:
        state["total"] = n
        if n % 500 == 0 and not report(state):
            cancelled["v"] = True

    files = _discover(root, stop, on_count)
    state["total"] = len(files)
    if cancelled["v"] or not report(state):
        return state

    known = dict(session.query(File.path, File.filesize).filter(File.path.like(root.rstrip("/") + "/%")).all())
    todo = []
    for path in files:
        try:
            size = os.path.getsize(path)
        except OSError:
            state["errors"] += 1
            continue
        if known.get(path) == size:
            state["skipped"] += 1
        else:
            todo.append(path)
    state["phase"] = "scanning"
    state["progress"] = state["skipped"]

    def work(path: str):
        if cancelled["v"]:
            return None
        try:
            sha = sha256_file(path, should_stop=stop)
            if sha is None:
                return None
            return {"path": path, "filename": os.path.basename(path),
                    "filesize": os.path.getsize(path), "sha256": sha}
        except OSError:
            return None

    batch: list = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, p): p for p in todo}
        for fut in as_completed(futures):
            result = fut.result()
            state["progress"] += 1
            state["current_file"] = futures[fut]
            if result:
                batch.append(result)
            elif not cancelled["v"]:
                state["errors"] += 1
            if len(batch) >= BATCH:
                _upsert(session, batch)
                batch.clear()
            if state["progress"] % 25 == 0 and not report(state):
                cancelled["v"] = True
                for f in futures:
                    f.cancel()
                break
    if batch:
        _upsert(session, batch)
    state["phase"] = "done" if not cancelled["v"] else "cancelled"
    state["current_file"] = ""
    report(state)
    return state
