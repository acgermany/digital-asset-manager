"""Keyword, semantic and hybrid search over assets."""
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings as env
from . import ai

RRF_K = 60  # reciprocal-rank-fusion constant


def _filters(kind: Optional[str], tier: Optional[str]) -> tuple:
    clauses, params = [], {}
    if kind:
        clauses.append("a.kind = :kind")
        params["kind"] = kind
    if tier:
        clauses.append("a.value_tier = :tier")
        params["tier"] = tier
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def keyword(session: Session, q: str, limit: int, kind=None, tier=None) -> list:
    where, params = _filters(kind, tier)
    rows = session.execute(text(f"""
        SELECT a.id, GREATEST(similarity(coalesce(a.search_text, ''), :q),
                              CASE WHEN a.search_text ILIKE :like THEN 0.5 ELSE 0 END) AS score
        FROM assets a
        WHERE (a.search_text ILIKE :like OR similarity(coalesce(a.search_text, ''), :q) > 0.2
               OR EXISTS (SELECT 1 FROM files f WHERE f.asset_id = a.id AND f.path ILIKE :like)) {where}
        ORDER BY score DESC, a.id DESC
        LIMIT :limit
    """), {"q": q, "like": f"%{q}%", "limit": limit, **params}).all()
    return [(r[0], float(r[1])) for r in rows]


def semantic(session: Session, cfg: dict, q: str, limit: int, kind=None, tier=None) -> list:
    vec = ai.embed(cfg, [q], prefix=env.embed_query_prefix, timeout=30, priority=ai.PRIORITY_INTERACTIVE)[0]
    where, params = _filters(kind, tier)
    rows = session.execute(text(f"""
        SELECT a.id, 1 - (e.embedding <=> CAST(:vec AS vector)) AS similarity
        FROM embeddings e JOIN assets a ON a.id = e.asset_id
        WHERE e.model = :model {where}
        ORDER BY e.embedding <=> CAST(:vec AS vector)
        LIMIT :limit
    """), {"vec": str(vec), "model": cfg["embed_model"], "limit": limit, **params}).all()
    return [(r[0], float(r[1])) for r in rows if float(r[1]) >= env.semantic_min_score]


def hybrid(keyword_hits: list, semantic_hits: list, limit: int) -> list:
    """Reciprocal rank fusion; keeps the semantic similarity for display."""
    scores: dict = {}
    sim = dict(semantic_hits)
    for hits in (keyword_hits, semantic_hits):
        for rank, (asset_id, _) in enumerate(hits):
            scores[asset_id] = scores.get(asset_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    ranked = sorted(scores, key=lambda a: -scores[a])[:limit]
    return [(a, sim.get(a)) for a in ranked]
