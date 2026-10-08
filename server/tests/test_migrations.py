from sqlalchemy import text

from dam import db, queue
from dam.models import Asset


def test_indexes_added_to_existing_tables(session):
    """Simulate a database created before the index existed, then restart."""
    session.execute(text("DROP INDEX ux_jobs_live_asset"))
    session.execute(text("DROP INDEX idx_assets_tags"))
    session.commit()
    db.init_db()
    names = {r[0] for r in session.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'"))}
    assert {"ux_jobs_live_asset", "idx_assets_tags", "idx_jobs_claim", "idx_jobs_batch"} <= names


def test_duplicate_live_jobs_are_collapsed_before_unique_index(session):
    session.execute(text("DROP INDEX ux_jobs_live_asset"))
    asset = Asset(sha256="x", filesize=1, kind="image")
    session.add(asset)
    session.flush()
    aid = asset.id
    for _ in range(2):
        session.execute(text("""INSERT INTO jobs (kind, queue, status, asset_id, payload, progress, attempts,
                                max_attempts, priority, run_after, created_at)
                                VALUES ('render', 'render', 'queued', :a, '{}', '{}', 0, 2, 0, now(), now())"""),
                        {"a": aid})
    session.commit()
    db.init_db()
    live = session.execute(text("SELECT COUNT(*) FROM jobs WHERE status = 'queued'")).scalar()
    assert live == 1
    assert queue.enqueue(session, "render", queue="render", asset_id=aid) is None
