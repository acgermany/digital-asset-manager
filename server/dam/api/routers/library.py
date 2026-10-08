"""Files, duplicates, preview content and segments."""
import json
import mimetypes
import os
import subprocess
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ... import queue
from ...core import events, locations, media, scoring
from ...db import get_session
from ...models import Asset, File, Job, Segment
from ..deps import mounts

router = APIRouter()


def _file_row(f: File, asset: Optional[Asset], ms: list) -> dict:
    return {
        "id": f.id, "asset_id": f.asset_id,
        "filepath": locations.to_host(f.path, ms), "filename": f.filename,
        "filesize": f.filesize, "sha256": f.sha256, "source": f.source,
        "provenance": f.provenance, "scan_date": f.scanned_at.isoformat() if f.scanned_at else None,
        "marked_for_deletion": f.marked_for_deletion,
        "value_tier": asset.value_tier if asset else None,
        "protected": bool(asset and asset.protected),
        "kind": asset.kind if asset else None,
    }


DUP_ASSETS = "SELECT asset_id FROM files WHERE asset_id IS NOT NULL GROUP BY asset_id HAVING COUNT(*) > 1"


@router.get("/files")
def list_files(limit: int = 100, offset: int = 0, search: Optional[str] = None,
               duplicates_only: bool = False, marked_only: bool = False,
               tier: Optional[str] = None, source: Optional[str] = None, kind: Optional[str] = None,
               session: Session = Depends(get_session)):
    q = session.query(File, Asset).outerjoin(Asset, Asset.id == File.asset_id)
    if search:
        like = f"%{search}%"
        q = q.filter(or_(File.path.ilike(like), File.filename.ilike(like), Asset.search_text.ilike(like)))
    if duplicates_only:
        q = q.filter(File.asset_id.in_(text(DUP_ASSETS)))
    if marked_only:
        q = q.filter(File.marked_for_deletion.is_(True))
    if tier:
        q = q.filter(Asset.value_tier == tier)
    if source:
        q = q.filter(File.source == source)
    if kind:
        q = q.filter(Asset.kind == kind)
    total = q.count()
    rows = q.order_by(File.scanned_at.desc(), File.id.desc()).limit(limit).offset(offset).all()
    ms = mounts(session)
    return {"files": [_file_row(f, a, ms) for f, a in rows], "total": total, "offset": offset, "limit": limit}


@router.post("/files/{file_id}/mark")
def mark_file(file_id: int, mark: bool = True, session: Session = Depends(get_session)):
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if mark and f.asset and f.asset.protected and len(f.asset.files) <= 1:
        raise HTTPException(400, "This is the only copy of a protected file")
    f.marked_for_deletion = mark
    events.log(session, "file_marked" if mark else "file_unmarked", actor="user", asset_id=f.asset_id,
               file_id=f.id)
    session.commit()
    return {"success": True, "file_id": file_id, "marked": mark}


@router.get("/duplicates")
def duplicates(limit: int = 50, offset: int = 0, session: Session = Depends(get_session)):
    total = session.execute(text(f"SELECT COUNT(*) FROM ({DUP_ASSETS}) d")).scalar()
    groups_rows = session.execute(text(f"""
        SELECT a.id, a.sha256, COUNT(f.id) AS count, SUM(f.filesize) AS total_size
        FROM assets a JOIN files f ON f.asset_id = a.id
        WHERE a.id IN ({DUP_ASSETS})
        GROUP BY a.id ORDER BY total_size DESC LIMIT :limit OFFSET :offset
    """), {"limit": limit, "offset": offset}).all()
    ms = mounts(session)
    groups = []
    for asset_id, sha, count, total_size in groups_rows:
        asset = session.get(Asset, asset_id)
        files = [{**_file_row(f, asset, ms), "score": scoring.score(f.path, f.filename)} for f in asset.files]
        files.sort(key=lambda x: x["score"], reverse=True)
        for i, f in enumerate(files):
            f["suggested_keep"] = i == 0
        groups.append({"sha256": sha, "asset_id": asset_id, "count": count, "total_size": total_size,
                       "value_tier": asset.value_tier, "files": files})
    return {"groups": groups, "total": total, "offset": offset, "limit": limit}


