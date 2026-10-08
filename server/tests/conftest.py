import os
import tempfile

# Configure before any dam import: test DB and a fake host root.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://dam:dam@localhost:55432/dam_test")
_HOST = tempfile.mkdtemp(prefix="dam-host-")
os.environ["HOST_ROOTS"] = f"Home|{_HOST}|/Users/tester"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dam-data-")

import pytest  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from dam.core import settings_store  # noqa: E402
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
