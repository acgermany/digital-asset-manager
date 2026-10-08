"""Batch embedding of assets with the Mac's Ollama model."""
import hashlib
import logging
from typing import Callable, Optional

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import settings as env
from ..models import Asset, Embedding
from . import ai
from .assets import build_search_text

log = logging.getLogger(__name__)

# Most valuable content gets embedded first.
TIER_ORDER = "CASE a.value_tier WHEN 'critical' THEN 0 WHEN 'precious' THEN 1 " \
             "WHEN 'standard' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"


def source_text(session: Session, asset: Asset, cfg: dict) -> str:
    """What gets embedded. Financial documents contribute metadata only unless allowed."""
    if not asset.search_text:
        asset.search_text = build_search_text(asset, session)
    text_ = asset.search_text or ""
    if asset.value_tier == "critical" and not cfg.get("embed_sensitive_text"):
        text_ = " | ".join(p for p in text_.split(" | ") if not p.startswith("terms:"))
    return text_[:8000]


def text_hash(text_: str) -> str:
    return hashlib.sha256(text_.encode("utf-8")).hexdigest()


def pending_ids(session: Session, model: str, limit: Optional[int] = None) -> list:
    """Assets with no embedding for `model`, valuable first, then oldest."""
    sql = f"""
        SELECT a.id FROM assets a
        LEFT JOIN embeddings e ON e.asset_id = a.id AND e.model = :model
        WHERE e.id IS NULL
        ORDER BY {TIER_ORDER}, a.id
    """ + (" LIMIT :limit" if limit else "")
    params = {"model": model, **({"limit": limit} if limit else {})}
    return [r[0] for r in session.execute(text(sql), params)]


def coverage(session: Session, model: str) -> dict:
    total = session.execute(text("SELECT COUNT(*) FROM assets")).scalar()
    done = session.execute(text("SELECT COUNT(*) FROM embeddings WHERE model = :m"), {"m": model}).scalar()
    other = session.execute(text(
        "SELECT model, COUNT(*) FROM embeddings WHERE model <> :m GROUP BY model"), {"m": model}).all()
    return {"model": model, "total": total, "embedded": done, "pending": max(0, total - done),
            "other_models": {m: n for m, n in other}}


def embed_assets(session: Session, cfg: dict, asset_ids: list) -> int:
    """Embed one chunk and upsert. Unchanged text is skipped. Raises ai.AIUnavailable."""
    assets = session.query(Asset).filter(Asset.id.in_(asset_ids)).all()
    model = cfg["embed_model"]
    existing = {e.asset_id: e.source_hash for e in
                session.query(Embedding).filter(Embedding.asset_id.in_(asset_ids), Embedding.model == model)}
    todo = []
    for a in assets:
        t = source_text(session, a, cfg)
        h = text_hash(t)
        if existing.get(a.id) != h:
            todo.append((a.id, t, h))
    if not todo:
        session.commit()
        return 0
    vectors = ai.embed(cfg, [t for _, t, _ in todo], prefix=env.embed_doc_prefix)
    for (asset_id, _, h), vec in zip(todo, vectors):
        stmt = insert(Embedding).values(asset_id=asset_id, model=model, source_hash=h, embedding=vec)
        session.execute(stmt.on_conflict_do_update(
            constraint="uq_embedding_asset_model",
            set_={"source_hash": h, "embedding": vec, "created_at": text("now()")}))
    session.commit()
    return len(todo)


def run_batch(session: Session, cfg: dict, report: Callable[[dict], bool],
              limit: Optional[int] = None, reembed: bool = False) -> dict:
    """Embed everything pending (or everything, with reembed). Resumable: each chunk commits."""
    model = cfg["embed_model"]
    if reembed:
        ids = [r[0] for r in session.execute(text(f"SELECT a.id FROM assets a ORDER BY {TIER_ORDER}, a.id"))]
        if limit:
            ids = ids[:limit]
    else:
        ids = pending_ids(session, model, limit)
    progress = {"total": len(ids), "done": 0, "embedded": 0, "model": model}
    if not report(progress):
        return progress
    size = env.embed_batch_size
    for start in range(0, len(ids), size):
        chunk = ids[start:start + size]
        progress["embedded"] += embed_assets(session, cfg, chunk)
        progress["done"] += len(chunk)
        if not report(progress):
            break
    return progress
