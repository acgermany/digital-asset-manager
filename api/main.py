import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

DB_PATH      = os.environ.get("DB_PATH",      "/data/inventory.db")
SCAN_PATH    = os.environ.get("SCAN_PATH",    "/scan")
INGEST_PATH  = os.environ.get("INGEST_PATH",  "/nas/ingest")
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "/nas/archive")
APP_VERSION  = os.environ.get("APP_VERSION",  "dev")

app = FastAPI(title="Digital Asset Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL,
            filename TEXT NOT NULL,
            filesize INTEGER NOT NULL,
            sha256 TEXT,
            scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(filepath, sha256)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sha256   ON file_inventory(sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_filepath ON file_inventory(filepath)")
    # Migrations: add columns when they don't exist yet
    _migrations = [
        "marked_for_deletion INTEGER DEFAULT 0",
        "status TEXT DEFAULT 'scanned'",
        "original_filename TEXT",
        "ingest_source TEXT",
        "initials TEXT",
        "category TEXT",
        "staged_path TEXT",
        "archive_path TEXT",
        "related_file_id INTEGER",
        "ingest_date TIMESTAMP",
        "archived_date TIMESTAMP",
        "collision INTEGER DEFAULT 0",
    ]
    for col_def in _migrations:
        try:
            conn.execute(f"ALTER TABLE file_inventory ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON file_inventory(status)")  # already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS segments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id    INTEGER NOT NULL,
            in_point   REAL    NOT NULL,
            out_point  REAL    NOT NULL,
            label      TEXT,
            metadata   TEXT    DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_file ON segments(file_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_info (
            file_id           INTEGER PRIMARY KEY REFERENCES file_inventory(id),
            duration          REAL,
            format            TEXT,
            video_codec       TEXT,
            width             INTEGER,
            height            INTEGER,
            frame_rate        REAL,
            timecode          TEXT,
            bit_rate          INTEGER,
            audio_codec       TEXT,
            audio_channels    INTEGER,
            audio_sample_rate INTEGER,
            raw_json          TEXT,
            scanned_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watcher_sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            path        TEXT    NOT NULL,
            initials    TEXT    DEFAULT '',
            source_type TEXT    DEFAULT 'hot_folder',
            enabled     INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            type         TEXT NOT NULL DEFAULT 'scan',
            status       TEXT NOT NULL DEFAULT 'pending',
            params       TEXT NOT NULL DEFAULT '{}',
            progress     INTEGER DEFAULT 0,
            total        INTEGER DEFAULT 0,
            phase        TEXT DEFAULT 'pending',
            current_file TEXT DEFAULT '',
            errors       INTEGER DEFAULT 0,
            error        TEXT,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at   TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


@app.on_event("startup")
async def startup() -> None:
    init_db()

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION, "db_path": DB_PATH, "scan_path": SCAN_PATH}

# ---------------------------------------------------------------------------
# Directory browser
# ---------------------------------------------------------------------------

@app.get("/api/browse")
def browse(path: Optional[str] = None):
    """List subdirectories at path, restricted to the SCAN_PATH tree."""
    root = os.path.realpath(SCAN_PATH)
    target = os.path.realpath(path) if path else root

    if not target.startswith(root):
        return {"error": "Path is outside the scan root", "path": root, "parent": None, "dirs": []}

    try:
        entries = []
        for name in sorted(os.listdir(target)):
            if name.startswith('.') or name.startswith('$') or name.startswith('#'):
                continue
            full = os.path.join(target, name)
            if os.path.isdir(full):
                entries.append({"name": name, "path": full})
        parent = os.path.dirname(target) if target != root else None
        if parent and not os.path.realpath(parent).startswith(root):
            parent = None
        return {"path": target, "parent": parent, "dirs": entries}
    except PermissionError:
        return {"error": "Permission denied", "path": target, "parent": None, "dirs": []}

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def get_stats():
    conn = get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) as n, COALESCE(SUM(filesize), 0) as s FROM file_inventory"
        ).fetchone()

        unique = conn.execute(
            "SELECT COUNT(DISTINCT sha256) FROM file_inventory WHERE sha256 IS NOT NULL"
        ).fetchone()[0]

        dup = conn.execute("""
            SELECT COUNT(*) as n, COALESCE(SUM(filesize), 0) as s
            FROM file_inventory
            WHERE sha256 IN (
                SELECT sha256 FROM file_inventory
                WHERE sha256 IS NOT NULL
                GROUP BY sha256 HAVING COUNT(DISTINCT filepath) > 1
            )
        """).fetchone()

        wasted = conn.execute("""
            SELECT COALESCE(SUM(total_size - min_size), 0)
            FROM (
                SELECT SUM(filesize) AS total_size, MIN(filesize) AS min_size
                FROM file_inventory
                WHERE sha256 IS NOT NULL
                GROUP BY sha256
                HAVING COUNT(DISTINCT filepath) > 1
            )
        """).fetchone()[0]

        marked = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(filesize), 0) FROM file_inventory WHERE marked_for_deletion = 1"
        ).fetchone()

        return {
            "total_files":        total["n"],
            "total_size":         total["s"],
            "unique_files":       unique,
            "duplicate_files":    dup["n"],
            "duplicate_size":     dup["s"],
            "wasted_space":       wasted,
            "marked_for_deletion": marked[0],
            "marked_size":        marked[1],
        }
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

