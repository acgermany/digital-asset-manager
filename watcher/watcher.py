"""
dam-watcher: watches the to_index hot folder and phone dump paths,
             creates ingest jobs in the SQLite jobs table.

Folder layout under /nas/ingest:
  to_index/     ← watcher watches ONLY this (hot folder, drop files here)
  to_archive/   ← review queue; worker moves files here after hashing/dedup
  duplicates/   ← SHA256 matches moved here (not watched)
  rejected/     ← rejected files moved here (not watched)

/nas/archive/   ← separate volume; approved files promoted here
"""
import fnmatch
import json
import os
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer
from sqlalchemy import create_engine, text, event
from sqlalchemy.pool import StaticPool

DB_URL        = (
    os.environ.get("DB_URL")
    or "sqlite:///{}".format(os.environ.get("DB_PATH", "/data/inventory.db"))
)
INGEST_PATH   = os.environ.get("INGEST_PATH",   "/nas/ingest/to_index")
# Comma-separated list of "initials:/glob/path/iPhone*" entries
PHONE_SOURCES = os.environ.get("PHONE_SOURCES", "")

# Minimum file size before we consider it worth ingesting (skip 0-byte files)
_MIN_BYTES = 1


def _make_engine():
    if DB_URL.startswith("sqlite"):
        _engine = create_engine(
            DB_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        @event.listens_for(_engine, "connect")
        def _set_wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
        return _engine
    return create_engine(DB_URL)

engine = _make_engine()

def _is_sqlite() -> bool:
    return engine.dialect.name == "sqlite"

def _get_db():
    return engine.connect()


def _recent_completed_expr() -> str:
    if _is_sqlite():
        return "created_at > datetime('now', '-60 seconds')"
    return "created_at > NOW() - INTERVAL '60 seconds'"


def _seed_sources() -> None:
    """Seed watcher_sources table from env vars on first start (if table is empty)."""
    conn = _get_db()
    try:
        if conn.execute(text("SELECT COUNT(*) FROM watcher_sources")).scalar() > 0:
            return
        _insert_or_ignore_sql = (
            "INSERT OR IGNORE INTO watcher_sources (name, path, initials, source_type, enabled) "
            "VALUES (:name, :path, :initials, :source_type, 1)"
            if _is_sqlite() else
            "INSERT INTO watcher_sources (name, path, initials, source_type, enabled) "
            "VALUES (:name, :path, :initials, :source_type, 1) ON CONFLICT DO NOTHING"
        )
        conn.execute(text(_insert_or_ignore_sql), {
            "name": "Hot Folder", "path": INGEST_PATH, "initials": "", "source_type": "hot_folder"
        })
        if PHONE_SOURCES:
            for initials, pattern in _parse_phone_sources(PHONE_SOURCES):
                conn.execute(text(_insert_or_ignore_sql), {
                    "name": f"Phone ({initials}): {os.path.basename(pattern)}",
                    "path": pattern,
                    "initials": initials,
                    "source_type": "phone_dump",
                })
        conn.commit()
        print("[watcher] seeded watcher_sources from env vars", flush=True)
    except Exception as e:
        print(f"[watcher] WARNING: could not seed watcher_sources: {e}", flush=True)
    finally:
        conn.close()


def _is_source_enabled(source_type: str, path: str) -> bool:
    """Return False if the source has been disabled in the DB."""
    try:
        conn = _get_db()
        row = conn.execute(
            text("SELECT enabled FROM watcher_sources WHERE source_type=:stype AND path=:path"),
            {"stype": source_type, "path": path}
        ).mappings().fetchone()
        conn.close()
        return row is None or bool(row["enabled"])
    except Exception:
        return True  # fail open


def _create_ingest_job(file_path: str, ingest_source: str, initials: str) -> None:
    try:
        conn = _get_db()
        # Skip if an ingest job for this path is already pending, running,
        # or completed within the last 60 seconds (catches startup-scan + watchdog races)
        existing = conn.execute(
            text(f"""SELECT id FROM jobs WHERE type='ingest' AND params LIKE :pattern
               AND (
                   status IN ('pending','running')
                   OR (status = 'completed' AND {_recent_completed_expr()})
               )"""),
            {"pattern": f"%{file_path}%"}
        ).mappings().fetchone()
        if existing:
            print(f"[watcher] skipping already-queued: {file_path}", flush=True)
            conn.close()
            return
        conn.execute(
            text("INSERT INTO jobs (type, params) VALUES ('ingest', :params)"),
            {"params": json.dumps({
                "file_path":     file_path,
                "ingest_source": ingest_source,
                "initials":      initials,
            })}
        )
        conn.commit()
        conn.close()
        print(f"[watcher] queued ingest: {file_path} (source={ingest_source}, initials={initials})", flush=True)
    except Exception as e:
        print(f"[watcher] ERROR creating job for {file_path}: {e}", flush=True)


def _is_hidden(path: str) -> bool:
    return any(part.startswith(".") or part.startswith("$") or part.startswith("#")
               for part in path.split(os.sep))


def _queue_path(path: str, ingest_source: str, initials: str) -> None:
    """Queue ingest jobs for path — handles both single files and directories."""
    # Short settle delay: network mounts fire events before the file is fully present
    time.sleep(1)

    if os.path.isdir(path):
        # A folder was dropped — walk it and queue every file inside
        for root, dirs, files in os.walk(path):
            # Skip hidden/system directories in-place
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("$") and not d.startswith("#")]
            for fname in files:
                fpath = os.path.join(root, fname)
                if _is_hidden(fpath):
                    continue
                try:
                    if os.path.getsize(fpath) < _MIN_BYTES:
                        continue
                except OSError:
                    continue
                _create_ingest_job(fpath, ingest_source, initials)
        return

    if not os.path.isfile(path):
        return
    try:
        if os.path.getsize(path) < _MIN_BYTES:
            return
    except OSError:
        return
    _create_ingest_job(path, ingest_source, initials)


