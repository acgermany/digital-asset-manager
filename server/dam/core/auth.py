"""Single-user login for the web UI.

- The password is stored as a salted PBKDF2-SHA256 hash (app_settings key "auth"), or comes
  from APP_PASSWORD in the environment.
- First-time setup needs a one-time code that is only visible on the server (logs and
  DATA_DIR/secrets/setup-code), so someone else on the network can't claim the app first.
- Sessions are HMAC-signed cookies; changing the password invalidates every session.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings as env
from ..models import AppSetting

log = logging.getLogger(__name__)

COOKIE = "dam_session"
ITERATIONS = 310_000
MIN_LENGTH = 10
DATA_DIR = os.environ.get("DATA_DIR", "/data")
SECRETS_DIR = os.path.join(DATA_DIR, "secrets")
SETUP_CODE_FILE = os.path.join(SECRETS_DIR, "setup-code")
SECRET_FILE = os.path.join(SECRETS_DIR, "session-secret")


def _ensure_dir() -> None:
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)


def _write_private(path: str, value: str) -> None:
    _ensure_dir()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)


_secret: Optional[bytes] = None


def secret() -> bytes:
    """Signing key: SECRET_KEY env if set to something real, else a random key kept in /data."""
    global _secret
    if _secret:
        return _secret
    if env.secret_key and env.secret_key not in ("dev-secret-change-me", "change-me"):
        _secret = env.secret_key.encode()
        return _secret
    try:
        with open(SECRET_FILE) as f:
            _secret = f.read().strip().encode()
    except FileNotFoundError:
        _secret = secrets.token_hex(32).encode()
        _write_private(SECRET_FILE, _secret.decode())
    return _secret


# ---------------------------------------------------------------- password storage

def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)


def _stored(session: Session) -> Optional[dict]:
    row = session.get(AppSetting, "auth")
    return row.value if row else None


def managed_by_env() -> bool:
    """APP_PASSWORD in the environment wins over a password set in the app."""
    return bool(env.app_password)


def configured(session: Session) -> bool:
    return bool(env.app_password) or bool(_stored(session))


def set_password(session: Session, password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Use at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    value = {"salt": salt.hex(), "hash": _hash(password, salt).hex(), "iterations": ITERATIONS,
             "epoch": secrets.token_hex(8)}  # new epoch = every existing session is invalid
    row = session.get(AppSetting, "auth")
    if row:
        row.value = value
    else:
        session.add(AppSetting(key="auth", value=value))
    session.commit()
    try:
        os.unlink(SETUP_CODE_FILE)
    except FileNotFoundError:
        pass


def check_password(session: Session, password: str) -> bool:
    if env.app_password:
        return hmac.compare_digest(password.encode(), env.app_password.encode())
    stored = _stored(session)
    if not stored:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(stored["salt"]),
                                 int(stored.get("iterations", ITERATIONS)))
    return hmac.compare_digest(actual.hex(), stored["hash"])


def _epoch(session: Session) -> str:
    if env.app_password:
        return hashlib.sha256(env.app_password.encode()).hexdigest()[:16]
    stored = _stored(session)
    return stored.get("epoch", "") if stored else ""


# ---------------------------------------------------------------- first-time setup code

def setup_code() -> str:
    """The one-time code for creating the password (created on first use, shown in logs)."""
    try:
        with open(SETUP_CODE_FILE) as f:
            code = f.read().strip()
            if code:
                return code
    except FileNotFoundError:
        pass
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    _write_private(SETUP_CODE_FILE, code)
    return code


def announce_setup_code(session: Session) -> None:
    if not configured(session):
        code = setup_code()
        log.warning("No login password set yet. Open the web UI and use setup code %s "
                    "(also in data/secrets/setup-code).", code)


def check_setup_code(code: str) -> bool:
    try:
        with open(SETUP_CODE_FILE) as f:
            expected = f.read().strip()
    except FileNotFoundError:
        return False
    normalise = lambda c: c.strip().upper().replace(" ", "")  # noqa: E731
    return bool(expected) and hmac.compare_digest(normalise(code), normalise(expected))


# ---------------------------------------------------------------- sessions

def make_token(session: Session) -> str:
    payload = {"exp": int(time.time()) + env.session_hours * 3600, "ep": _epoch(session)}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def valid_token(session: Session, token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return False
    return payload.get("exp", 0) > time.time() and payload.get("ep") == _epoch(session)


# ---------------------------------------------------------------- brute-force protection

_failures: dict = {}
_lock = threading.Lock()
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


def locked_out(client: str) -> int:
    """Seconds remaining in a lockout for this client (0 if none)."""
    with _lock:
        count, until = _failures.get(client, (0, 0.0))
        return max(0, int(until - time.time())) if count >= MAX_FAILURES else 0


def record_failure(client: str) -> None:
    with _lock:
        count, until = _failures.get(client, (0, 0.0))
        if until and until < time.time():
            count = 0
        count += 1
        _failures[client] = (count, time.time() + LOCKOUT_SECONDS if count >= MAX_FAILURES else 0.0)


def record_success(client: str) -> None:
    with _lock:
        _failures.pop(client, None)
