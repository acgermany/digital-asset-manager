import os

from fastapi.testclient import TestClient
from sqlalchemy import text

from dam import queue, tasks
from dam.api.app import app
from dam.core.ingest import process, register
from dam.models import Job, QueueConfig
from dam.worker import WorkerProcess

client = TestClient(app)


def make_assets(session, dirs, n, kind_ext="jpg", folder="P"):
    hot, lib = dirs
    ids = []
    for i in range(n):
        p = os.path.join(hot, folder, f"f{i}.{kind_ext}")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(f"{folder}{i}".encode())
        st = os.stat(p)
        ing = register(session, hot, p, st.st_size, st.st_mtime)
        session.commit()
        process(session, ing, {"hotfolder": hot, "library": lib, "delete_mode": "delete"})
        ids.append(ing.asset_id)
    session.execute(text("DELETE FROM jobs"))  # ingest jobs aren't the subject here
    session.commit()
    return ids


def test_cap_holds_across_workers(session, dirs):
    ids = make_assets(session, dirs, 3)
    for a in ids:
        queue.enqueue(session, "describe", queue="vision", asset_id=a)
    session.commit()
    assert session.get(QueueConfig, "vision").max_running == 1
    first = queue.claim(session, ["vision"], "w1")
    assert first is not None
    assert queue.claim(session, ["vision"], "w2") is None          # capped at 1 cluster-wide
    session.get(QueueConfig, "vision").max_running = 2
    session.commit()
    assert queue.claim(session, ["vision"], "w2") is not None


def test_paused_queue_is_not_claimed(session, dirs):
    ids = make_assets(session, dirs, 1)
    queue.enqueue(session, "render", queue="render", asset_id=ids[0])
    session.get(QueueConfig, "render").enabled = False
    session.commit()
    assert queue.claim(session, ["render"], "w1") is None
    session.get(QueueConfig, "render").enabled = True
    session.commit()
    assert queue.claim(session, ["render"], "w1") is not None


def test_one_live_job_per_asset_and_batch_fan_out(session, dirs):
    ids = make_assets(session, dirs, 4)
    queue.enqueue(session, "render", queue="render", asset_id=ids[0])
    assert queue.enqueue(session, "render", queue="render", asset_id=ids[0]) is None   # duplicate skipped
    batch = queue.create_batch(session, "render", "render", "a.kind = 'image'", {}, "Render previews")
    session.commit()
    assert batch.total == 3                                                          # ids[0] already live
    p = queue.batch_progress(session, batch)["progress"]
    assert p["total"] == 3 and p["queued"] == 3


def test_batch_progress_and_cancel(session, dirs):
    make_assets(session, dirs, 3)
    batch = queue.create_batch(session, "render", "render", "TRUE", {}, "All")
    session.commit()
    job = queue.claim(session, ["render"], "w1")
    queue.complete(session, job, "rendered")
    p = queue.batch_progress(session, batch)["progress"]
    assert p["done"] == 1 and p["rendered"] == 1
    queue.cancel_batch(session, batch)
    session.commit()
    p = queue.batch_progress(session, batch)["progress"]
    assert p["cancelled"] == 2


def test_stalled_job_is_reclaimed(session, dirs):
    ids = make_assets(session, dirs, 1)
    queue.enqueue(session, "render", queue="render", asset_id=ids[0])
    session.commit()
    job = queue.claim(session, ["render"], "dead-worker")
    session.execute(text("UPDATE jobs SET locked_at = now() - interval '2 hours' WHERE id = :i"), {"i": job.id})
    session.commit()
    assert queue.requeue_stale(session) == 1
    session.refresh(job)
    assert job.status == "queued" and job.locked_by is None


def test_claim_more_takes_a_chunk(session, dirs):
    ids = make_assets(session, dirs, 5)
    for a in ids:
        queue.enqueue(session, "embed", queue="embed", asset_id=a)
    session.commit()
    first = queue.claim(session, ["embed"], "w1")
    extra = queue.claim_more(session, first, 31)
    assert len(extra) == 4 and all(j.status == "running" for j in extra)


def test_worker_runs_jobs_applies_overrides_and_drains(session, dirs, monkeypatch):
    ids = make_assets(session, dirs, 3)
    for a in ids:
        queue.enqueue(session, "render", queue="render", asset_id=a)
    session.commit()
    monkeypatch.setitem(tasks.HANDLERS, "render", lambda s, job, cfg: "rendered")

    w = WorkerProcess("test", ["render"], 1)
    w.register()
    from dam.db import SessionLocal
    with SessionLocal() as s:
        assert w.run_one(s)
    assert session.execute(text("SELECT COUNT(*) FROM jobs WHERE status='done'")).scalar() == 1

    # Override from the app: drain -> no more claims
    r = client.patch(f"/api/workers/{w.id}", json={"status": "draining", "concurrency": 3})
    assert r.status_code == 200
    w.heartbeat()
    assert w.draining and w.concurrency == 3
    with SessionLocal() as s:
        assert not w.run_one(s)

    client.patch(f"/api/workers/{w.id}", json={"status": "running"})
    w.heartbeat()
    with SessionLocal() as s:
        assert w.run_one(s)
    w.heartbeat()   # counters are reported on heartbeat
    assert client.get("/api/workers").json()["workers"][0]["processed"] == 2


def test_defer_does_not_burn_attempts(session, dirs, monkeypatch):
    ids = make_assets(session, dirs, 1)
    queue.enqueue(session, "describe", queue="vision", asset_id=ids[0])
    session.commit()

    def asleep(s, job, cfg):
        raise tasks.Defer("Mac asleep", 60)
    monkeypatch.setitem(tasks.HANDLERS, "describe", asleep)
    w = WorkerProcess("t", ["vision"], 1)
    w.register()
    from dam.db import SessionLocal
    with SessionLocal() as s:
        assert w.run_one(s)
    job = session.query(Job).one()
    session.refresh(job)
    assert job.status == "queued" and job.attempts == 0 and job.waiting_reason == "Mac asleep"


def test_queue_api(session):
    qs = {q["name"]: q for q in client.get("/api/queues").json()["queues"]}
    assert set(qs) == {"ingest", "scan", "render", "embed", "vision", "speech"}
    r = client.patch("/api/queues/render", json={"max_running": 2, "enabled": False})
    q = {q["name"]: q for q in r.json()["queues"]}["render"]
    assert q["max_running"] == 2 and q["enabled"] is False
    r = client.patch("/api/queues/render", json={"unlimited": True})
    assert {q["name"]: q for q in r.json()["queues"]}["render"]["max_running"] is None
    assert client.patch("/api/queues/render", json={"stall_seconds": 5}).status_code == 400


def test_render_batch_api(session, dirs, monkeypatch):
    make_assets(session, dirs, 2)
    r = client.post("/api/render", json={"kinds": ["image"]})
    assert r.status_code == 200 and r.json()["progress"]["total"] == 2
    assert client.post("/api/render", json={"kinds": ["image"]}).status_code == 400   # already running
