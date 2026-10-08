from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    """Transactional scope: commit on success, roll back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session():
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create the pgvector extension and all tables (POC: no migrations yet)."""
    from . import models  # noqa: F401  (registers tables)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    models.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ai_state (id, state, reachable, model_installed, model_loaded, semantic_enabled) "
            "VALUES (1, 'offline', false, false, false, false) ON CONFLICT (id) DO NOTHING"
        ))


def reset_db() -> None:
    """Drop and recreate everything. Tests only."""
    from . import models

    models.Base.metadata.drop_all(engine)
    init_db()
