import httpx
from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core import ai

client = TestClient(app)


def test_ai_calls_identify_the_dam_and_carry_priority():
    h = ai.headers({"orchestrator_key": "orch_abc"}, ai.PRIORITY_INTERACTIVE)
    assert h == {"X-Client-Name": "dam", "X-Priority": "50", "Authorization": "Bearer orch_abc"}
    assert "Authorization" not in ai.headers({}, 0)


def test_status_when_not_configured(session):
    r = client.get("/api/orchestrator/status").json()
    assert r["configured"] is False and r["routed"] == {"ollama": False, "whisper": False}


def test_secrets_are_write_only_and_routing_is_reported(session, monkeypatch):
    r = client.put("/api/config", json={"orchestrator_url": "http://orch:11500/", "orchestrator_key": "orch_secret",
                                        "ollama_url": "http://orch:11500"})
    assert r.status_code == 200, r.text
    settings = r.json()["settings"]
    assert "orchestrator_key" not in settings and settings["orchestrator_key_set"] is True
    assert settings["orchestrator_url"] == "http://orch:11500"

    def fake_get(url, headers=None, timeout=None):
        assert url == "http://orch:11500/admin/status"
        return httpx.Response(200, json={"nodes": [], "models": [], "queue": [], "events": [], "now": 0})
    monkeypatch.setattr(httpx, "get", fake_get)
    s = client.get("/api/orchestrator/status").json()
    assert s["error"] is None and s["routed"] == {"ollama": True, "whisper": False}
    assert s["status"]["queue"] == []


def test_bad_orchestrator_url_is_rejected(session):
    assert client.put("/api/config", json={"orchestrator_url": "not a url"}).status_code == 400
