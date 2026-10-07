import concurrent.futures
import hashlib
import json
import os
import shutil
import time
from datetime import datetime

from sqlalchemy import create_engine, text, event
from sqlalchemy.pool import StaticPool

DB_URL           = (
    os.environ.get("DB_URL")
    or "sqlite:///{}".format(os.environ.get("DB_PATH", "/data/inventory.db"))
)
SCAN_PATH         = os.environ.get("SCAN_PATH",         "/scan")
INGEST_PATH       = os.environ.get("INGEST_PATH",       "/nas/ingest")           # root (duplicates/ rejected/ live here)
INGEST_WATCH_PATH = os.environ.get("INGEST_WATCH_PATH", "/nas/ingest/to_index")  # hot folder watcher watches
STAGING_PATH      = os.environ.get("STAGING_PATH",      "/nas/ingest/to_archive") # review queue before archive
ARCHIVE_PATH      = os.environ.get("ARCHIVE_PATH",      "/nas/archive")
WORKFLOWS_PATH    = os.environ.get("WORKFLOWS_PATH",    "/workflows")
POLL_SEC       = 2

MEDIA_EXTS = {
    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.wmv', '.flv', '.mpg', '.mpeg', '.ts',
    '.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.wma', '.opus', '.aiff', '.aif', '.mxf',
}

PHOTO_EXTS = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.tiff', '.dng', '.raw', '.arw'}
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.mxf', '.m4v', '.mpg', '.ts'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.aac', '.m4a', '.aiff', '.ogg', '.opus'}
DOC_EXTS   = {'.pdf', '.txt', '.md'}


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

def get_db():
    return engine.connect()


def _upsert_file_inventory_sql() -> str:
    """SQL to insert/replace a file record from a scan (used in run_scan batch)."""
    if _is_sqlite():
        return """INSERT OR REPLACE INTO file_inventory
            (filepath, filename, filesize, sha256, scan_date)
            VALUES (:filepath, :filename, :filesize, :sha256, CURRENT_TIMESTAMP)"""
    return """INSERT INTO file_inventory
        (filepath, filename, filesize, sha256, scan_date)
        VALUES (:filepath, :filename, :filesize, :sha256, CURRENT_TIMESTAMP)
        ON CONFLICT (filepath, sha256) DO UPDATE SET
            filesize=EXCLUDED.filesize, scan_date=EXCLUDED.scan_date"""