def _score_file(file: dict) -> dict:
    """
    Score a file; higher = better keep candidate.
    Mirrors the logic in DataClean.ipynb.
    """
    score = 0.0
    path = file["filepath"].lower()
    stem = os.path.splitext(file["filename"])[0].lower()

    bad_folders = ["unknown", "#recycle", "unorganized", "unsorted", "temp", "tmp"]
    for bad in bad_folders:
        if f"/{bad}/" in path or path.endswith(f"/{bad}"):
            score -= 50

    dupe_patterns = [r"\s*\(\d+\)$", r"\s+\d+\.$", r"_\d+$", r" copy$", r"_copy$"]
    for pat in dupe_patterns:
        if re.search(pat, stem):
            score -= 100

    score -= len(file["filepath"]) * 0.01   # shorter path = better organised

    return {**file, "score": round(score, 2)}


@app.get("/api/duplicates")
def get_duplicates(limit: int = 50, offset: int = 0):
    conn = get_db()
    try:
        total = conn.execute("""
            SELECT COUNT(DISTINCT sha256) FROM file_inventory
            WHERE sha256 IN (
                SELECT sha256 FROM file_inventory
                WHERE sha256 IS NOT NULL
                GROUP BY sha256 HAVING COUNT(DISTINCT filepath) > 1
            )
        """).fetchone()[0]

        group_rows = conn.execute("""
            SELECT sha256,
                   COUNT(DISTINCT filepath) AS count,
                   SUM(filesize)            AS total_size
            FROM file_inventory
            WHERE sha256 IN (
                SELECT sha256 FROM file_inventory
                WHERE sha256 IS NOT NULL
                GROUP BY sha256 HAVING COUNT(DISTINCT filepath) > 1
            )
            GROUP BY sha256
            ORDER BY total_size DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()

        groups = []
        for g in group_rows:
            raw_files = [
                dict(r) for r in conn.execute("""
                    SELECT id, filepath, filename, filesize, scan_date, marked_for_deletion
                    FROM file_inventory
                    WHERE sha256 = ?
                    GROUP BY filepath
                    ORDER BY filesize DESC
                """, (g["sha256"],)).fetchall()
            ]
            scored = sorted(
                [_score_file(f) for f in raw_files],
                key=lambda x: x["score"],
                reverse=True,
            )
            # Flag the top scorer as suggested_keep
            if scored:
                scored[0]["suggested_keep"] = True
                for f in scored[1:]:
                    f["suggested_keep"] = False

            groups.append({
                "sha256":     g["sha256"],
                "count":      g["count"],
                "total_size": g["total_size"],
                "files":      scored,
            })

        return {"groups": groups, "total": total, "offset": offset, "limit": limit}
    finally:
        conn.close()


@app.post("/api/duplicates/{sha256}/auto-mark")
def auto_mark_duplicates(sha256: str):
    """Mark all but the highest-scored file in this duplicate group for deletion."""
    conn = get_db()
    try:
        raw = [
            dict(r) for r in conn.execute(
                "SELECT id, filepath, filename, filesize, marked_for_deletion FROM file_inventory WHERE sha256 = ? GROUP BY filepath",
                (sha256,)
            ).fetchall()
        ]
        if len(raw) <= 1:
            return {"error": "No duplicates for this hash"}

        scored = sorted([_score_file(f) for f in raw], key=lambda x: x["score"], reverse=True)
        keep_id  = scored[0]["id"]
        mark_ids = [f["id"] for f in scored[1:]]

        conn.execute("UPDATE file_inventory SET marked_for_deletion = 0 WHERE id = ?", (keep_id,))
        conn.executemany(
            "UPDATE file_inventory SET marked_for_deletion = 1 WHERE id = ?",
            [(mid,) for mid in mark_ids],
        )
        conn.commit()
        return {"kept": keep_id, "marked": mark_ids}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

_SORTABLE_COLS = {"filename", "filepath", "filesize", "scan_date", "marked_for_deletion", "sha256", "id"}

@app.get("/api/files")
def get_files(
    limit: int = 100,
    offset: int = 0,
    search: Optional[str] = None,
    duplicates_only: bool = False,
    marked_only: bool = False,
    media_type: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
):
    conn = get_db()
    try:
        conditions = ["1=1"]
        params: list = []

        if search:
            conditions.append("filepath LIKE ?")
            params.append(f"%{search}%")

        if duplicates_only:
            conditions.append("""
                sha256 IN (
                    SELECT sha256 FROM file_inventory
                    WHERE sha256 IS NOT NULL
                    GROUP BY sha256 HAVING COUNT(DISTINCT filepath) > 1
                )
            """)

        if marked_only:
            conditions.append("marked_for_deletion = 1")

        if media_type and media_type in _TYPE_EXTS:
            exts = _TYPE_EXTS[media_type]
            placeholders = " OR ".join("LOWER(filename) LIKE ?" for _ in exts)
            conditions.append(f"({placeholders})")
            params.extend(f"%{e}" for e in exts)
        elif media_type == 'other':
            all_known = [e for exts in _TYPE_EXTS.values() for e in exts]
            placeholders = " AND ".join("LOWER(filename) NOT LIKE ?" for _ in all_known)
            conditions.append(f"({placeholders})")
            params.extend(f"%{e}" for e in all_known)

        where = " AND ".join(conditions)
        total = conn.execute(
            f"SELECT COUNT(*) FROM file_inventory WHERE {where}", params
        ).fetchone()[0]

        order_col = sort_by if sort_by in _SORTABLE_COLS else "scan_date"
        order_dir = "ASC" if sort_dir == "asc" else "DESC"
        rows = conn.execute(
            f"SELECT * FROM file_inventory WHERE {where} ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return {"files": [dict(r) for r in rows], "total": total, "offset": offset, "limit": limit}
    finally:
        conn.close()


@app.post("/api/files/{file_id}/mark")
def mark_file(file_id: int, mark: bool = True):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE file_inventory SET marked_for_deletion = ? WHERE id = ?",
            (1 if mark else 0, file_id),
        )
        conn.commit()
        return {"success": True, "file_id": file_id, "marked": mark}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------

class SegmentCreate(BaseModel):
    in_point:  float
    out_point: float
    label:     Optional[str] = None
    metadata:  Optional[str] = "{}"

class SegmentUpdate(BaseModel):
    in_point:  Optional[float] = None
    out_point: Optional[float] = None
    label:     Optional[str]   = None
    metadata:  Optional[str]   = None


@app.get("/api/files/{file_id}/segments")
def get_segments(file_id: int):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM segments WHERE file_id = ? ORDER BY in_point",
            (file_id,)
        ).fetchall()
        return {"segments": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/files/{file_id}/segments")
def create_segment(file_id: int, body: SegmentCreate):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO segments (file_id, in_point, out_point, label, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (file_id, body.in_point, body.out_point, body.label, body.metadata or "{}"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM segments WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.patch("/api/segments/{segment_id}")
def update_segment(segment_id: int, body: SegmentUpdate):
    conn = get_db()
    try:
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="Nothing to update")
        updates["updated_at"] = "CURRENT_TIMESTAMP"
        set_clause = ", ".join(
            f"{k} = CURRENT_TIMESTAMP" if v == "CURRENT_TIMESTAMP" else f"{k} = ?"
            for k, v in updates.items()
        )
        params = [v for v in updates.values() if v != "CURRENT_TIMESTAMP"] + [segment_id]
        conn.execute(f"UPDATE segments SET {set_clause} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Segment not found")
        return dict(row)
    finally:
        conn.close()


@app.delete("/api/segments/{segment_id}")
def delete_segment(segment_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# File content / preview
# ---------------------------------------------------------------------------

# All audio extensions — always transcoded to MP3 via FFmpeg.
# Serving audio directly is unreliable: WAV can be 24-bit/32-bit float or
# non-PCM, FLAC/OGG/Opus fail on Safari, etc. FFmpeg normalises everything.
_ALL_AUDIO = {
    '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus',
    '.aiff', '.aif', '.wma', '.alac', '.ape', '.amr', '.m4b',
    '.aax', '.aa', '.ra', '.rm', '.caf', '.dsf', '.dff',
}

_MIME = {
    '.mp3':  'audio/mpeg',   '.m4a': 'audio/mp4',
    '.aac':  'audio/aac',    '.wav': 'audio/wav',
    '.mp4':  'video/mp4',    '.m4v': 'video/mp4',
    '.mov':  'video/quicktime', '.webm': 'video/webm',
    '.avi':  'video/x-msvideo', '.mkv': 'video/x-matroska',
    '.wmv':  'video/x-ms-wmv',  '.flv': 'video/x-flv',
    '.ts':   'video/mp2t',
}

_TYPE_EXTS = {
    'video': ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.wmv', '.flv', '.mpg', '.mpeg', '.ts'],
    'audio': ['.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.wma', '.opus', '.aiff', '.aif'],
    'image': ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.tif', '.ico', '.avif'],
    'pdf':   ['.pdf'],
    'text':  ['.txt', '.md', '.csv', '.json', '.xml', '.log', '.yml', '.yaml', '.ini', '.conf', '.sh', '.py', '.js'],
}


def _transcode_to_mp3_file(filepath: str) -> str:
    """Transcode audio to MP3 into a temp file; returns path. Raises on failure."""
    fd, tmp = tempfile.mkstemp(suffix='.mp3')
    os.close(fd)
    result = subprocess.run(
        [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-i', filepath,
            '-f', 'mp3', '-codec:a', 'libmp3lame', '-b:a', '192k',
            '-y', tmp,
        ],
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        os.unlink(tmp)
        err = result.stderr.decode('utf-8', errors='replace').strip()
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {err}")
    return tmp


@app.get("/api/files/{file_id}/content")
def get_file_content(file_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT filepath, filename FROM file_inventory WHERE id = ?", (file_id,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="File not found in database")

    filepath = row["filepath"]
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found on disk")

    ext = os.path.splitext(filepath)[1].lower()

    # All audio goes through FFmpeg → MP3 for universal browser compatibility.
    # FileResponse (not StreamingResponse) is required: it sets Accept-Ranges: bytes
    # so Safari can issue range requests, which it requires for all media playback.
    if ext in _ALL_AUDIO:
        tmp = _transcode_to_mp3_file(filepath)
        return FileResponse(
            tmp,
            media_type='audio/mpeg',
            filename=os.path.splitext(row["filename"])[0] + '.mp3',
            background=BackgroundTask(os.unlink, tmp),
        )

    mime_type = _MIME.get(ext) or mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return FileResponse(filepath, media_type=mime_type, filename=row["filename"])

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobCreate(BaseModel):
    type: str = "scan"
    params: dict = {}

WORKFLOWS_PATH = os.environ.get("WORKFLOWS_PATH", "/workflows")

@app.post("/api/jobs")
def create_job(body: JobCreate):
    if body.type not in ("scan", "mediainfo", "workflow", "ingest", "promote"):
        raise HTTPException(status_code=400, detail=f"Unknown job type: {body.type}")
    conn = get_db()
    try:
        if body.type == "scan":
            active = conn.execute(
                "SELECT id FROM jobs WHERE type='scan' AND status IN ('pending','running') LIMIT 1"
            ).fetchone()
            if active:
                raise HTTPException(status_code=409, detail="A scan is already running or pending")
        cur = conn.execute(
            "INSERT INTO jobs (type, params) VALUES (?, ?)",
            (body.type, json.dumps(body.params))
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


@app.get("/api/jobs")
def list_jobs(limit: int = 20):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        jobs = [dict(r) for r in rows]

        # Enrich mediainfo jobs with filenames so the UI can display them
        file_ids_needed: set = set()
        for job in jobs:
            if job["type"] == "mediainfo":
                try:
                    p = json.loads(job["params"]) if isinstance(job["params"], str) else {}
                    for fid in p.get("file_ids", []):
                        file_ids_needed.add(fid)
                except Exception:
                    pass

        if file_ids_needed:
            placeholders = ",".join("?" * len(file_ids_needed))
            fname_rows = conn.execute(
                f"SELECT id, filename FROM file_inventory WHERE id IN ({placeholders})",
                list(file_ids_needed),
            ).fetchall()
            fname_map = {r["id"]: r["filename"] for r in fname_rows}
            for job in jobs:
                if job["type"] == "mediainfo":
                    try:
                        p = json.loads(job["params"]) if isinstance(job["params"], str) else {}
                        job["_filenames"] = [
                            fname_map[fid] for fid in p.get("file_ids", []) if fid in fname_map
                        ]
                    except Exception:
                        pass

        return {"jobs": jobs}
    finally:
        conn.close()


@app.get("/api/jobs/active")
def get_active_job():
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('pending','running') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(row)
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        if row["status"] not in ("pending", "running"):
            raise HTTPException(status_code=400, detail=f"Job cannot be cancelled (status: {row['status']})")
        conn.execute(
            "UPDATE jobs SET status='cancelling', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,)
        )
        conn.commit()
        return {"status": "cancelling"}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Media info
# ---------------------------------------------------------------------------

@app.get("/api/files/{file_id}/mediainfo")
def get_mediainfo(file_id: int):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM media_info WHERE file_id=?", (file_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()

@app.post("/api/files/{file_id}/mediainfo/scan")
def queue_mediainfo(file_id: int):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM file_inventory WHERE id=?", (file_id,)).fetchone():
            raise HTTPException(status_code=404, detail="File not found")
        cur = conn.execute(
            "INSERT INTO jobs (type, params) VALUES ('mediainfo', ?)",
            (json.dumps({"file_ids": [file_id]}),)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

@app.get("/api/workflows")
def list_workflows():
    if not os.path.isdir(WORKFLOWS_PATH):
        return {"workflows": []}
    scripts = []
    for name in sorted(os.listdir(WORKFLOWS_PATH)):
        full = os.path.join(WORKFLOWS_PATH, name)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            scripts.append({"name": name, "path": full})
    return {"workflows": scripts}

@app.post("/api/workflows/run")
def run_workflow(body: dict):
    workflow = body.get("workflow")
    file_ids = body.get("file_ids", [])
    if not workflow:
        raise HTTPException(status_code=400, detail="workflow is required")
    if not file_ids:
        raise HTTPException(status_code=400, detail="file_ids is required")
    script = os.path.join(WORKFLOWS_PATH, workflow)
    if not os.path.isfile(script) or not os.access(script, os.X_OK):
        raise HTTPException(status_code=404, detail=f"Workflow not found: {workflow}")
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO jobs (type, params) VALUES ('workflow', ?)",
            (json.dumps({"workflow": workflow, "file_ids": file_ids}),)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

class IngestTrigger(BaseModel):
    file_path:     str
    ingest_source: str = "manual"
    initials:      str = ""

@app.post("/api/ingest")
def trigger_ingest(body: IngestTrigger):
    """Manually trigger an ingest job for a file already on disk."""
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO jobs (type, params) VALUES ('ingest', ?)",
            (json.dumps({
                "file_path":     body.file_path,
                "ingest_source": body.ingest_source,
                "initials":      body.initials,
            }),)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()

@app.get("/api/ingest/stats")
def ingest_stats():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT status, COUNT(*) as n
            FROM file_inventory
            WHERE status IN ('staged','rejected','duplicate','archived')
            GROUP BY status
        """).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        return {
            "staged":    counts.get("staged", 0),
            "rejected":  counts.get("rejected", 0),
            "duplicate": counts.get("duplicate", 0),
            "archived":  counts.get("archived", 0),
        }
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Staged review
# ---------------------------------------------------------------------------

