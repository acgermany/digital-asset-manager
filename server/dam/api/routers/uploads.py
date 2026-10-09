"""Browser uploads into the hot folder: resumable and chunked, so large videos survive
network hiccups. Finished files go through the normal ingest pipeline."""
import os
import re
import secrets
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...core import events, ingest as ingest_ops, locations, settings_store
from ...core.files import unique_path
from ...db import get_session
from ...models import Upload
from .auth import client_id

router = APIRouter()

CHUNK = 16 * 1024 * 1024          # what the browser sends per request
MAX_CHUNK = 64 * 1024 * 1024      # what we accept per request
STAGING = ".dam-uploads"          # hidden: the watcher ignores dot-folders
FREE_MARGIN = 1024 ** 3           # keep 1 GB free after an upload


def _clean_part(part: str) -> str:
    part = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", part).strip().strip(".")
    return part[:120]


def _clean_dir(rel: str) -> str:
    parts = [_clean_part(p) for p in (rel or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and p not in (".", "..") and not p.startswith(".")]
    return "/".join(parts[:10])


def _hotfolder(session: Session) -> str:
    hot = settings_store.get(session, "hotfolder")
    if not hot or not locations.is_dir(hot):
        raise HTTPException(400, "Set a hot folder in Configure first; uploads are delivered there")
    if not os.access(hot, os.W_OK):
        raise HTTPException(400, "The hot folder is not writable")
    return hot


def _dict(u: Upload) -> dict:
    return {"id": u.id, "filename": u.filename, "provenance": u.provenance, "relative_dir": u.relative_dir,
            "size": u.size, "received": u.received, "status": u.status, "ingest_id": u.ingest_id,
            "error": u.error, "chunk_size": CHUNK}


class StartBody(BaseModel):
    filename: str
    size: int
    provenance: Optional[str] = None
    relative_dir: Optional[str] = None
    last_modified: Optional[float] = None   # milliseconds since epoch, from the browser


@router.post("/uploads")
def start(body: StartBody, request: Request, session: Session = Depends(get_session)):
    hot = _hotfolder(session)
    name = _clean_part(os.path.basename(body.filename.replace("\\", "/")))
    if not name:
        raise HTTPException(400, "Invalid file name")
    if body.size <= 0:
        raise HTTPException(400, f"{name} is empty (0 bytes)")
    free = shutil.disk_usage(hot).free
    if body.size + FREE_MARGIN > free:
        raise HTTPException(507, f"Not enough space in the hot folder for {name} "
                                 f"({body.size / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free)")
    staging = os.path.join(hot, STAGING)
    os.makedirs(staging, exist_ok=True)
    upload_id = secrets.token_hex(12)
    temp = os.path.join(staging, f"{upload_id}.part")
    open(temp, "wb").close()
    u = Upload(id=upload_id, filename=name, size=body.size, received=0,
               provenance=_clean_part(body.provenance or "") or "Uploads",
               relative_dir=_clean_dir(body.relative_dir or ""),
               last_modified=(body.last_modified / 1000.0) if body.last_modified else None,
               status="uploading", temp_path=temp, client=client_id(request))
    session.add(u)
    session.commit()
    return _dict(u)


def _get(session: Session, upload_id: str) -> Upload:
    u = session.get(Upload, upload_id)
    if not u:
        raise HTTPException(404, "Upload not found")
    return u


@router.get("/uploads/{upload_id}")
def status(upload_id: str, session: Session = Depends(get_session)):
    return _dict(_get(session, upload_id))


@router.put("/uploads/{upload_id}")
async def chunk(upload_id: str, offset: int, request: Request, session: Session = Depends(get_session)):
    u = _get(session, upload_id)
    if u.status != "uploading":
        raise HTTPException(409, f"Upload is {u.status}")
    if offset != u.received:
        # Out of step (a retried chunk that already landed, or a resume): tell the browser where we are.
        raise HTTPException(409, detail={"message": "Offset mismatch", "received": u.received})
    data = bytearray()
    async for piece in request.stream():
        data.extend(piece)
        if len(data) > MAX_CHUNK:
            raise HTTPException(413, "Chunk too large")
    if u.received + len(data) > u.size:
        raise HTTPException(400, "More data than the declared file size")
    with open(u.temp_path, "r+b") as f:
        f.seek(offset)
        f.write(data)
    u.received = offset + len(data)
    session.commit()
    return {"received": u.received}


@router.post("/uploads/{upload_id}/complete")
def complete(upload_id: str, session: Session = Depends(get_session)):
    u = _get(session, upload_id)
    if u.status == "complete":
        return _dict(u)
    if u.received != u.size or os.path.getsize(u.temp_path) != u.size:
        raise HTTPException(409, detail={"message": "Upload is incomplete", "received": u.received})
    hot = _hotfolder(session)
    dest_dir = os.path.join(hot, u.provenance, *([u.relative_dir] if u.relative_dir else []))
    os.makedirs(dest_dir, exist_ok=True)
    dest = unique_path(os.path.join(dest_dir, u.filename))
    if u.last_modified:
        os.utime(u.temp_path, (u.last_modified, u.last_modified))   # keep the file's own date
    os.replace(u.temp_path, dest)                                  # same filesystem: atomic
    st = os.stat(dest)
    ing = ingest_ops.register(session, hot, dest, st.st_size, st.st_mtime)
    u.status, u.final_path, u.ingest_id = "complete", dest, ing.id
    events.log(session, "uploaded", actor="user", ingest_id=ing.id, filename=u.filename,
               size=u.size, provenance=u.provenance, client=u.client)
    session.commit()
    return _dict(u)


@router.delete("/uploads/{upload_id}")
def cancel(upload_id: str, session: Session = Depends(get_session)):
    u = _get(session, upload_id)
    if u.status == "uploading":
        try:
            os.unlink(u.temp_path)
        except FileNotFoundError:
            pass
        u.status = "cancelled"
        session.commit()
    return _dict(u)
