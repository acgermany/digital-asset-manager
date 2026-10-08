"""2-D layout of asset embeddings for the similarity map.

Similar content lands close together. PCA reduces to 50 dims, then t-SNE lays
the points out. Results are cached until the set of embeddings changes.
"""
import hashlib
import json
import threading
from typing import Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

MAX_POINTS = 3000          # t-SNE cost grows fast; sample beyond this (valuable first)
_cache: dict = {}
_lock = threading.Lock()


def _signature(session: Session, model: str) -> str:
    row = session.execute(text(
        "SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(created_at)::text, '') "
        "FROM embeddings WHERE model = :m"), {"m": model}).one()
    return hashlib.sha1(f"{model}|{row[0]}|{row[1]}|{row[2]}".encode()).hexdigest()


def _layout(vectors: np.ndarray) -> np.ndarray:
    n = len(vectors)
    if n == 1:
        return np.zeros((1, 2))
    if n < 5:
        # Too few for t-SNE; a PCA plane is honest enough.
        centered = vectors - vectors.mean(axis=0)
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        return centered @ vt[:2].T
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE
    reduced = PCA(n_components=min(50, n - 1, vectors.shape[1]), random_state=0).fit_transform(vectors)
    perplexity = max(2.0, min(30.0, (n - 1) / 3))
    return TSNE(n_components=2, perplexity=perplexity, init="pca", random_state=0,
                metric="cosine", max_iter=750).fit_transform(reduced)


def similarity_map(session: Session, model: str) -> Optional[dict]:
    sig = _signature(session, model)
    with _lock:
        if _cache.get("sig") == sig:
            return _cache["data"]
        rows = session.execute(text("""
            SELECT a.id, e.embedding::text, a.kind, a.value_tier,
                   (SELECT f.id FROM files f WHERE f.asset_id = a.id
                     ORDER BY (f.source <> 'library'), f.id LIMIT 1) AS file_id,
                   (SELECT f.filename FROM files f WHERE f.asset_id = a.id
                     ORDER BY (f.source <> 'library'), f.id LIMIT 1) AS filename,
                   a.filesize
            FROM embeddings e JOIN assets a ON a.id = e.asset_id
            WHERE e.model = :m
            ORDER BY CASE a.value_tier WHEN 'critical' THEN 0 WHEN 'precious' THEN 1 ELSE 2 END, a.id DESC
            LIMIT :limit
        """), {"m": model, "limit": MAX_POINTS}).all()
        rows = [r for r in rows if r.file_id is not None]
        if not rows:
            data = None
        else:
            vectors = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
            xy = _layout(vectors)
            span = np.ptp(xy, axis=0)
            span[span == 0] = 1
            xy = (xy - xy.min(axis=0)) / span  # 0..1
            total = session.execute(text("SELECT COUNT(*) FROM embeddings WHERE model = :m"), {"m": model}).scalar()
            data = {
                "model": model, "total": total, "shown": len(rows),
                "points": [{"asset_id": r.id, "file_id": r.file_id, "filename": r.filename, "kind": r.kind,
                            "tier": r.value_tier, "filesize": r.filesize,
                            "x": round(float(x), 4), "y": round(float(y), 4)}
                           for r, (x, y) in zip(rows, xy)],
            }
        _cache.update(sig=sig, data=data)
        return data