class PathOverride(BaseModel):
    path: str

class CategoryOverride(BaseModel):
    category: str

VALID_CATEGORIES = {
    "Photos", "HomeVideos",
    "Audio/Recordings", "Audio/Music",
    "Art/Visual", "Art/Video",
    "Documents",
}

@app.get("/api/staged/approve-all")
def approve_all_staged_preflight():
    """Return staged files grouped by conflict status (for UI dry-run)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, archive_path FROM file_inventory WHERE status='staged'"
        ).fetchall()
        paths = [r["archive_path"] for r in rows]
        conflicts = len(paths) != len(set(paths))
        return {"count": len(rows), "has_conflicts": conflicts}
    finally:
        conn.close()

@app.post("/api/staged/approve-all")
def approve_all_staged():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, archive_path FROM file_inventory WHERE status='staged'"
        ).fetchall()
        if not rows:
            return {"queued": 0}
        paths = [r["archive_path"] for r in rows]
        if len(paths) != len(set(paths)):
            raise HTTPException(status_code=409, detail="Archive path conflicts exist — review individually")
        job_ids = []
        for row in rows:
            cur = conn.execute(
                "INSERT INTO jobs (type, params) VALUES ('promote', ?)",
                (json.dumps({"file_id": row["id"], "archive_path": row["archive_path"]}),)
            )
            job_ids.append(cur.lastrowid)
        conn.commit()
        return {"queued": len(job_ids), "job_ids": job_ids}
    finally:
        conn.close()

@app.get("/api/staged")
def list_staged(limit: int = 50, offset: int = 0):
    conn = get_db()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM file_inventory WHERE status='staged'"
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT * FROM file_inventory WHERE status='staged'
               ORDER BY ingest_date DESC LIMIT ? OFFSET ?""",
            (limit, offset)
        ).fetchall()
        return {"files": [dict(r) for r in rows], "total": total, "offset": offset, "limit": limit}
    finally:
        conn.close()