def _insert_or_ignore(table: str, cols: str, placeholders: str) -> str:
    if _is_sqlite():
        return f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
    return f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _sha256(path: str, stop=None) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                if stop and stop():
                    return None
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def run_scan(job_id: int, scan_path: str, workers: int) -> None:
    conn = get_db()

    def update(**kw):
        kw["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        params = dict(kw)
        params["job_id"] = job_id
        conn.execute(text(f"UPDATE jobs SET {sets} WHERE id=:job_id"), params)
        conn.commit()

    def should_stop() -> bool:
        row = conn.execute(
            text("SELECT status FROM jobs WHERE id=:id"), {"id": job_id}
        ).mappings().fetchone()
        return row and row["status"] == "cancelling"

    try:
        update(phase="discovering")

        all_files: list[str] = []
        for root, _, files in os.walk(scan_path):
            if should_stop():
                update(status="cancelled", completed_at=datetime.now().isoformat(), phase="cancelled")
                return
            for fname in files:
                all_files.append(os.path.join(root, fname))
            update(total=len(all_files))

        if should_stop():
            update(status="cancelled", completed_at=datetime.now().isoformat(), phase="cancelled")
            return

        update(phase="scanning", total=len(all_files))

        scan_conn = get_db()
        batch: list[dict] = []
        progress = 0
        errors   = 0

        def process(filepath: str):
            if should_stop():
                return None
            try:
                st = os.stat(filepath)
                checksum = _sha256(filepath, should_stop)
                return {
                    "filepath": filepath,
                    "filename": os.path.basename(filepath),
                    "filesize": st.st_size,
                    "sha256":   checksum,
                }
            except Exception:
                return None

        ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futs = {ex.submit(process, f): f for f in all_files}
            for fut in concurrent.futures.as_completed(futs):
                if should_stop():
                    break
                progress += 1
                result = fut.result()
                if result:
                    batch.append(result)
                else:
                    errors += 1

                if len(batch) >= 100:
                    scan_conn.execute(text(_upsert_file_inventory_sql()), batch)
                    scan_conn.commit()
                    batch.clear()

                if progress % 50 == 0:
                    update(progress=progress, errors=errors, current_file=futs[fut])
        finally:
            ex.shutdown(wait=True, cancel_futures=True)

        if batch and not should_stop():
            scan_conn.execute(text(_upsert_file_inventory_sql()), batch)
            scan_conn.commit()

        scan_conn.close()

        if should_stop():
            update(status="cancelled", completed_at=datetime.now().isoformat(), phase="cancelled", progress=progress, errors=errors)
        else:
            update(status="completed", completed_at=datetime.now().isoformat(), phase="completed", progress=progress, errors=errors)
            _queue_mediainfo_for_scan(scan_path)

    except Exception as e:
        update(status="failed", error=str(e), completed_at=datetime.now().isoformat(), phase="failed")
    finally:
        conn.close()


def _queue_mediainfo_for_scan(scan_path: str) -> None:
    conn = get_db()
    try:
        rows = conn.execute(text("""
            SELECT f.id, f.filename FROM file_inventory f
            LEFT JOIN media_info m ON m.file_id = f.id
            WHERE m.file_id IS NULL
        """)).mappings().fetchall()
        media_ids = [r["id"] for r in rows
                     if os.path.splitext(r["filename"])[1].lower() in MEDIA_EXTS]
        if media_ids:
            conn.execute(
                text("INSERT INTO jobs (type, params) VALUES ('mediainfo', :params)"),
                {"params": json.dumps({"file_ids": media_ids})}
            )
            conn.commit()
            print(f"Queued mediainfo job for {len(media_ids)} media files", flush=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MediaInfo
# ---------------------------------------------------------------------------

import subprocess

def _parse_mediainfo(raw: dict) -> dict:
    tracks  = raw.get("media", {}).get("track", [])
    general = next((t for t in tracks if t.get("@type") == "General"), {})
    video   = next((t for t in tracks if t.get("@type") == "Video"),   {})
    audio   = next((t for t in tracks if t.get("@type") == "Audio"),   {})

    def num(val):
        try: return float(val)
        except (TypeError, ValueError): return None

    def integer(val):
        try: return int(float(val))
        except (TypeError, ValueError): return None

    return {
        "duration":          num(general.get("Duration")),
        "format":            general.get("Format"),
        "bit_rate":          integer(general.get("OverallBitRate")),
        "video_codec":       video.get("Format"),
        "width":             integer(video.get("Width")),
        "height":            integer(video.get("Height")),
        "frame_rate":        num(video.get("FrameRate")),
        "timecode":          video.get("TimeCode_FirstFrame") or general.get("TimeCode_FirstFrame"),
        "audio_codec":       audio.get("Format"),
        "audio_channels":    integer(audio.get("Channels")),
        "audio_sample_rate": integer(audio.get("SamplingRate")),
    }


def run_mediainfo(job_id: int, file_ids: list[int]) -> None:
    conn = get_db()

    def update(**kw):
        kw["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        params = dict(kw)
        params["job_id"] = job_id
        conn.execute(text(f"UPDATE jobs SET {sets} WHERE id=:job_id"), params)
        conn.commit()

    total    = len(file_ids)
    progress = 0
    errors   = 0

    try:
        update(status="running", started_at=datetime.now().isoformat(), phase="scanning", total=total)

        for file_id in file_ids:
            row = conn.execute(
                text("SELECT filepath FROM file_inventory WHERE id=:id"), {"id": file_id}
            ).mappings().fetchone()
            if not row or not os.path.isfile(row["filepath"]):
                errors += 1
                progress += 1
                continue

            filepath = row["filepath"]
            try:
                result = subprocess.run(
                    ["mediainfo", "--Output=JSON", filepath],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    errors += 1
                    progress += 1
                    continue

                raw = json.loads(result.stdout)
                fields = _parse_mediainfo(raw)

                if _is_sqlite():
                    conn.execute(text("""
                        INSERT OR REPLACE INTO media_info
                            (file_id, duration, format, video_codec, width, height,
                             frame_rate, timecode, bit_rate, audio_codec, audio_channels,
                             audio_sample_rate, raw_json, scanned_at)
                        VALUES (:file_id, :duration, :format, :video_codec, :width, :height,
                                :frame_rate, :timecode, :bit_rate, :audio_codec, :audio_channels,
                                :audio_sample_rate, :raw_json, CURRENT_TIMESTAMP)
                    """), {
                        "file_id": file_id,
                        "duration": fields["duration"], "format": fields["format"],
                        "video_codec": fields["video_codec"],
                        "width": fields["width"], "height": fields["height"],
                        "frame_rate": fields["frame_rate"], "timecode": fields["timecode"],
                        "bit_rate": fields["bit_rate"], "audio_codec": fields["audio_codec"],
                        "audio_channels": fields["audio_channels"],
                        "audio_sample_rate": fields["audio_sample_rate"],
                        "raw_json": result.stdout,
                    })
                else:
                    conn.execute(text("""
                        INSERT INTO media_info
                            (file_id, duration, format, video_codec, width, height,
                             frame_rate, timecode, bit_rate, audio_codec, audio_channels,
                             audio_sample_rate, raw_json, scanned_at)
                        VALUES (:file_id, :duration, :format, :video_codec, :width, :height,
                                :frame_rate, :timecode, :bit_rate, :audio_codec, :audio_channels,
                                :audio_sample_rate, :raw_json, CURRENT_TIMESTAMP)
                        ON CONFLICT (file_id) DO UPDATE SET
                            duration=EXCLUDED.duration, format=EXCLUDED.format,
                            video_codec=EXCLUDED.video_codec, width=EXCLUDED.width,
                            height=EXCLUDED.height, frame_rate=EXCLUDED.frame_rate,
                            timecode=EXCLUDED.timecode, bit_rate=EXCLUDED.bit_rate,
                            audio_codec=EXCLUDED.audio_codec,
                            audio_channels=EXCLUDED.audio_channels,
                            audio_sample_rate=EXCLUDED.audio_sample_rate,
                            raw_json=EXCLUDED.raw_json, scanned_at=EXCLUDED.scanned_at
                    """), {
                        "file_id": file_id,
                        "duration": fields["duration"], "format": fields["format"],
                        "video_codec": fields["video_codec"],
                        "width": fields["width"], "height": fields["height"],
                        "frame_rate": fields["frame_rate"], "timecode": fields["timecode"],
                        "bit_rate": fields["bit_rate"], "audio_codec": fields["audio_codec"],
                        "audio_channels": fields["audio_channels"],
                        "audio_sample_rate": fields["audio_sample_rate"],
                        "raw_json": result.stdout,
                    })
                conn.commit()

            except Exception as e:
                print(f"mediainfo failed for {filepath}: {e}", flush=True)
                errors += 1

            progress += 1
            if progress % 10 == 0:
                update(progress=progress, errors=errors, current_file=filepath)

        update(status="completed", completed_at=datetime.now().isoformat(),
               phase="completed", progress=progress, errors=errors)

    except Exception as e:
        update(status="failed", error=str(e), completed_at=datetime.now().isoformat(), phase="failed")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def run_workflow(job_id: int, workflow: str, file_ids: list[int]) -> None:
    conn = get_db()

    def update(**kw):
        kw["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        params = dict(kw)
        params["job_id"] = job_id
        conn.execute(text(f"UPDATE jobs SET {sets} WHERE id=:job_id"), params)
        conn.commit()

    script = os.path.join(WORKFLOWS_PATH, workflow)
    if not os.path.isfile(script) or not os.access(script, os.X_OK):
        update(status="failed", error=f"Script not found or not executable: {script}",
               completed_at=datetime.now().isoformat(), phase="failed")
        conn.close()
        return

    total    = len(file_ids)
    progress = 0
    errors   = 0

    try:
        update(status="running", started_at=datetime.now().isoformat(), phase="running", total=total)

        for file_id in file_ids:
            row = conn.execute(
                text("SELECT filepath FROM file_inventory WHERE id=:id"), {"id": file_id}
            ).mappings().fetchone()
            if not row:
                errors += 1
                progress += 1
                continue

            filepath = row["filepath"]
            update(current_file=filepath, progress=progress)

            try:
                result = subprocess.run(
                    [script, filepath],
                    capture_output=True, text=True, timeout=3600
                )
                if result.returncode != 0:
                    print(f"Workflow {workflow} failed for {filepath}: {result.stderr}", flush=True)
                    errors += 1
            except subprocess.TimeoutExpired:
                print(f"Workflow {workflow} timed out for {filepath}", flush=True)
                errors += 1
            except Exception as e:
                print(f"Workflow {workflow} error for {filepath}: {e}", flush=True)
                errors += 1

            progress += 1

        update(status="completed", completed_at=datetime.now().isoformat(),
               phase="completed", progress=progress, errors=errors)

    except Exception as e:
        update(status="failed", error=str(e), completed_at=datetime.now().isoformat(), phase="failed")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ingest helpers
# ---------------------------------------------------------------------------

def _wait_for_stable(path: str, stable_secs: int = 5, timeout: int = 300) -> bool:
    """Poll until file size is stable for stable_secs consecutive seconds."""
    start      = time.time()
    prev_size  = -1
    stable_at  = None

    while time.time() - start < timeout:
        try:
            size = os.path.getsize(path)
        except OSError:
            time.sleep(1)
            continue

        if size == prev_size:
            if stable_at is None:
                stable_at = time.time()
            elif time.time() - stable_at >= stable_secs:
                return True
        else:
            stable_at = None

        prev_size = size
        time.sleep(1)

    return False


def _get_exif_date(filepath: str) -> datetime | None:
    """Return DateTimeOriginal from EXIF via exiftool. Returns None on failure."""
    try:
        result = subprocess.run(
            ["exiftool", "-DateTimeOriginal", "-j", "-d", "%Y%m%d-%H%M%S", filepath],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if not data:
            return None
        dt_str = data[0].get("DateTimeOriginal")
        if not dt_str:
            return None
        return datetime.strptime(dt_str, "%Y%m%d-%H%M%S")
    except Exception:
        return None


def _get_audio_tags(filepath: str) -> dict:
    """Return ID3/music tags via exiftool."""
    try:
        result = subprocess.run(
            ["exiftool", "-Artist", "-Album", "-j", filepath],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
        return data[0] if data else {}
    except Exception:
        return {}


def _detect_category(ext: str) -> str:
    """Detect category from file extension. Audio/Music determined separately via tags."""
    if ext in PHOTO_EXTS:
        return "Photos"
    if ext in VIDEO_EXTS:
        return "HomeVideos"
    if ext in AUDIO_EXTS:
        return "Audio/Recordings"  # may be upgraded to Audio/Music if tags present
    if ext in DOC_EXTS:
        return "Documents"
    return "Documents"


def _build_dated_name(dt: datetime | None, filepath: str, initials: str, ext: str) -> tuple[str, str]:
    """Return (date_str YYYYMMDD, proposed_filename). Falls back to ctime if no EXIF."""
    if dt is None:
        try:
            st = os.stat(filepath)
            ts = getattr(st, "st_birthtime", None) or st.st_ctime
            dt = datetime.fromtimestamp(ts)
        except Exception:
            dt = datetime.now()

    date_str = dt.strftime("%Y%m%d")
    time_str = dt.strftime("%H%M%S")
    suffix   = f"-{initials}" if initials else ""
    return date_str, f"{date_str}-{time_str}{suffix}{ext}"


def _build_archive_path(category: str, date_str: str, filename: str,
                        original_filename: str, audio_tags: dict) -> str:
    """Return the proposed absolute archive path for a file."""
    if category == "Photos":
        return os.path.join(ARCHIVE_PATH, "Photos", date_str, filename)
    if category == "HomeVideos":
        return os.path.join(ARCHIVE_PATH, "HomeVideos", date_str, filename)
    if category == "Audio/Recordings":
        return os.path.join(ARCHIVE_PATH, "Audio", "Recordings", date_str, filename)
    if category == "Audio/Music":
        artist = audio_tags.get("Artist") or "Unknown"
        album  = audio_tags.get("Album")  or "Unsorted"
        return os.path.join(ARCHIVE_PATH, "Audio", "Music", artist, album, original_filename)
    if category == "Art/Visual":
        return os.path.join(ARCHIVE_PATH, "Art", "Visual", "Unknown", filename)
    if category == "Art/Video":
        return os.path.join(ARCHIVE_PATH, "Art", "Video", "Unknown", filename)
    if category == "Documents":
        return os.path.join(ARCHIVE_PATH, "Documents", date_str, filename)
    return os.path.join(ARCHIVE_PATH, "Documents", date_str, filename)


def _unique_staging_path(base: str) -> str:
    """If base already exists on disk, append _2, _3, etc."""
    if not os.path.exists(base):
        return base
    root, ext = os.path.splitext(base)
    counter = 2
    while True:
        candidate = f"{root}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _prune_empty_dirs(file_path: str, stop_at: str) -> None:
    """Remove empty parent directories left behind after a file is moved out.
    Walks up from file_path's parent, removing dirs with os.rmdir (no-op if not empty),
    stopping at or above stop_at."""
    stop_at = os.path.realpath(stop_at)
    current = os.path.realpath(os.path.dirname(file_path))
    while current != stop_at and current.startswith(stop_at + os.sep):
        try:
            os.rmdir(current)  # only succeeds if directory is empty
        except OSError:
            break  # not empty or permission denied — stop climbing
        current = os.path.dirname(current)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def run_ingest(job_id: int, file_path: str, ingest_source: str, initials: str) -> None:
    conn = get_db()

    def update(**kw):
        kw["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        params = dict(kw)
        params["job_id"] = job_id
        conn.execute(text(f"UPDATE jobs SET {sets} WHERE id=:job_id"), params)
        conn.commit()

    try:
        update(status="running", started_at=datetime.now().isoformat(),
               phase="stabilizing", current_file=file_path)

        if not os.path.isfile(file_path):
            # Check if a parallel job already processed this file (moved it out of to_index/)
            already = conn.execute(
                text("SELECT id FROM file_inventory WHERE original_filename=:name AND status IN ('staged','archived')"),
                {"name": os.path.basename(file_path)}
            ).mappings().fetchone()
            if already:
                update(status="completed", phase="skipped",
                       completed_at=datetime.now().isoformat(),
                       error="Duplicate job — file already processed")
            else:
                update(status="failed", error=f"File not found: {file_path}",
                       completed_at=datetime.now().isoformat(), phase="failed")
            return

        # 1. Wait for write to stabilize
        if not _wait_for_stable(file_path):
            update(status="failed", error="File did not stabilize within timeout",
                   completed_at=datetime.now().isoformat(), phase="failed")
            return

        update(phase="hashing")

        # 2. Compute SHA256
        checksum = _sha256(file_path)
        if not checksum:
            update(status="failed", error="Failed to compute checksum",
                   completed_at=datetime.now().isoformat(), phase="failed")
            return

        # 3. Duplicate check against staged or archived files
        existing = conn.execute(
            text("SELECT id, filepath FROM file_inventory WHERE sha256=:sha256 AND status IN ('staged','archived')"),
            {"sha256": checksum}
        ).mappings().fetchone()
        if existing:
            dup_dir = os.path.join(INGEST_PATH, "duplicates")
            os.makedirs(dup_dir, exist_ok=True)
            dup_dest = _unique_staging_path(os.path.join(dup_dir, os.path.basename(file_path)))
            try:
                shutil.move(file_path, dup_dest)
                _prune_empty_dirs(file_path, INGEST_WATCH_PATH)
            except Exception as e:
                print(f"Warning: could not move duplicate to duplicates/: {e}", flush=True)

            # Record the duplicate
            conn.execute(text(_insert_or_ignore(
                "file_inventory",
                "filepath, filename, filesize, sha256, status, ingest_date, ingest_source, initials",
                ":filepath, :filename, :filesize, :sha256, 'duplicate', CURRENT_TIMESTAMP, :ingest_source, :initials"
            )), {
                "filepath": dup_dest,
                "filename": os.path.basename(file_path),
                "filesize": os.path.getsize(dup_dest) if os.path.exists(dup_dest) else 0,
                "sha256": checksum,
                "ingest_source": ingest_source,
                "initials": initials,
            })
            conn.commit()
            update(status="completed", phase="duplicate",
                   error=f"Duplicate of file #{existing['id']} ({existing['filepath']})",
                   completed_at=datetime.now().isoformat())
            print(f"Duplicate detected: {file_path} → duplicates/", flush=True)
            return

        update(phase="extracting")

        # 4. Extract EXIF date
        ext               = os.path.splitext(file_path)[1].lower()
        exif_dt           = _get_exif_date(file_path)
        original_filename = os.path.basename(file_path)

        # 5. Audio tag check (to distinguish Music from Recordings)
        audio_tags = {}
        if ext in AUDIO_EXTS:
            audio_tags = _get_audio_tags(file_path)

        # 6. Category and proposed archive path
        category = _detect_category(ext)
        if category == "Audio/Recordings" and audio_tags.get("Artist") and audio_tags.get("Album"):
            category = "Audio/Music"

        # Phone dumps get a date-based rename in the archive.
        # All other sources keep their original filename.
        if ingest_source == "phone_dump":
            date_str, prop_name = _build_dated_name(exif_dt, file_path, initials, ext)
        else:
            try:
                st = os.stat(file_path)
                ts = getattr(st, "st_birthtime", None) or st.st_ctime
                dt_folder = exif_dt or datetime.fromtimestamp(ts)
            except Exception:
                dt_folder = datetime.now()
            date_str  = dt_folder.strftime("%Y%m%d")
            prop_name = original_filename

        proposed_archive = _build_archive_path(category, date_str, prop_name, original_filename, audio_tags)

        # 7. Move file from to_index/ into to_archive/ (the review queue).
        #    Phone dumps: organised by category (timestamps make names unique).
        #    Hot-folder files: mirror the original folder structure exactly — no category prefix.
        if ingest_source == "phone_dump":
            staging_base = os.path.join(STAGING_PATH, category.replace("/", os.sep), prop_name)
        else:
            rel_dir = os.path.relpath(os.path.dirname(file_path), INGEST_WATCH_PATH)
            if rel_dir == "." or rel_dir.startswith(".."):
                rel_dir = ""
            staging_base = os.path.join(
                STAGING_PATH,
                *(rel_dir.split(os.sep) if rel_dir else []),
                prop_name,
            )
        staging_path = _unique_staging_path(staging_base)

        # Detect filename collision (suffix was added to make the path unique)
        collision = staging_path != staging_base

        # Mirror any collision suffix into the proposed archive path too
        if collision and category != "Audio/Music":
            base_root, _        = os.path.splitext(staging_base)
            stag_root, _        = os.path.splitext(staging_path)
            counter_suffix      = stag_root[len(base_root):]
            arch_root, arch_ext = os.path.splitext(proposed_archive)
            proposed_archive    = arch_root + counter_suffix + arch_ext

        update(phase="staging")
        os.makedirs(os.path.dirname(staging_path), exist_ok=True)
        shutil.move(file_path, staging_path)
        _prune_empty_dirs(file_path, INGEST_WATCH_PATH)
        if collision:
            print(f"[worker] filename collision — renamed to {os.path.basename(staging_path)}", flush=True)

        # 8. Insert record — staged_path is the to_archive/ location
        filesize = os.path.getsize(staging_path)
        cur = conn.execute(text("""
            INSERT INTO file_inventory
                (filepath, filename, filesize, sha256, status,
                 original_filename, ingest_source, initials, category,
                 staged_path, archive_path, ingest_date, collision)
            VALUES (:filepath, :filename, :filesize, :sha256, 'staged', :original_filename,
                    :ingest_source, :initials, :category,
                    :staged_path, :archive_path, CURRENT_TIMESTAMP, :collision)
        """), {
            "filepath": staging_path,
            "filename": original_filename,
            "filesize": filesize,
            "sha256": checksum,
            "original_filename": original_filename,
            "ingest_source": ingest_source,
            "initials": initials,
            "category": category,
            "staged_path": staging_path,
            "archive_path": proposed_archive,
            "collision": 1 if collision else 0,
        })
        conn.commit()
        file_id = cur.lastrowid

        # 9. Queue mediainfo
        if ext in MEDIA_EXTS:
            conn.execute(
                text("INSERT INTO jobs (type, params) VALUES ('mediainfo', :params)"),
                {"params": json.dumps({"file_ids": [file_id]})}
            )
            conn.commit()

        update(status="completed", completed_at=datetime.now().isoformat(),
               phase="completed", progress=1, total=1)
        print(f"Ingested {file_path} → {staging_path} (category={category})", flush=True)

    except Exception as e:
        update(status="failed", error=str(e), completed_at=datetime.now().isoformat(), phase="failed")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------

def run_promote(job_id: int, file_id: int, archive_path_override: str | None = None) -> None:
    conn = get_db()

    def update(**kw):
        kw["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k}=:{k}" for k in kw)
        params = dict(kw)
        params["job_id"] = job_id
        conn.execute(text(f"UPDATE jobs SET {sets} WHERE id=:job_id"), params)
        conn.commit()

    try:
        update(status="running", started_at=datetime.now().isoformat(), phase="promoting")

        row = conn.execute(
            text("SELECT staged_path, archive_path, sha256 FROM file_inventory WHERE id=:id"),
            {"id": file_id}
        ).mappings().fetchone()
        if not row:
            update(status="failed", error=f"File record not found: id={file_id}",
                   completed_at=datetime.now().isoformat(), phase="failed")
            return

        staged_path  = row["staged_path"]
        archive_path = archive_path_override or row["archive_path"]
        expected_sha = row["sha256"]

        if not staged_path or not os.path.isfile(staged_path):
            update(status="failed", error=f"Staged file not found on disk: {staged_path}",
                   completed_at=datetime.now().isoformat(), phase="failed")
            return

        # Create destination directory and copy
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        shutil.copy2(staged_path, archive_path)

        # Verify copy
        copy_sha = _sha256(archive_path)
        if copy_sha != expected_sha:
            try:
                os.unlink(archive_path)
            except Exception:
                pass
            update(status="failed",
                   error=f"Copy verification failed: expected={expected_sha} got={copy_sha}",
                   completed_at=datetime.now().isoformat(), phase="failed")
            return

        # Delete staged copy
        try:
            os.unlink(staged_path)
        except Exception as e:
            print(f"Warning: could not delete staged copy {staged_path}: {e}", flush=True)

        # Update DB record
        conn.execute(text("""
            UPDATE file_inventory
            SET status='archived', archived_date=CURRENT_TIMESTAMP,
                filepath=:filepath, archive_path=:archive_path, staged_path=NULL
            WHERE id=:id
        """), {"filepath": archive_path, "archive_path": archive_path, "id": file_id})
        conn.commit()

        update(status="completed", completed_at=datetime.now().isoformat(),
               phase="completed", progress=1, total=1)
        print(f"Promoted file #{file_id} → {archive_path}", flush=True)

    except Exception as e:
        update(status="failed", error=str(e), completed_at=datetime.now().isoformat(), phase="failed")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def claim_next_job():
    conn = get_db()
    try:
        row = conn.execute(
            text("SELECT * FROM jobs WHERE status='pending' ORDER BY created_at LIMIT 1")
        ).mappings().fetchone()
        if not row:
            return None
        conn.execute(
            text("""UPDATE jobs SET status='running', started_at=CURRENT_TIMESTAMP,
               updated_at=CURRENT_TIMESTAMP, phase='starting'
               WHERE id=:id AND status='pending'"""),
            {"id": row["id"]}
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def main():
    print(f"Worker started. DB={DB_URL} INGEST={INGEST_PATH} ARCHIVE={ARCHIVE_PATH}", flush=True)
    # Wait for the API to initialise the DB (depends_on only waits for container start)
    while True:
        try:
            conn = get_db()
            conn.execute(text("SELECT 1 FROM jobs LIMIT 1"))
            conn.close()
            break
        except Exception:
            print("Waiting for DB to be ready...", flush=True)
            time.sleep(2)
    while True:
        job = claim_next_job()
        if job:
            params = json.loads(job.get("params") or "{}")
            jtype  = job["type"]
            jid    = job["id"]
            print(f"Job {jid} ({jtype}): {params}", flush=True)

            if jtype == "scan":
                run_scan(jid, params.get("scan_path", SCAN_PATH), int(params.get("workers", 4)))
            elif jtype == "mediainfo":
                run_mediainfo(jid, params.get("file_ids", []))
            elif jtype == "workflow":
                run_workflow(jid, params.get("workflow", ""), params.get("file_ids", []))
            elif jtype == "ingest":
                run_ingest(jid,
                           params.get("file_path", ""),
                           params.get("ingest_source", "manual"),
                           params.get("initials", ""))
            elif jtype == "promote":
                run_promote(jid,
                            params.get("file_id"),
                            params.get("archive_path"))
            else:
                print(f"Unknown job type: {jtype}", flush=True)

            print(f"Job {jid} done", flush=True)
        else:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
