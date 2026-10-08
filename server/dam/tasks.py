"""Job handlers: one function per job kind, each handling a single item.

A handler returns an outcome string (shown in batch progress) or raises:
  Defer      -> retry later without using an attempt (the Mac is asleep, library unmounted)
  Permanent  -> fail now, don't retry (file gone, unsupported)
Anything else counts as a failed attempt and is retried with backoff.
"""
import logging
import os
import threading
import time
from typing import Optional

from . import queue
from .config import settings as env
from .core import ai, catalog, describe, embed, media, transcribe
from .core.ingest import Deferred, IngestError, process
from .models import Asset, Ingest, Job

log = logging.getLogger("dam.tasks")


class Defer(Exception):
    def __init__(self, reason: str, seconds: int = 60):
        super().__init__(reason)
        self.seconds = seconds


class Permanent(Exception):
    pass


# Probing the Mac for every job would hammer it; remember the answer briefly per process.
_probe_cache: dict = {}
_probe_lock = threading.Lock()
PROBE_TTL = 15


def _cached(key: str, fn):
    with _probe_lock:
        hit = _probe_cache.get(key)
        if hit and time.monotonic() - hit[0] < PROBE_TTL:
            return hit[1]
    value = fn()
    with _probe_lock:
        _probe_cache[key] = (time.monotonic(), value)
    return value


def _asset(session, job: Job) -> Asset:
    asset = session.get(Asset, job.asset_id) if job.asset_id else None
    if asset is None:
        raise Permanent("Asset no longer exists")
    return asset


# ---------------------------------------------------------------- ingest / scan

def run_ingest(session, job: Job, cfg: dict) -> str:
    ingest = session.get(Ingest, job.ingest_id)
    if ingest is None:
        return "gone"
    try:
        process(session, ingest, cfg)
    except Deferred as e:
        session.rollback()
        ingest = session.get(Ingest, job.ingest_id)
        ingest.status, ingest.error = "queued", str(e)
        session.commit()
        raise Defer(str(e), 30)
    except Exception as e:
        session.rollback()
        ingest = session.get(Ingest, job.ingest_id)
        final = isinstance(e, IngestError) or job.attempts >= job.max_attempts
        ingest.error = str(e)
        ingest.status = "failed" if final else "queued"
        session.commit()
        if final:
            raise Permanent(str(e)) from e
        raise
    session.refresh(ingest)
    return ingest.status


def run_catalog_scan(session, job: Job, cfg: dict) -> str:
    state = catalog.scan(session, job.payload["path"], int(job.payload.get("workers", 2)),
                         lambda progress: queue.heartbeat(session, job, progress))
    return state.get("phase", "done")


# ---------------------------------------------------------------- render (thumbnails + proxies)

def run_render(session, job: Job, cfg: dict) -> str:
    asset = _asset(session, job)
    path = next((f.path for f in sorted(asset.files, key=lambda f: (f.source != "library", f.id))
                 if os.path.isfile(f.path)), None)
    if not path:
        raise Defer("No copy of this file is reachable (drive or share not mounted?)", 300)
    vtype = media.view_type(path)
    made = []
    queue.heartbeat(session, job, {"current": os.path.basename(path), "step": "thumbnail"})
    if vtype == "image" and media.display_image(path, asset.sha256):
        made.append("display")
    if media.thumbnail(path, asset.sha256, vtype):
        made.append("thumbnail")
    if vtype == "video" and not media.browser_playable(path, media.probe(path)):
        def report(fraction: float) -> bool:
            return queue.heartbeat(session, job, {"current": os.path.basename(path), "step": "proxy",
                                                  "fraction": round(fraction, 3)})
        if media.make_proxy(path, asset.sha256, report):
            made.append("proxy")
    return "rendered" if made else "skipped"


# ---------------------------------------------------------------- Mac-bound work

def run_describe(session, job: Job, cfg: dict) -> str:
    status = _cached(f"vision:{cfg['ollama_url']}:{cfg['describe_model']}",
                     lambda: ai.probe(cfg["ollama_url"], cfg["describe_model"]))
    if not status["reachable"]:
        raise Defer(status["error"] or "Ollama is not reachable")
    if not status["installed"]:
        raise Defer(f"Model '{cfg['describe_model']}' is not installed on the Mac. "
                    f"Run: ollama pull {cfg['describe_model']}", 300)
    asset = _asset(session, job)
    queue.heartbeat(session, job, {"current": asset.files[0].filename if asset.files else None})
    try:
        ok = describe.describe_asset(session, cfg, asset)
    except ai.AIUnavailable as e:
        session.rollback()
        _probe_cache.clear()
        raise Defer(str(e))
    return "described" if ok else "unreadable"


def run_transcribe(session, job: Job, cfg: dict) -> str:
    status = _cached(f"speech:{cfg['transcribe_url']}", lambda: transcribe.server_status(cfg))
    if not status["reachable"]:
        raise Defer(status["error"] or "Transcription server not reachable")
    asset = _asset(session, job)
    queue.heartbeat(session, job, {"current": asset.files[0].filename if asset.files else None})
    try:
        outcome = transcribe.transcribe_asset(session, cfg, asset)
    except ai.AIUnavailable as e:
        session.rollback()
        _probe_cache.clear()
        raise Defer(str(e))
    return {"failed": "unreadable"}.get(outcome, outcome)


def run_embed(session, job: Job, cfg: dict, extra: Optional[list] = None) -> str:
    """Embeds this job's asset plus `extra` jobs claimed alongside it (one model call)."""
    jobs = [job] + (extra or [])
    state = ai.refresh(session, cfg)
    if state.state != "ready":
        if not (state.reachable and state.model_installed):
            raise Defer(state.last_error or "Waiting for the embedding model on the Mac")
        state = ai.warm(session, cfg)
        if state.state != "ready":
            raise Defer(state.last_error or "The embedding model did not load")
    ids = [j.asset_id for j in jobs if j.asset_id]
    try:
        embed.embed_assets(session, cfg, ids)
    except ai.AIUnavailable as e:
        session.rollback()
        if "dimensional" in str(e):
            raise Permanent(str(e))
        raise Defer(str(e))
    return "embedded"


HANDLERS = {
    "ingest": run_ingest,
    "catalog_scan": run_catalog_scan,
    "render": run_render,
    "describe": run_describe,
    "transcribe": run_transcribe,
    "embed": run_embed,
}

# Kinds whose jobs are processed in chunks (claim this many extra alongside the first).
CHUNKED = {"embed": max(0, env.embed_batch_size - 1)}
