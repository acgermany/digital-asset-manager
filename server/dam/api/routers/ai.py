"""Semantic search controls, embedding batches and search."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ... import queue
from ...core import ai, embed, events, search as search_ops, settings_store
from ...db import get_session
from ...models import Asset, Job
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
    job = session.query(Job).filter_by(kind="embed_batch").order_by(Job.id.desc()).first()
    return {
        **ai.as_dict(state, cfg),
        "coverage": embed.coverage(session, cfg["embed_model"]),
        "batch": _job_dict(job),
    }


def _job_dict(job: Optional[Job]) -> Optional[dict]:
    if job is None:
        return None
    return {"id": job.id, "status": job.status, "progress": job.progress or {},
            "waiting_reason": job.waiting_reason, "error": job.error,
            "reembed": bool((job.payload or {}).get("reembed")),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None}


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
    active = session.query(Job).filter(Job.kind == "embed_batch",
                                       Job.status.in_(("queued", "running", "cancelling"))).first()
    if active:
        raise HTTPException(400, "An embedding batch is already running")
    queue.enqueue(session, "embed_batch", queue="ai", max_attempts=50,
                  payload={"reembed": body.reembed, "limit": body.limit})
    events.log(session, "embed_batch_started", actor="user", reembed=body.reembed)
    session.commit()
    return _status(session)


@router.post("/ai/embed/cancel")
def cancel_batch(session: Session = Depends(get_session)):
    job = session.query(Job).filter(Job.kind == "embed_batch",
                                    Job.status.in_(("queued", "running"))).first()
    if not job:
        raise HTTPException(400, "No embedding batch is running")
    job.status = "cancelled" if job.status == "queued" else "cancelling"
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