class IngestHandler(FileSystemEventHandler):
    """Fires on files or folders dropped into to_archive/."""

    def _handle(self, path: str) -> None:
        if _is_hidden(path):
            return
        if not _is_source_enabled("hot_folder", INGEST_PATH):
            return
        _queue_path(path, "hot_folder", "")

    def on_created(self, event):
        self._handle(event.src_path)

    def on_moved(self, event):
        self._handle(event.dest_path)


class PhoneHandler(FileSystemEventHandler):
    """Fires on files that land under a phone dump glob path."""

    def __init__(self, initials: str, glob_pattern: str):
        self.initials     = initials
        self.glob_pattern = glob_pattern  # e.g. /nas/homes/auston/Photos/MobileBackup/iPhone*

    def _matches(self, path: str) -> bool:
        """Return True if path descends from a directory matching the glob pattern."""
        # Walk up the directory tree checking each ancestor against the glob
        current = os.path.dirname(path)
        while current and current != os.path.dirname(current):
            if fnmatch.fnmatch(current, self.glob_pattern):
                return True
            current = os.path.dirname(current)
        return False

    def _handle(self, path: str) -> None:
        if _is_hidden(path):
            return
        if not self._matches(path):
            return
        if not _is_source_enabled("phone_dump", self.glob_pattern):
            return
        _queue_path(path, "phone_dump", self.initials)

    def on_created(self, event):
        self._handle(event.src_path)

    def on_moved(self, event):
        self._handle(event.dest_path)


def _parse_phone_sources(sources_str: str) -> list[tuple[str, str]]:
    """Parse 'ag:/nas/path/iPhone*,hb:/nas/other' into [(initials, pattern), ...]."""
    result = []
    for entry in sources_str.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        initials, pattern = entry.split(":", 1)
        result.append((initials.strip(), pattern.strip()))
    return result


def _startup_scan(path: str, ingest_source: str, initials: str) -> None:
    """Queue any files already sitting in path at startup (watchdog misses pre-existing files)."""
    if not os.path.isdir(path):
        return
    count = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.startswith("$") and not d.startswith("#")]
        for fname in files:
            fpath = os.path.join(root, fname)
            if _is_hidden(fpath):
                continue
            try:
                if os.path.getsize(fpath) < _MIN_BYTES:
                    continue
            except OSError:
                continue
            _create_ingest_job(fpath, ingest_source, initials)
            count += 1
    if count:
        print(f"[watcher] startup scan queued {count} existing file(s) from {path}", flush=True)


def main() -> None:
    print(f"[watcher] starting — INGEST_PATH={INGEST_PATH}", flush=True)

    # Wait for the API to initialise the DB before seeding or queuing jobs
    while True:
        try:
            conn = _get_db()
            conn.execute(text("SELECT 1 FROM jobs LIMIT 1"))
            conn.close()
            break
        except Exception:
            print("[watcher] waiting for DB to be ready...", flush=True)
            time.sleep(2)

    _seed_sources()
    observer = Observer()

    # Hot folder watcher
    if os.path.isdir(INGEST_PATH):
        observer.schedule(IngestHandler(), INGEST_PATH, recursive=True)
        print(f"[watcher] watching hot folder: {INGEST_PATH}", flush=True)
        _startup_scan(INGEST_PATH, "hot_folder", "")
    else:
        print(f"[watcher] WARNING: INGEST_PATH not found: {INGEST_PATH}", flush=True)

    # Phone dump watchers
    if PHONE_SOURCES:
        for initials, pattern in _parse_phone_sources(PHONE_SOURCES):
            watch_dir = os.path.dirname(pattern)
            if os.path.isdir(watch_dir):
                observer.schedule(PhoneHandler(initials, pattern), watch_dir, recursive=True)
                print(f"[watcher] watching phone source ({initials}): {pattern}", flush=True)
            else:
                print(f"[watcher] WARNING: phone source parent not found: {watch_dir}", flush=True)

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
