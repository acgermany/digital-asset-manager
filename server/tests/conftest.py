import os
import tempfile

# Configure before any dam import: test DB and a fake host root.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://dam:dam@localhost:55432/dam_test")
_HOST = tempfile.mkdtemp(prefix="dam-host-")
os.environ["HOST_ROOTS"] = f"Home|{_HOST}|/Users/tester"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dam-data-")
# The API requires a login; tests run as a signed-in user (see the TestClient patch below).
os.environ["APP_PASSWORD"] = "test-password-123"
os.environ["SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from dam.core import auth, settings_store  # noqa: E402
from dam.db import SessionLocal, reset_db  # noqa: E402


@pytest.fixture
def host_root():
    return _HOST


@pytest.fixture
def session():
    try:
        reset_db()
    except OperationalError:
        pytest.skip("test Postgres not available on localhost:55432")
    settings_store._cache_at = 0.0
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def dirs(host_root):
    """Fresh hot folder + library inside the fake host root."""
    base = tempfile.mkdtemp(dir=host_root)
    hot, lib = os.path.join(base, "hot"), os.path.join(base, "library")
    os.makedirs(hot)
    os.makedirs(lib)
    open(os.path.join(lib, ".dam_library"), "w").close()
    return hot, lib


# Every TestClient starts signed in. test_auth.py builds its own clients to test the login itself.
_original_init = TestClient.__init__


def _signed_in_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    if auth.env.app_password:   # tests that switch to a database-stored password sign in themselves
        self.cookies.set(auth.COOKIE, auth.make_token(None))


TestClient.__init__ = _signed_in_init
