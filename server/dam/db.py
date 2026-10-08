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
    with engine.begin() as conn:
        # Columns added after a table first existed (POC: no migration tool yet)
        for ddl in (
            "ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb",
            "ADD COLUMN IF NOT EXISTS description TEXT",
            "ADD COLUMN IF NOT EXISTS description_tags JSONB DEFAULT '[]'::jsonb",
            "ADD COLUMN IF NOT EXISTS description_model VARCHAR(128)",
            "ADD COLUMN IF NOT EXISTS described_at TIMESTAMPTZ",
            "ADD COLUMN IF NOT EXISTS description_error TEXT",
            "ADD COLUMN IF NOT EXISTS transcript TEXT",
            "ADD COLUMN IF NOT EXISTS transcript_segments JSONB DEFAULT '[]'::jsonb",
            "ADD COLUMN IF NOT EXISTS transcript_language VARCHAR(16)",
            "ADD COLUMN IF NOT EXISTS transcript_model VARCHAR(128)",
            "ADD COLUMN IF NOT EXISTS transcribed_at TIMESTAMPTZ",
            "ADD COLUMN IF NOT EXISTS transcript_error TEXT",
        ):
            conn.execute(text(f"ALTER TABLE IF EXISTS assets {ddl}"))
        _migrate_jobs(conn)
    models.Base.metadata.create_all(engine)
    _create_missing_indexes(models.Base.metadata)
    seed_queues()
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


QUEUE_DEFAULTS = [
    # name, description, max_running (cluster-wide; None = unlimited), max_attempts, stall_seconds
    ("ingest", "Hot-folder files: checksum, de-duplicate, metadata, file into library", None, 3, 300),
    ("scan", "Catalog scans of existing folders", 1, 1, 600),
    ("render", "Thumbnails and browser-playable video proxies (ffmpeg)", None, 2, 600),
    ("embed", "Search embeddings (Ollama on the Mac)", 2, 50, 300),
    ("vision", "Photo captions and video summaries (Ollama vision model on the Mac)", 1, 50, 600),
    ("speech", "Transcripts (Whisper server on the Mac)", 1, 50, 900),
]


def seed_queues() -> None:
    with engine.begin() as conn:
        for name, desc, cap, attempts, stall in QUEUE_DEFAULTS:
            conn.execute(text("""
                INSERT INTO queues (name, description, enabled, max_running, max_attempts, stall_seconds)
                VALUES (:n, :d, true, :c, :a, :s)
                ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
            """), {"n": name, "d": desc, "c": cap, "a": attempts, "s": stall})


def _migrate_jobs(conn) -> None:
    """Move jobs from the single-process design (default/heavy/ai queues, whole-batch jobs)."""
    exists = conn.execute(text("SELECT to_regclass('public.jobs')")).scalar()
    if not exists:
        return
    conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS batch_id INTEGER"))
    conn.execute(text("DROP INDEX IF EXISTS idx_jobs_claim"))
    conn.execute(text("UPDATE jobs SET queue = 'ingest' WHERE queue = 'default'"))
    conn.execute(text("UPDATE jobs SET queue = 'scan' WHERE kind = 'catalog_scan' AND queue = 'heavy'"))
    # Whole-library batch jobs are replaced by per-item jobs; retire any still pending.
    conn.execute(text("""
        UPDATE jobs SET status = 'cancelled', finished_at = now(),
                        error = 'Replaced by the scalable job system; start the batch again'
        WHERE kind IN ('embed_batch', 'describe_batch', 'transcribe_batch', 'proxy')
          AND status IN ('queued', 'running', 'cancelling')"""))
    # The one-live-job-per-asset index can't be built over duplicates: keep the oldest.
    conn.execute(text("""
        UPDATE jobs j SET status = 'cancelled', finished_at = now(), error = 'Duplicate of an earlier job'
        WHERE j.asset_id IS NOT NULL AND j.status IN ('queued', 'running', 'cancelling')
          AND EXISTS (SELECT 1 FROM jobs k WHERE k.kind = j.kind AND k.asset_id = j.asset_id
                      AND k.status IN ('queued', 'running', 'cancelling') AND k.id < j.id)"""))


def _create_missing_indexes(metadata) -> None:
    """create_all() only makes indexes together with new tables; add any declared later."""
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            for index in table.indexes:
                index.create(conn, checkfirst=True)
