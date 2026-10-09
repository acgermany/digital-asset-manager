import os

from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core.ingest import process
from dam.models import Ingest

client = TestClient(app)


def configure(dirs):
    hot, lib = dirs
    r = client.put("/api/config", json={"hotfolder": hot, "library": lib})
    assert r.status_code == 200, r.text
    return hot, lib


def test_chunked_upload_resume_and_ingest(session, dirs):
    hot, lib = configure(dirs)
    data = os.urandom(300_000)
    r = client.post("/api/uploads", json={"filename": "IMG_1.jpg", "size": len(data), "provenance": "Mom's iPhone",
                                         "relative_dir": "2019/Beach", "last_modified": 1_560_000_000_000})
    assert r.status_code == 200, r.text
    up = r.json()

    assert client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:100_000]).json()["received"] == 100_000
    # A retried chunk (network blip) is told where the server actually is
    r = client.put(f"/api/uploads/{up['id']}?offset=0", content=data[:100_000])
    assert r.status_code == 409 and r.json()["detail"]["received"] == 100_000
    # Completing early is refused
    assert client.post(f"/api/uploads/{up['id']}/complete").status_code == 409
    client.put(f"/api/uploads/{up['id']}?offset=100000", content=data[100_000:])
    done = client.post(f"/api/uploads/{up['id']}/complete").json()
    assert done["status"] == "complete" and done["ingest_id"]

    ing = session.get(Ingest, done["ingest_id"])
    assert ing.provenance == "Mom's iPhone"
    assert ing.original_path.endswith("Mom's iPhone/2019/Beach/IMG_1.jpg")
    assert int(os.path.getmtime(ing.original_path)) == 1_560_000_000          # device date kept
    assert not os.listdir(os.path.join(hot, ".dam-uploads"))                   # staging emptied
    process(session, ing, {"hotfolder": hot, "library": lib, "delete_mode": "delete"})
    assert ing.status == "accepted"


def test_upload_rejects_bad_input(session, dirs):
    configure(dirs)
    assert client.post("/api/uploads", json={"filename": "e.txt", "size": 0}).status_code == 400
    r = client.post("/api/uploads", json={"filename": "../../etc/passwd", "size": 10,
                                         "relative_dir": "../../..", "provenance": "../x"})
    assert r.status_code == 200
    up = r.json()
    assert up["filename"] == "passwd" and up["relative_dir"] == "" and "/" not in up["provenance"]
    assert client.put(f"/api/uploads/{up['id']}?offset=0", content=b"x" * 11).status_code == 400   # over size
    assert client.post("/api/uploads", json={"filename": "huge.mov", "size": 10 ** 15}).status_code == 507


def test_cancel_removes_staged_data(session, dirs):
    hot, _ = configure(dirs)
    up = client.post("/api/uploads", json={"filename": "a.mov", "size": 10}).json()
    client.put(f"/api/uploads/{up['id']}?offset=0", content=b"12345")
    assert client.delete(f"/api/uploads/{up['id']}").json()["status"] == "cancelled"
    assert not os.listdir(os.path.join(hot, ".dam-uploads"))


def test_uploads_require_login(session):
    anon = TestClient(app)
    anon.cookies.clear()
    assert anon.post("/api/uploads", json={"filename": "a", "size": 1}).status_code == 401
