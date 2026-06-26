import mimetypes
import os
import re
import hashlib
import sqlite3
import subprocess
import tempfile
import threading
import concurrent.futures
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/data/inventory.db")
SCAN_PATH = os.environ.get("SCAN_PATH", "/scan")

app = FastAPI(title="Digital Asset Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Scan state (in-process, reset on restart)
# ---------------------------------------------------------------------------

scan_state: dict = {
    "running": False,
    "phase": "idle",       # "discovering" | "scanning" | "stopping" | "idle"
    "scan_path": None,
    "progress": 0,
    "total": 0,
    "current_file": "",
    "errors": 0,
    "start_time": None,
    "end_time": None,
    "error": None,
    "cancelled": False,
}

cancel_event = threading.Event()

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
    # Migration: add marked_for_deletion if the column doesn't exist yet
    try:
        conn.execute("ALTER TABLE file_inventory ADD COLUMN marked_for_deletion INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # already exists
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
    return {"status": "ok", "db_path": DB_PATH, "scan_path": SCAN_PATH}

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

@app.get("/api/files")
def get_files(
    limit: int = 100,
    offset: int = 0,
    search: Optional[str] = None,
    duplicates_only: bool = False,
    marked_only: bool = False,
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

        where = " AND ".join(conditions)
        total = conn.execute(
            f"SELECT COUNT(*) FROM file_inventory WHERE {where}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT * FROM file_inventory WHERE {where} ORDER BY scan_date DESC LIMIT ? OFFSET ?",
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
# Scan
# ---------------------------------------------------------------------------

def _sha256(path: str) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                if cancel_event.is_set():
                    return None
                h.update(chunk)
        return h.hexdigest()
    except (IOError, OSError):
        return None


def _run_scan(scan_path: str, workers: int) -> None:
    global scan_state
    cancel_event.clear()
    scan_state.update({
        "running": True,
        "phase": "discovering",
        "scan_path": scan_path,
        "progress": 0,
        "total": 0,
        "current_file": "",
        "errors": 0,
        "start_time": datetime.now().isoformat(),
        "end_time": None,
        "error": None,
        "cancelled": False,
    })

    try:
        # Phase 1: walk the tree, updating total per directory so the UI
        # shows file count growing instead of a blank spinner.
        all_files: list = []
        for root, _, files in os.walk(scan_path):
            if cancel_event.is_set():
                break
            for fname in files:
                all_files.append(os.path.join(root, fname))
            scan_state["total"] = len(all_files)

        if cancel_event.is_set():
            scan_state["cancelled"] = True
            return

        # Phase 2: compute checksums in parallel
        scan_state["phase"] = "scanning"

        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")

        def process(filepath: str):
            if cancel_event.is_set():
                return None
            try:
                st = os.stat(filepath)
                return {
                    "filepath": filepath,
                    "filename": os.path.basename(filepath),
                    "filesize": st.st_size,
                    "sha256":   _sha256(filepath),
                }
            except Exception:
                return None

        batch: list = []
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futs = {ex.submit(process, f): f for f in all_files}
            for fut in concurrent.futures.as_completed(futs):
                if cancel_event.is_set():
                    break
                scan_state["progress"] += 1
                scan_state["current_file"] = futs[fut]
                result = fut.result()
                if result:
                    batch.append(result)
                else:
                    scan_state["errors"] += 1

                if len(batch) >= 100:
                    conn.executemany("""
                        INSERT OR REPLACE INTO file_inventory
                            (filepath, filename, filesize, sha256, scan_date)
                        VALUES (:filepath, :filename, :filesize, :sha256, CURRENT_TIMESTAMP)
                    """, batch)
                    conn.commit()
                    batch.clear()
        finally:
            ex.shutdown(wait=True, cancel_futures=True)

        if batch and not cancel_event.is_set():
            conn.executemany("""
                INSERT OR REPLACE INTO file_inventory
                    (filepath, filename, filesize, sha256, scan_date)
                VALUES (:filepath, :filename, :filesize, :sha256, CURRENT_TIMESTAMP)
            """, batch)
            conn.commit()

        conn.close()
    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        scan_state["running"]    = False
        scan_state["phase"]      = "idle"
        scan_state["end_time"]   = datetime.now().isoformat()
        if cancel_event.is_set():
            scan_state["cancelled"] = True


@app.post("/api/scan/start")
def start_scan(scan_path: Optional[str] = None, workers: int = 4):
    if scan_state["running"]:
        return {"error": "Scan already in progress"}
    path = scan_path or SCAN_PATH
    threading.Thread(target=_run_scan, args=(path, workers), daemon=True).start()
    return {"status": "started", "path": path}


@app.post("/api/scan/cancel")
def cancel_scan():
    if not scan_state["running"]:
        return {"error": "No scan in progress"}
    scan_state["phase"] = "stopping"
    cancel_event.set()
    return {"status": "cancelling"}


@app.get("/api/scan/status")
def scan_status():
    return dict(scan_state)
