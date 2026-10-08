"""Semantic search controls, embedding batches and search."""
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ... import queue
from ...core import ai, describe, embed, events, search as search_ops, settings_store, transcribe
from ...db import get_session
from ...models import Asset, Batch
from ..deps import mounts
from .library import _file_row

router = APIRouter()
STALE_SECONDS = 5


def _status(session: Session) -> dict:
    cfg = settings_store.all_settings(session)
    state = ai.get_state(session)
    age = (datetime.now(timezone.utc) - state.last_checked).total_seconds() if state.last_checked else None
    if age is None or age > STALE_SECONDS:
        state = ai.refresh(session, cfg)
    return {
        **ai.as_dict(state, cfg),
        "coverage": embed.coverage(session, cfg["embed_model"]),
        "batch": _latest_batch(session, "embed"),
    }


def _latest_batch(session: Session, kind: str) -> Optional[dict]:
    batch = session.query(Batch).filter_by(kind=kind).order_by(Batch.id.desc()).first()
    return queue.batch_progress(session, batch)


def _start_batch(session: Session, kind: str, queue_name: str, where: str, params: dict, label: str,
                 payload: Optional[dict] = None) -> None:
    running = session.query(Batch).filter_by(kind=kind, status="running").first()
    if running:
        raise HTTPException(400, f"A {kind} batch is already running")
    batch = queue.create_batch(session, kind, queue_name, where, params, label, payload=payload)
    events.log(session, f"{kind}_batch_started", actor="user", batch_id=batch.id, total=batch.total)
    session.commit()


def _cancel_batches(session: Session, kind: str) -> None:
    batches = session.query(Batch).filter_by(kind=kind, status="running").all()
    if not batches:
        raise HTTPException(400, "Nothing is running")
    for b in batches:
        queue.cancel_batch(session, b)


@router.get("/ai/status")
def status(session: Session = Depends(get_session)):
    return _status(session)