@router.post("/duplicates/{sha256}/auto-mark")
def auto_mark(sha256: str, session: Session = Depends(get_session)):
    asset = session.query(Asset).filter_by(sha256=sha256).one_or_none()
    if not asset or len(asset.files) <= 1:
        return {"error": "No duplicates for this hash"}
    ranked = sorted(asset.files, key=lambda f: scoring.score(f.path, f.filename), reverse=True)
    keep, rest = ranked[0], ranked[1:]
    keep.marked_for_deletion = False
    for f in rest:
        f.marked_for_deletion = True
    events.log(session, "duplicates_auto_marked", actor="user", asset_id=asset.id,
               kept=keep.id, marked=[f.id for f in rest])
    session.commit()
    return {"kept": keep.id, "marked": [f.id for f in rest]}


# ---------------------------------------------------------------- content

ALL_AUDIO = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.aiff', '.aif', '.wma',
             '.alac', '.ape', '.amr', '.m4b', '.caf'}
MIME = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
        '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo'}


def _transcode_to_mp3(path: str) -> str:
    fd, tmp = tempfile.mkstemp(suffix='.mp3')
    os.close(fd)
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', path,
                             '-f', 'mp3', '-codec:a', 'libmp3lame', '-b:a', '192k', '-y', tmp],
                            stderr=subprocess.PIPE)
    if result.returncode != 0:
        os.unlink(tmp)
        raise HTTPException(500, f"FFmpeg error: {result.stderr.decode('utf-8', 'replace').strip()}")
    return tmp


@router.get("/files/{file_id}/content")
def file_content(file_id: int, session: Session = Depends(get_session)):
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(404, "File not found in database")
    if not os.path.isfile(f.path):
        raise HTTPException(404, "File not found on disk (is the drive or share mounted?)")
    ext = os.path.splitext(f.path)[1].lower()
    if ext in ALL_AUDIO:
        tmp = _transcode_to_mp3(f.path)
        return FileResponse(tmp, media_type='audio/mpeg',
                            filename=os.path.splitext(f.filename)[0] + '.mp3',
                            background=BackgroundTask(os.unlink, tmp))
    mime = MIME.get(ext) or mimetypes.guess_type(f.path)[0] or "application/octet-stream"
    return FileResponse(f.path, media_type=mime, filename=f.filename)


# ---------------------------------------------------------------- segments (stored per asset)

class SegmentBody(BaseModel):
    in_point: Optional[float] = None
    out_point: Optional[float] = None
    label: Optional[str] = None
    metadata: Optional[str] = None


def _seg(s: Segment) -> dict:
    return {"id": s.id, "asset_id": s.asset_id, "in_point": s.in_point, "out_point": s.out_point,
            "label": s.label, "metadata": s.metadata_json,
            "created_at": s.created_at.isoformat() if s.created_at else None}


def _asset_for_file(session: Session, file_id: int) -> int:
    f = session.get(File, file_id)
    if not f or not f.asset_id:
        raise HTTPException(404, "File not found")
    return f.asset_id


def _check_meta(raw: Optional[str]) -> str:
    raw = raw or "{}"
    try:
        json.loads(raw)
    except ValueError:
        raise HTTPException(400, "metadata must be valid JSON")
    return raw


@router.get("/files/{file_id}/segments")
def get_segments(file_id: int, session: Session = Depends(get_session)):
    asset_id = _asset_for_file(session, file_id)
    rows = session.query(Segment).filter_by(asset_id=asset_id).order_by(Segment.in_point).all()
    return {"segments": [_seg(s) for s in rows]}


@router.post("/files/{file_id}/segments")
def create_segment(file_id: int, body: SegmentBody, session: Session = Depends(get_session)):
    asset_id = _asset_for_file(session, file_id)
    s = Segment(asset_id=asset_id, in_point=body.in_point or 0, out_point=body.out_point or 0,
                label=body.label, metadata_json=_check_meta(body.metadata))
    session.add(s)
    session.commit()
    return _seg(s)


