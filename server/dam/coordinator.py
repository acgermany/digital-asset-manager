"""Coordinator: one per deployment, inside the app container. Does no heavy work.

- Tracks the Mac's embedding model (offline/unloaded/loading/ready); warms it when semantic
  search is switched on and honours Ollama's idle unload.
- Turns the "automatic" settings into per-item jobs that any worker can pick up:
  auto-embed (while semantic search is on), auto-describe, auto-transcribe.
- Reclaims stalled jobs and closes finished batches.
"""
import logging
import time

from sqlalchemy import text

from . import queue
from .core import ai, describe, embed, settings_store, transcribe
from .db import SessionLocal

log = logging.getLogger("dam.coordinator")
IDLE_POLL = 10
ACTIVE_POLL = 2
AUTO_CHUNK = 200   # enqueue at most this many auto jobs per pass; the rest follow next pass


def _live_jobs(session, kind: str) -> int:
    return session.execute(text(
        "SELECT COUNT(*) FROM jobs WHERE kind = :k AND status IN ('queued','running','cancelling')"),
        {"k": kind}).scalar()


def _auto(session, kind: str, queue_name: str, where_sql: str, params: dict) -> int:
    """Top up auto jobs for pending assets, keeping the backlog bounded."""
    if _live_jobs(session, kind) >= AUTO_CHUNK:
        return 0
    n = queue.enqueue_assets(session, kind, queue_name,
                             f"{where_sql} AND a.id IN (SELECT a.id FROM assets a WHERE {where_sql} LIMIT {AUTO_CHUNK})",
                             params, payload={"auto": True})
    session.commit()
    return n


def tick(session, prev_state):
    cfg = settings_store.all_settings(session, fresh=True)
    state = ai.refresh(session, cfg)
    if state.semantic_enabled:
        if state.state == "unloaded" and prev_state == "ready":
            # Ollama dropped the model after keep_alive idle time: honour that and free the Mac.
            state.semantic_enabled = False
            state.last_error = "Semantic search turned off after being idle (model unloaded on the Mac)"
            session.commit()
        elif state.state in ("unloaded", "loading") and state.reachable and state.model_installed:
            log.info("loading %s on the Mac", cfg["embed_model"])
            state = ai.warm(session, cfg)
        elif state.state == "ready":
            where, params = embed.pending_where(cfg["embed_model"])
            if _auto(session, "embed", "embed", where, params):
                log.info("queued auto-embed jobs")
    if cfg.get("describe_auto"):
        where, params = describe.pending_where(cfg)
        _auto(session, "describe", "vision", where, params)
    if cfg.get("transcribe_auto"):
        where, params = transcribe.pending_where(cfg)
        _auto(session, "transcribe", "speech", where, params)
    queue.requeue_stale(session)
    _expire_uploads(session)
    return state


def _expire_uploads(session) -> None:
    """Uploads with no progress for a day were abandoned: free the disk space."""
    import os
    rows = session.execute(text("""
        UPDATE uploads SET status = 'failed', error = 'Abandoned (no data for 24 hours)'
        WHERE status = 'uploading' AND updated_at < now() - interval '24 hours'
        RETURNING temp_path""")).all()
    for (path,) in rows:
        try:
            os.unlink(path)
        except OSError:
            pass
    session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    prev_state = None
    while True:
        session = SessionLocal()
        delay = IDLE_POLL
        try:
            state = tick(session, prev_state)
            prev_state = state.state
            if state.semantic_enabled or state.state == "loading":
                delay = ACTIVE_POLL
        except Exception:  # noqa: BLE001
            log.exception("coordinator pass failed")
            session.rollback()
            delay = 15
        finally:
            session.close()
        time.sleep(delay)


if __name__ == "__main__":
    main()