@router.post("/ai/semantic/enable")
def enable(session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    state = ai.refresh(session, cfg)
    if not state.reachable:
        raise HTTPException(400, state.last_error or "Ollama is not reachable")
    if not state.model_installed:
        raise HTTPException(400, state.last_error)
    state.semantic_enabled = True
    state.last_error = None
    if state.state != "ready":
        # The AI worker picks this up and loads the model; the UI shows "loading" meanwhile.
        state.state, state.loading_since = "loading", datetime.now(timezone.utc)
    events.log(session, "semantic_enabled", actor="user", model=cfg["embed_model"])
    session.commit()
    return _status(session)


@router.post("/ai/semantic/disable")
def disable(session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    state = ai.get_state(session)
    state.semantic_enabled = False
    state.loading_since = None
    session.commit()
    ai.unload(cfg)
    events.log(session, "semantic_disabled", actor="user")
    session.commit()
    ai.refresh(session, cfg)
    return _status(session)


class BatchBody(BaseModel):
    reembed: bool = False
    limit: Optional[int] = None


@router.post("/ai/embed")
def start_batch(body: BatchBody, session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    if body.reembed:
        where, params, label = "TRUE", {}, "Re-embed all"     # unchanged text is skipped per item
    else:
        (where, params), label = embed.pending_where(cfg["embed_model"]), "Embed pending"
    _start_batch(session, "embed", "embed", where, params, label, payload={"reembed": body.reembed})
    return _status(session)


@router.post("/ai/embed/cancel")
def cancel_batch(session: Session = Depends(get_session)):
    _cancel_batches(session, "embed")
    session.commit()
    return _status(session)


# ---------------------------------------------------------------- search

def _results(session: Session, hits: list) -> list:
    ms = mounts(session)
    out = []
    for asset_id, score in hits:
        asset = session.get(Asset, asset_id)
        if asset is None:
            continue
        files = sorted(asset.files, key=lambda f: (f.source != "library", f.id))
        if not files:
            continue
        row = _file_row(files[0], asset, ms)
        row["score"] = round(score, 4) if score is not None else None
        row["copies"] = len(files)
        out.append(row)
    return out


@router.get("/search")
def search(q: str, mode: str = "auto", kind: Optional[str] = None, tier: Optional[str] = None,
           limit: int = 50, session: Session = Depends(get_session)):
    q = q.strip()
    if not q:
        return {"mode": "keyword", "results": [], "notice": None}
    limit = max(1, min(limit, 200))
    cfg = settings_store.all_settings(session)
    state = ai.get_state(session)
    want_semantic = mode == "semantic" or (mode == "auto" and state.semantic_enabled)
    notice = None

    kw = search_ops.keyword(session, q, limit, kind, tier) if mode != "semantic" else []
    if want_semantic and state.state == "ready":
        try:
            sem = search_ops.semantic(session, cfg, q, limit, kind, tier)
            hits = sem if mode == "semantic" else search_ops.hybrid(kw, sem, limit)
            cov = embed.coverage(session, cfg["embed_model"])
            if cov["pending"]:
                notice = f"{cov['pending']} of {cov['total']} items are not embedded yet and only match by keyword."
            return {"mode": "semantic" if mode == "semantic" else "hybrid",
                    "results": _results(session, hits), "notice": notice}
        except ai.AIUnavailable as e:
            notice = f"Semantic search unavailable ({e}); showing keyword results."
    elif want_semantic:
        notice = ("The embedding model is still loading; showing keyword results."
                  if state.state == "loading" else
                  "Semantic search is off or the Mac is unavailable; showing keyword results.")
    if mode == "semantic" and not kw:
        kw = search_ops.keyword(session, q, limit, kind, tier)
    return {"mode": "keyword", "results": _results(session, kw), "notice": notice}


# ---------------------------------------------------------------- vision descriptions

def _describe_status(session: Session) -> dict:
    cfg = settings_store.all_settings(session)
    probe = ai.probe(cfg["ollama_url"], cfg["describe_model"])
    return {
        "model": cfg["describe_model"], "reachable": probe["reachable"],
        "model_installed": probe["installed"], "model_loaded": probe["loaded"],
        "error": probe["error"] or (None if probe["installed"] else
                                    f"Model '{cfg['describe_model']}' is not installed. Run: ollama pull {cfg['describe_model']}"),
        "auto": bool(cfg.get("describe_auto")),
        "coverage": describe.coverage(session, cfg),
        "batch": _latest_batch(session, "describe"),
    }


@router.get("/ai/describe/status")
def describe_status(session: Session = Depends(get_session)):
    return _describe_status(session)


class DescribeBody(BaseModel):
    redescribe: bool = False
    limit: Optional[int] = None


@router.post("/ai/describe")
def start_describe(body: DescribeBody, session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    before = datetime.now(timezone.utc).isoformat() if body.redescribe else None
    where, params = describe.pending_where(cfg, before)
    _start_batch(session, "describe", "vision", where, params,
                 "Re-describe all" if body.redescribe else "Describe pending")
    return _describe_status(session)


@router.post("/ai/describe/cancel")
def cancel_describe(session: Session = Depends(get_session)):
    _cancel_batches(session, "describe")
    settings_store.update(session, {"describe_auto": False})  # don't immediately refill
    session.commit()
    return _describe_status(session)


@router.post("/assets/{asset_id}/describe")
def describe_one(asset_id: int, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.kind not in ("image", "video"):
        raise HTTPException(400, "Only images and videos can be described")
    asset.description_error = None
    queue.enqueue(session, "describe", queue="vision", priority=100, asset_id=asset_id)
    session.commit()
    return {"status": "queued"}


@router.get("/ai/models")
def installed_models(session: Session = Depends(get_session)):
    """Models installed in Ollama, with what they can do (for the Configure dropdowns)."""
    cfg = settings_store.all_settings(session)
    try:
        r = httpx.get(f"{cfg['ollama_url']}/api/tags", timeout=5)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": str(e), "models": []}
    models = [{"name": m.get("name"), "capabilities": m.get("capabilities") or [],
               "size": m.get("size"), "parameters": (m.get("details") or {}).get("parameter_size")}
              for m in r.json().get("models", [])]
    return {"reachable": True, "error": None, "models": sorted(models, key=lambda m: m["name"])}


# ---------------------------------------------------------------- transcripts (Whisper)

def _transcribe_status(session: Session) -> dict:
    cfg = settings_store.all_settings(session)
    server = transcribe.server_status(cfg)
    return {"url": cfg["transcribe_url"], "api": cfg["transcribe_api"], "reachable": server["reachable"],
            "error": server["error"], "auto": bool(cfg.get("transcribe_auto")),
            "coverage": transcribe.coverage(session, cfg), "batch": _latest_batch(session, "transcribe")}


@router.get("/ai/transcribe/status")
def transcribe_status(session: Session = Depends(get_session)):
    return _transcribe_status(session)


class TranscribeBody(BaseModel):
    retranscribe: bool = False
    limit: Optional[int] = None


@router.post("/ai/transcribe")
def start_transcribe(body: TranscribeBody, session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    before = datetime.now(timezone.utc).isoformat() if body.retranscribe else None
    where, params = transcribe.pending_where(cfg, before)
    _start_batch(session, "transcribe", "speech", where, params,
                 "Re-transcribe all" if body.retranscribe else "Transcribe pending")
    return _transcribe_status(session)


@router.post("/ai/transcribe/cancel")
def cancel_transcribe(session: Session = Depends(get_session)):
    _cancel_batches(session, "transcribe")
    settings_store.update(session, {"transcribe_auto": False})
    session.commit()
    return _transcribe_status(session)


@router.post("/assets/{asset_id}/transcribe")
def transcribe_one(asset_id: int, session: Session = Depends(get_session)):
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.kind not in ("video", "audio"):
        raise HTTPException(400, "Only videos and audio can be transcribed")
    asset.transcript_error = None
    queue.enqueue(session, "transcribe", queue="speech", priority=100, asset_id=asset_id)
    session.commit()
    return {"status": "queued"}


@router.post("/ai/transcribe/test")
def test_transcribe(session: Session = Depends(get_session)):
    return transcribe.server_status(settings_store.all_settings(session, fresh=True))
