import os

import pytest
from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core import ai, embed
from dam.core.ingest import process, register

client = TestClient(app)


def fake_embed(cfg, texts, prefix="", timeout=0):
    """Deterministic 768-dim vectors: words 'beach'/'tax' push different axes."""
    out = []
    for t in texts:
        v = [0.0] * 768
        t = t.lower()
        v[0] = 1.0 if "beach" in t or "ocean" in t or "sea" in t else 0.0
        v[1] = 1.0 if "tax" in t or "1099" in t or "irs" in t else 0.0
        v[2] = 0.1
        out.append(v)
    return out


@pytest.fixture
def library(session, dirs, monkeypatch):
    hot, lib = dirs
    monkeypatch.setattr(ai, "embed", fake_embed)
    files = {"Family/beach day.jpg": b"a", "Taxes/1099-int.txt": b"Internal Revenue Service Form 1099-INT"}
    for rel, data in files.items():
        p = os.path.join(hot, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        st = os.stat(p)
        i = register(session, hot, p, st.st_size, st.st_mtime)
        session.commit()
        process(session, i, {"hotfolder": hot, "library": lib, "delete_mode": "delete"})
    return hot, lib


def test_batch_embeds_valuable_first_and_is_idempotent(session, library):
    cfg = {"embed_model": "test-model", "embed_sensitive_text": False}
    order = embed.pending_ids(session, "test-model")
    tiers = [session.get(__import__("dam.models", fromlist=["Asset"]).Asset, i).value_tier for i in order]
    assert tiers[0] == "critical"
    assert embed.embed_assets(session, cfg, order) == 2
    assert embed.coverage(session, "test-model")["pending"] == 0
    assert embed.embed_assets(session, cfg, order) == 0  # text unchanged: skipped


def test_financial_terms_not_embedded_by_default(session, library):
    from dam.models import Asset
    critical = session.query(Asset).filter_by(value_tier="critical").one()
    text_ = embed.source_text(session, critical, {"embed_sensitive_text": False})
    assert "terms:" not in text_ and "forms:" in text_


def test_search_keyword_and_semantic(session, library):
    from dam.core import settings_store
    settings_store.update(session, {"embed_model": "test-model"})
    session.commit()
    cfg = settings_store.all_settings(session, fresh=True)
    embed.embed_assets(session, cfg, embed.pending_ids(session, cfg["embed_model"]))

    r = client.get("/api/search", params={"q": "beach", "mode": "keyword"}).json()
    assert r["mode"] == "keyword" and r["results"][0]["filename"] == "beach day.jpg"

    state = ai.get_state(session)
    state.state, state.semantic_enabled = "ready", True
    session.commit()
    r = client.get("/api/search", params={"q": "ocean"}).json()   # no keyword match
    assert r["mode"] == "hybrid" and r["results"][0]["filename"] == "beach day.jpg"
    assert r["results"][0]["score"] > 0.9


def test_search_falls_back_to_keyword_while_loading(session, library):
    state = ai.get_state(session)
    state.state, state.semantic_enabled = "loading", True
    session.commit()
    r = client.get("/api/search", params={"q": "beach"}).json()
    assert r["mode"] == "keyword" and "loading" in r["notice"]
