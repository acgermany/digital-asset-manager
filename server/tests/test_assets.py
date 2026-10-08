import os

from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core import projection
from dam.core.ingest import process, register
from dam.models import Asset, Embedding

client = TestClient(app)


def ingest(session, hot, lib, rel, data):
    p = os.path.join(hot, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    st = os.stat(p)
    i = register(session, hot, p, st.st_size, st.st_mtime)
    session.commit()
    process(session, i, {"hotfolder": hot, "library": lib, "delete_mode": "delete"})
    return i


def test_detail_has_everything_and_tags_are_editable(session, dirs):
    hot, lib = dirs
    i = ingest(session, hot, lib, "Grandma/a.jpg", b"aaa")
    file_id = client.get("/api/files").json()["files"][0]["id"]
    d = client.get(f"/api/files/{file_id}/details").json()
    assert d["asset"]["sha256"] == i.sha256
    assert d["ingests"][0]["provenance"] == "Grandma" and d["ingests"][0]["ingest_date"]
    assert d["files"][0]["exists"] is True
    assert any(e["action"] == "ingest_accepted" for e in d["events"])

    r = client.patch(f"/api/assets/{i.asset_id}", json={"tags": ["Beach ", "beach", "2019 trip"]})
    assert r.json()["asset"]["tags"] == ["Beach", "2019 trip"]
    session.expire_all()
    assert "tags: Beach, 2019 trip" in session.get(Asset, i.asset_id).search_text
    assert client.get("/api/files", params={"tag": "Beach"}).json()["total"] == 1
    assert client.get("/api/tags").json()["tags"][0]["count"] == 1

    r = client.patch(f"/api/assets/{i.asset_id}", json={"value_tier": "critical"})
    assert r.json()["asset"]["protected"] is True


def test_overview_groups_by_provenance(session, dirs):
    hot, lib = dirs
    ingest(session, hot, lib, "Grandma/a.jpg", b"1")
    ingest(session, hot, lib, "Grandma/b.mp4", b"22")
    ingest(session, hot, lib, "Taxes/c.pdf", b"333")
    o = client.get("/api/library/overview").json()
    g = {x["group"]: x for x in o["groups"]}
    assert g["Grandma"]["count"] == 2 and g["Grandma"]["bytes"] == 3
    assert g["Taxes"]["kinds"]["document"]["count"] == 1
    assert sum(t["count"] for t in o["timeline"]) == 3


def test_map_lays_out_embedded_assets(session, dirs):
    hot, lib = dirs
    for n in range(8):
        ingest(session, hot, lib, f"P/f{n}.jpg", bytes([n]) * 10)
    for a in session.query(Asset).all():
        vec = [0.0] * 768
        vec[a.id % 3] = 1.0
        vec[5] = 0.01 * a.id
        session.add(Embedding(asset_id=a.id, model="nomic-embed-text", source_hash="x", embedding=vec))
    session.commit()
    projection._cache.clear()
    m = client.get("/api/library/map").json()
    assert m["shown"] == 8
    assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 for p in m["points"])