@app.get("/api/staged/{file_id}")
def get_staged(file_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM file_inventory WHERE id=? AND status='staged'", (file_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Staged file not found")
        return dict(row)
    finally:
        conn.close()

@app.patch("/api/staged/{file_id}/path")
def override_staged_path(file_id: int, body: PathOverride):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM file_inventory WHERE id=? AND status='staged'", (file_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Staged file not found")
        conn.execute(
            "UPDATE file_inventory SET archive_path=? WHERE id=?",
            (body.path, file_id)
        )
        conn.commit()
        return {"success": True, "archive_path": body.path}
    finally:
        conn.close()

@app.patch("/api/staged/{file_id}/category")
def override_staged_category(file_id: int, body: CategoryOverride):
    if body.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Invalid category: {body.category}")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM file_inventory WHERE id=? AND status='staged'", (file_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Staged file not found")
        conn.execute(
            "UPDATE file_inventory SET category=? WHERE id=?",
            (body.category, file_id)
        )
        conn.commit()
        return {"success": True, "category": body.category}
    finally:
        conn.close()

@app.post("/api/staged/{file_id}/approve")
def approve_staged(file_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT staged_path, archive_path FROM file_inventory WHERE id=? AND status='staged'",
            (file_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Staged file not found")
        cur = conn.execute(
            "INSERT INTO jobs (type, params) VALUES ('promote', ?)",
            (json.dumps({"file_id": file_id, "archive_path": row["archive_path"]}),)
        )
        conn.commit()
        return {"job_id": cur.lastrowid}
    finally:
        conn.close()

@app.post("/api/staged/{file_id}/reject")
def reject_staged(file_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT staged_path, filename FROM file_inventory WHERE id=? AND status='staged'",
            (file_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Staged file not found")

        staged_path = row["staged_path"]
        if staged_path and os.path.isfile(staged_path):
            rejected_dir = os.path.join(INGEST_PATH, "rejected")
            os.makedirs(rejected_dir, exist_ok=True)
            dest = os.path.join(rejected_dir, os.path.basename(staged_path))
            # Avoid overwriting existing rejected file with the same name
            if os.path.exists(dest):
                base, ext = os.path.splitext(dest)
                i = 2
                while os.path.exists(f"{base}_{i}{ext}"):
                    i += 1
                dest = f"{base}_{i}{ext}"
            os.rename(staged_path, dest)
            conn.execute(
                "UPDATE file_inventory SET status='rejected', staged_path=?, filepath=? WHERE id=?",
                (dest, dest, file_id)
            )
        else:
            conn.execute(
                "UPDATE file_inventory SET status='rejected' WHERE id=?",
                (file_id,)
            )
        conn.commit()
        return {"success": True}
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Watcher sources
# ---------------------------------------------------------------------------

class WatcherSourceToggle(BaseModel):
    enabled: bool

@app.get("/api/watcher/sources")
def list_watcher_sources():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM watcher_sources ORDER BY source_type, name"
        ).fetchall()
        return {"sources": [dict(r) for r in rows]}
    finally:
        conn.close()

@app.patch("/api/watcher/sources/{source_id}")
def toggle_watcher_source(source_id: int, body: WatcherSourceToggle):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM watcher_sources WHERE id=?", (source_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Watcher source not found")
        conn.execute(
            "UPDATE watcher_sources SET enabled=? WHERE id=?",
            (1 if body.enabled else 0, source_id)
        )
        conn.commit()
        return {"success": True, "enabled": body.enabled}
    finally:
        conn.close()
