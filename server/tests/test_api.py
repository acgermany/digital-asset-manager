import os

from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core.ingest import process, register

client = TestClient(app)


def test_config_accepts_host_paths_and_marks_library(session, host_root):
    os.makedirs(os.path.join(host_root, "DAM", "Hot"), exist_ok=True)
    os.makedirs(os.path.join(host_root, "DAM", "Library"), exist_ok=True)
    r = client.put("/api/config", json={"hotfolder": "/Users/tester/DAM/Hot",
                                        "library": "/Users/tester/DAM/Library"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["hotfolder"] == "/Users/tester/DAM/Hot"
    assert body["status"]["library"]["marker"] is True
    assert os.path.isfile(os.path.join(host_root, "DAM", "Library", ".dam_library"))


def test_config_rejects_paths_outside_visible_locations(session):
    r = client.put("/api/config", json={"hotfolder": "/etc"})
    assert r.status_code == 400
    assert "not inside a location" in r.json()["detail"]


def test_config_rejects_nested_hotfolder_and_library(session, host_root):
    os.makedirs(os.path.join(host_root, "N", "lib"), exist_ok=True)
    r = client.put("/api/config", json={"hotfolder": "/Users/tester/N", "library": "/Users/tester/N/lib"})
    assert r.status_code == 400


def test_browse_root_and_mkdir(session, host_root):
    roots = client.get("/api/locations/browse").json()
    assert roots["dirs"][0]["host_path"] == "/Users/tester"
    r = client.post("/api/locations/mkdir", json={"path": "/Users/tester", "name": "NewFolder"})
    assert r.status_code == 200 and r.json()["host_path"] == "/Users/tester/NewFolder"


def test_mount_validation(session):
    r = client.post("/api/mounts", json={"name": "bad name!", "server": "nas", "share": "media"})
    assert r.status_code == 400


def test_resolve_review_keep_both(session, dirs):
    hot, lib = dirs
    client.put("/api/config", json={"hotfolder": hot, "library": lib})
    cfg = {"hotfolder": hot, "library": lib, "delete_mode": "delete"}
    for name in ("a.jpg", "b.jpg"):
        p = os.path.join(hot, name)
        with open(p, "wb") as f:
            f.write(b"same bytes")
        st = os.stat(p)
        i = register(session, hot, p, st.st_size, st.st_mtime)
        session.commit()
        process(session, i, cfg)
    reviews = client.get("/api/reviews").json()["reviews"]
    assert len(reviews) == 1 and reviews[0]["kind"] == "exact_name_differs"
    r = client.post(f"/api/reviews/{reviews[0]['id']}/resolve", json={"resolution": "keep_both"})
    assert r.status_code == 200, r.text
    dupes = client.get("/api/duplicates").json()
    assert dupes["total"] == 1 and dupes["groups"][0]["count"] == 2


def test_stats_and_scan_status_shape(session):
    s = client.get("/api/stats").json()
    assert {"total_files", "wasted_space", "tiers", "open_reviews"} <= set(s)
    assert client.get("/api/scan/status").json()["running"] is False