@router.patch("/segments/{segment_id}")
def update_segment(segment_id: int, body: SegmentBody, session: Session = Depends(get_session)):
    s = session.get(Segment, segment_id)
    if not s:
        raise HTTPException(404, "Segment not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "Nothing to update")
    if "metadata" in data:
        s.metadata_json = _check_meta(data.pop("metadata"))
    for k, v in data.items():
        setattr(s, k, v)
    session.commit()
    return _seg(s)


@router.delete("/segments/{segment_id}")
def delete_segment(segment_id: int, session: Session = Depends(get_session)):
    s = session.get(Segment, segment_id)
    if s:
        session.delete(s)
        session.commit()
    return {"success": True}


# ---------------------------------------------------------------- viewer

VIDEO_MIME = {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/mp4', '.webm': 'video/webm'}


def _get_file(session: Session, file_id: int) -> File:
    f = session.get(File, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    if not os.path.isfile(f.path):
        raise HTTPException(404, "File not found on disk (is the drive or share mounted?)")
    return f


def _proxy_job(session: Session, asset_id: int) -> Optional[Job]:
    return session.query(Job).filter(Job.kind == "proxy", Job.asset_id == asset_id) \
        .order_by(Job.id.desc()).first()


@router.get("/files/{file_id}/thumbnail")
def thumbnail(file_id: int, session: Session = Depends(get_session)):
    f = _get_file(session, file_id)
    out = media.thumbnail(f.path, f.sha256 or str(f.id), media.view_type(f.filename))
    if not out:
        raise HTTPException(404, "No thumbnail for this file")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@router.get("/files/{file_id}/viewinfo")
def view_info(file_id: int, session: Session = Depends(get_session)):
    """How the browser should show this file. Starts a video proxy if one is needed."""
    f = _get_file(session, file_id)
    vtype = media.view_type(f.filename)
    sha = f.sha256 or str(f.id)
    info = {"file_id": f.id, "type": vtype, "filename": f.filename,
            "filepath": locations.to_host(f.path, mounts(session)), "filesize": f.filesize,
            "url": f"/api/files/{f.id}/view", "download_url": f"/api/files/{f.id}/content",
            "proxy": None}
    if vtype == "video":
        probe = media.probe(f.path)
        info.update(duration=probe.get("duration"), width=probe.get("width"), height=probe.get("height"),
                    video_codec=probe.get("video_codec"), audio_codec=probe.get("audio_codec"))
        if not media.browser_playable(f.path, probe):
            if media.has_proxy(sha):
                info["proxy"] = {"status": "ready", "progress": 1.0}
            else:
                job = _proxy_job(session, f.asset_id) if f.asset_id else None
                if job is None or job.status in ("failed", "cancelled", "done"):
                    if job is not None and job.status == "failed" and job.error:
                        info["proxy"] = {"status": "failed", "error": job.error}
                        return info
                    job = queue.enqueue(session, "proxy", queue="heavy", asset_id=f.asset_id,
                                        priority=20, max_attempts=2, payload={"file_id": f.id})
                    session.commit()
                info["proxy"] = {"status": "queued" if job.status == "queued" else "processing",
                                 "progress": (job.progress or {}).get("fraction", 0.0)}
    elif vtype == "audio":
        info["url"] = f"/api/files/{f.id}/content"  # transcoded to MP3 there
    return info


@router.post("/files/{file_id}/proxy/retry")
def retry_proxy(file_id: int, session: Session = Depends(get_session)):
    f = _get_file(session, file_id)
    queue.enqueue(session, "proxy", queue="heavy", asset_id=f.asset_id, priority=20, max_attempts=2,
                  payload={"file_id": f.id})
    session.commit()
    return {"status": "queued"}


@router.get("/files/{file_id}/view")
def view(file_id: int, session: Session = Depends(get_session)):
    f = _get_file(session, file_id)
    vtype = media.view_type(f.filename)
    sha = f.sha256 or str(f.id)
    ext = os.path.splitext(f.path)[1].lower()
    if vtype == "image":
        out = media.display_image(f.path, sha)
        if not out:
            raise HTTPException(415, "This image format could not be converted for viewing")
        mime = mimetypes.guess_type(out)[0] or "image/jpeg"
        return FileResponse(out, media_type=mime)
    if vtype == "video":
        if media.has_proxy(sha):
            return FileResponse(media.proxy_path(sha), media_type="video/mp4")
        return FileResponse(f.path, media_type=VIDEO_MIME.get(ext, "video/mp4"))
    if vtype == "pdf":
        return FileResponse(f.path, media_type="application/pdf",
                            headers={"Content-Disposition": "inline"})
    if vtype == "text":
        return FileResponse(f.path, media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": "inline"})
    return file_content(file_id, session)
