import dataclasses
import os

import pytest
from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core import auth


def anonymous() -> TestClient:
    c = TestClient(app)
    c.cookies.clear()
    return c


def test_api_requires_login_but_health_is_public(session):
    c = anonymous()
    r = c.get("/api/config")
    assert r.status_code == 401 and r.json()["auth"] == "login"
    assert c.get("/api/files/1/content").status_code == 401
    assert c.get("/api/health").json() == {"status": "ok"}       # nothing beyond up/down


def test_env_password_login_and_lockout(session):
    c = anonymous()
    assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"password": "test-password-123"})
    assert r.status_code == 200 and auth.COOKIE in r.cookies
    assert c.get("/api/config").status_code == 200

    other = anonymous()
    for _ in range(auth.MAX_FAILURES):
        other.post("/api/auth/login", json={"password": "wrong"}, headers={"X-Real-IP": "10.9.9.9"})
    r = other.post("/api/auth/login", json={"password": "test-password-123"}, headers={"X-Real-IP": "10.9.9.9"})
    assert r.status_code == 429                                    # locked out even with the right one


def test_tampered_cookie_is_rejected(session):
    c = anonymous()
    token = auth.make_token(None)
    c.cookies.set(auth.COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert c.get("/api/config").status_code == 401


@pytest.fixture
def db_password_mode(monkeypatch):
    """Run as if APP_PASSWORD were not set, so the password lives in the database."""
    monkeypatch.setattr(auth, "env", dataclasses.replace(auth.env, app_password=""))
    try:
        os.unlink(auth.SETUP_CODE_FILE)
    except FileNotFoundError:
        pass


def test_first_time_setup_needs_the_server_side_code(session, db_password_mode):
    c = anonymous()
    assert c.get("/api/config").json()["auth"] == "setup"
    code = auth.setup_code()
    assert c.post("/api/auth/setup", json={"setup_code": "AAAA-BBBB-CCCC", "password": "long-enough-pw"}).status_code == 403
    assert c.post("/api/auth/setup", json={"setup_code": code, "password": "short"}).status_code == 400
    r = c.post("/api/auth/setup", json={"setup_code": code.lower(), "password": "long-enough-pw"})
    assert r.status_code == 200
    assert c.get("/api/config").status_code == 200
    assert not os.path.exists(auth.SETUP_CODE_FILE)                 # one-time
    assert anonymous().post("/api/auth/setup", json={"setup_code": code, "password": "x" * 12}).status_code == 400


def test_password_change_signs_out_other_sessions(session, db_password_mode):
    auth.set_password(session, "first-password")
    a, b = anonymous(), anonymous()
    a.post("/api/auth/login", json={"password": "first-password"})
    b.post("/api/auth/login", json={"password": "first-password"})
    assert b.get("/api/config").status_code == 200
    r = a.post("/api/auth/password", json={"current": "first-password", "new": "second-password"})
    assert r.status_code == 200
    assert a.get("/api/config").status_code == 200                 # the changer stays in
    assert b.get("/api/config").status_code == 401                 # everyone else is out
    assert "auth" not in a.get("/api/config").json()["settings"]   # hash never exposed


def test_cookie_is_secure_over_https(session):
    c = anonymous()
    r = c.post("/api/auth/login", json={"password": "test-password-123"}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()
    r = c.post("/api/auth/login", json={"password": "test-password-123"})
    assert "secure" not in r.headers["set-cookie"].lower()
