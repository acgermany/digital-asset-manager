"""AI worker: tracks the Mac's Ollama model, warms/unloads it, runs embedding batches.

Separate from the ingest workers so a slow or sleeping Mac never holds up ingest.
"""
import logging
import os
import socket
import time

from sqlalchemy import text

from . import queue
from .core import ai, describe, embed, settings_store
from .db import SessionLocal
from .models import Job

log = logging.getLogger("dam.ai_worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:ai"
IDLE_POLL = 10
ACTIVE_POLL = 2


def _ensure_ready(session, cfg) -> bool:
    state = ai.refresh(session, cfg)
    if state.state == "ready":
        return True
    if state.reachable and state.model_installed:
        state = ai.warm(session, cfg)
    return state.state == "ready"


def run_embed_job(session, job: Job, cfg: dict) -> None:
    if not _ensure_ready(session, cfg):
        state = ai.get_state(session)
        queue.defer(session, job, state.last_error or "Waiting for the embedding model on the Mac", seconds=60)
        return

    def report(progress: dict) -> bool:
        return queue.heartbeat(session, job, progress)

    try:
        embed.run_batch(session, cfg, report, limit=job.payload.get("limit"),
                        reembed=bool(job.payload.get("reembed")))
    except ai.AIUnavailable as e:
        session.rollback()
        job = session.get(Job, job.id)
        if "dimensional" in str(e):
            queue.fail(session, job, str(e), retry=False)
        else:
            queue.defer(session, job, f"Paused: {e}", seconds=60)  # resumes; done chunks are kept
        return
    queue.complete(session, job)


def run_describe_job(session, job: Job, cfg: dict) -> None:
    status = ai.probe(cfg["ollama_url"], cfg["describe_model"])
    if not status["reachable"] or not status["installed"]:
        reason = status["error"] or (f"Model '{cfg['describe_model']}' is not installed on the Mac. "
                                     f"Run: ollama pull {cfg['describe_model']}")
        queue.defer(session, job, reason, seconds=60)
        return

    def report(progress: dict) -> bool:
        return queue.heartbeat(session, job, progress)

    payload = job.payload or {}
    try:
        describe.run_batch(session, cfg, report, asset_ids=payload.get("asset_ids"),
                           redescribe_before=payload.get("redescribe_before"), limit=payload.get("limit"))
    except ai.AIUnavailable as e:
        session.rollback()
        queue.defer(session, session.get(Job, job.id), f"Paused: {e}", seconds=60)
        return
    queue.complete(session, job)


def auto_describe(session, cfg) -> None:
    """With auto-describe on, keep a describe batch going while anything is pending."""
    if not cfg.get("describe_auto"):
        return
    busy = session.execute(text(
        "SELECT 1 FROM jobs WHERE kind = 'describe_batch' AND status IN ('queued','running','cancelling') LIMIT 1")).first()
    if busy or not describe.pending_ids(session, cfg, limit=1):
        return
    queue.enqueue(session, "describe_batch", queue="vision", max_attempts=50, payload={"auto": True, "limit": 200})
    session.commit()


def auto_embed(session, cfg) -> None:
    """While semantic search is on and the model is loaded, embed new assets in the background."""
    busy = session.execute(text(
        "SELECT 1 FROM jobs WHERE kind = 'embed_batch' AND status IN ('queued','running') LIMIT 1")).first()
    if busy:
        return
    ids = embed.pending_ids(session, cfg["embed_model"], limit=8)
    if ids:
        try:
            n = embed.embed_assets(session, cfg, ids)
            if n:
                log.info("auto-embedded %d assets", n)
        except ai.AIUnavailable as e:
            session.rollback()
            log.warning("auto-embed paused: %s", e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # status polls every few seconds
    prev_state = None
    while True:
        session = SessionLocal()
        delay = IDLE_POLL
        try:
            cfg = settings_store.all_settings(session, fresh=True)
            state = ai.refresh(session, cfg)

            if state.semantic_enabled:
                delay = ACTIVE_POLL
                if state.state == "unloaded" and prev_state == "ready":
                    # Ollama dropped the model after keep_alive idle time: honour that and free the Mac.
                    state.semantic_enabled = False
                    state.last_error = "Semantic search turned off after being idle (model unloaded on the Mac)"
                    session.commit()
                elif state.state in ("unloaded", "loading") and state.reachable and state.model_installed:
                    log.info("loading %s on the Mac", cfg["embed_model"])
                    state = ai.warm(session, cfg)
                elif state.state == "ready":
                    auto_embed(session, cfg)
            prev_state = state.state

            job = queue.claim(session, ["ai"], WORKER_ID)
            if job is not None:
                delay = 0
                try:
                    run_embed_job(session, job, cfg)
                except Exception as e:  # noqa: BLE001
                    log.exception("%s job %s failed", job.kind, job.id)
                    session.rollback()
                    queue.fail(session, session.get(Job, job.id), f"{type(e).__name__}: {e}")
                prev_state = ai.get_state(session).state
        except Exception:  # noqa: BLE001
            log.exception("ai worker loop error")
            session.rollback()
            delay = 15
        finally:
            session.close()
        time.sleep(delay)


def vision_main() -> None:
    """Separate loop for vision descriptions: hours-long batches must not block
    the embedding model's warm-up or semantic search."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    worker_id = WORKER_ID.replace(":ai", ":vision")
    while True:
        session = SessionLocal()
        delay = IDLE_POLL
        try:
            cfg = settings_store.all_settings(session, fresh=True)
            auto_describe(session, cfg)
            job = queue.claim(session, ["vision"], worker_id)
            if job is not None:
                delay = 0
                try:
                    run_describe_job(session, job, cfg)
                except Exception as e:  # noqa: BLE001
                    log.exception("describe job %s failed", job.id)
                    session.rollback()
                    queue.fail(session, session.get(Job, job.id), f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            log.exception("vision worker loop error")
            session.rollback()
            delay = 15
        finally:
            session.close()
        time.sleep(delay)


if __name__ == "__main__":
    import sys
    vision_main() if "--vision" in sys.argv else main()
