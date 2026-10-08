"""Ollama client and model-availability state.

State machine kept in ai_state (single row):
    offline  -> Ollama unreachable
    unloaded -> reachable, embedding model not in memory
    loading  -> warm-up request in flight
    ready    -> model loaded (per /api/ps)
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..config import settings as env
from ..models import AIState

log = logging.getLogger(__name__)

STATUS_TIMEOUT = 4.0


class AIUnavailable(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _matches(name: str, model: str) -> bool:
    """`nomic-embed-text` matches `nomic-embed-text:latest`."""
    if name == model:
        return True
    if ":" not in model:
        return name == f"{model}:latest"
    return False


def get_state(session: Session) -> AIState:
    state = session.get(AIState, 1)
    if state is None:
        state = AIState(id=1, state="offline", reachable=False, model_installed=False,
                        model_loaded=False, semantic_enabled=False)
        session.add(state)
        session.flush()
    return state


def probe(url: str, model: str) -> dict:
    """Ask Ollama what is installed and what is loaded. Never raises."""
    try:
        tags = httpx.get(f"{url}/api/tags", timeout=STATUS_TIMEOUT)
        tags.raise_for_status()
        installed = any(_matches(m.get("name", ""), model) for m in tags.json().get("models", []))
        ps = httpx.get(f"{url}/api/ps", timeout=STATUS_TIMEOUT)
        ps.raise_for_status()
        loaded_until = None
        loaded = False
        for m in ps.json().get("models", []):
            if _matches(m.get("name", ""), model):
                loaded = True
                loaded_until = m.get("expires_at")
        return {"reachable": True, "installed": installed, "loaded": loaded,
                "loaded_until": loaded_until, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "installed": False, "loaded": False,
                "loaded_until": None, "error": f"Ollama not reachable at {url}: {e}"}


def refresh(session: Session, cfg: dict) -> AIState:
    """Update ai_state from a live probe. Keeps 'loading' until the model shows up or times out."""
    state = get_state(session)
    result = probe(cfg["ollama_url"], cfg["embed_model"])
    state.reachable = result["reachable"]
    state.model_installed = result["installed"]
    state.model_loaded = result["loaded"]
    state.last_checked = _now()
    if result["loaded_until"]:
        try:
            state.loaded_until = datetime.fromisoformat(result["loaded_until"].replace("Z", "+00:00"))
        except ValueError:
            state.loaded_until = None
    else:
        state.loaded_until = None

    if not result["reachable"]:
        state.state, state.last_error, state.loading_since = "offline", result["error"], None
    elif not result["installed"]:
        state.state = "unloaded"
        state.last_error = (f"Model '{cfg['embed_model']}' is not installed on the Mac. "
                            f"Run: ollama pull {cfg['embed_model']}")
        state.loading_since = None
    elif result["loaded"]:
        state.state, state.last_error, state.loading_since = "ready", None, None
    elif state.state == "loading" and state.loading_since and \
            (_now() - state.loading_since).total_seconds() < env.model_load_timeout:
        pass  # still warming up
    else:
        if state.state == "loading":
            state.last_error = "Model took too long to load"
        state.state, state.loading_since = "unloaded", None
    session.commit()
    return state


def embed(cfg: dict, texts: list, prefix: str = "", timeout: float = 120.0) -> list:
    """Embed texts with Ollama's /api/embed (batch input, keep_alive support)."""
    if not texts:
        return []
    try:
        r = httpx.post(f"{cfg['ollama_url']}/api/embed", timeout=timeout, json={
            "model": cfg["embed_model"],
            "input": [prefix + t for t in texts],
            "keep_alive": cfg.get("embed_keep_alive") or env.embed_keep_alive,
            "truncate": True,
        })
    except httpx.HTTPError as e:
        raise AIUnavailable(f"Ollama not reachable: {e}") from e
    if r.status_code != 200:
        raise AIUnavailable(f"Ollama error {r.status_code}: {r.text[:300]}")
    vectors = r.json().get("embeddings") or []
    if len(vectors) != len(texts):
        raise AIUnavailable("Ollama returned the wrong number of embeddings")
    if vectors and len(vectors[0]) != env.embed_dims:
        raise AIUnavailable(
            f"Model '{cfg['embed_model']}' returns {len(vectors[0])}-dimensional vectors but the "
            f"database is set up for {env.embed_dims}. Use a {env.embed_dims}-dim model "
            f"(e.g. nomic-embed-text) or change EMBED_DIMS and re-create the embeddings table.")
    return vectors


def warm(session: Session, cfg: dict) -> AIState:
    """Load the embedding model (blocks until Ollama has it in memory)."""
    state = get_state(session)
    state.state, state.loading_since, state.last_error = "loading", _now(), None
    session.commit()
    try:
        embed(cfg, ["warm up"], timeout=env.model_load_timeout)
    except AIUnavailable as e:
        state.last_error = str(e)
        state.state, state.loading_since = "unloaded", None
        session.commit()
        return state
    return refresh(session, cfg)


def unload(cfg: dict) -> None:
    try:
        httpx.post(f"{cfg['ollama_url']}/api/embed", timeout=STATUS_TIMEOUT,
                   json={"model": cfg["embed_model"], "input": [], "keep_alive": 0})
    except httpx.HTTPError as e:
        log.info("unload request failed: %s", e)


def as_dict(state: AIState, cfg: dict) -> dict:
    return {
        "state": state.state,
        "semantic_enabled": state.semantic_enabled,
        "reachable": state.reachable,
        "model": cfg["embed_model"],
        "model_installed": state.model_installed,
        "model_loaded": state.model_loaded,
        "loading_since": state.loading_since.isoformat() if state.loading_since else None,
        "loaded_until": state.loaded_until.isoformat() if state.loaded_until else None,
        "last_checked": state.last_checked.isoformat() if state.last_checked else None,
        "last_error": state.last_error,
    }
