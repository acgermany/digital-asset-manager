"""Vision-model descriptions: captions for images, summaries for videos.

Images are sent as one downscaled JPEG. Videos are sampled into N evenly spaced
frames sent together in one request, so the model can describe what happens
across them. Runs against Ollama on the Mac via /api/chat.
"""
import base64
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Asset, Embedding
from . import ai, events, media
from .assets import build_search_text

log = logging.getLogger(__name__)

TIER_ORDER = "CASE value_tier WHEN 'precious' THEN 0 WHEN 'critical' THEN 1 " \
             "WHEN 'standard' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
TAGS_RE = re.compile(r"^\s*(?:\*\*)?tags(?:\*\*)?\s*:\s*(.+)$", re.I | re.M)


class FrameError(Exception):
    """This file can't be turned into frames (missing, corrupt, unsupported)."""


class DescribeError(Exception):
    """The model answered but the answer is unusable for this file."""


# ---------------------------------------------------------------- eligibility

def _eligible_sql(cfg: dict) -> tuple:
    kinds = [k for k in (cfg.get("describe_kinds") or []) if k in ("image", "video")] or ["none"]
    clause = "kind = ANY(:kinds)"
    if not cfg.get("describe_critical"):
        clause += " AND value_tier <> 'critical'"
    return clause, {"kinds": kinds}


def pending_ids(session: Session, cfg: dict, limit: Optional[int] = None,
                redescribe_before: Optional[str] = None) -> list:
    """Undescribed assets, or with redescribe_before (ISO time) everything not redone since then."""
    clause, params = _eligible_sql(cfg)
    if redescribe_before:
        clause += " AND (described_at IS NULL OR described_at < CAST(:before AS timestamptz))"
        params["before"] = redescribe_before
    else:
        clause += " AND described_at IS NULL AND description_error IS NULL"
    sql = f"SELECT id FROM assets WHERE {clause} ORDER BY {TIER_ORDER}, id"
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit
    return [r[0] for r in session.execute(text(sql), params)]


def coverage(session: Session, cfg: dict) -> dict:
    clause, params = _eligible_sql(cfg)
    row = session.execute(text(f"""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE described_at IS NOT NULL),
               COUNT(*) FILTER (WHERE description_error IS NOT NULL AND described_at IS NULL)
        FROM assets WHERE {clause}"""), params).one()
    eligible, done, failed = row
    return {"model": cfg["describe_model"], "eligible": eligible, "described": done,
            "failed": failed, "pending": max(0, eligible - done - failed)}


# ---------------------------------------------------------------- frames

def _jpeg(args: list, timeout: int = 60) -> bytes:
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", *args, "-frames:v", "1",
                            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise FrameError("Timed out extracting a frame")
    if r.returncode != 0 or not r.stdout:
        log.info("frame extraction failed: %s", r.stderr.decode("utf-8", "replace").strip()[-500:])
        raise FrameError("not a readable image or video (it may be damaged or an unsupported format)")
    return r.stdout


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}-second"
    m, s = divmod(seconds, 60)
    return f"{m} minute {s} second" if m < 60 else f"{m // 60} hour {m % 60} minute"


def frames_for(asset: Asset, path: str, cfg: dict) -> tuple:
    """Returns (list of JPEG bytes, prompt-context dict)."""
    size = int(cfg.get("describe_frame_size") or 768)
    scale = f"scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease"
    if asset.kind == "image":
        src = media.display_image(path, asset.sha256)
        if not src:
            raise FrameError("Image format could not be converted")
        return [_jpeg(["-i", src, "-vf", scale])], {}
    info = media.probe(path)
    duration = info.get("duration") or 0
    n = max(1, min(32, int(cfg.get("describe_video_frames") or 8)))
    if duration <= 0:
        return [_jpeg(["-i", path, "-vf", scale])], {"frames": 1, "duration": "short"}
    frames = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        try:
            frames.append(_jpeg(["-ss", f"{t:.2f}", "-i", path, "-vf", scale]))
        except FrameError:
            continue  # a bad seek point shouldn't sink the whole video
    if not frames:
        raise FrameError("Could not extract any frames from the video")
    return frames, {"frames": len(frames), "duration": _fmt_duration(duration)}


# ---------------------------------------------------------------- model call

def build_prompt(asset: Asset, cfg: dict, context: dict) -> str:
    template = cfg["describe_video_prompt"] if asset.kind == "video" else cfg["describe_image_prompt"]
    out = template
    for key, value in context.items():   # plain replace: user prompts may contain other braces
        out = out.replace("{" + key + "}", str(value))
    return out


def call_model(cfg: dict, prompt: str, images: list, timeout: float = 600) -> str:
    """Ask the vision model. If a thinking model runs out of tokens before it answers,
    retry once with twice the budget."""
    budget = int(cfg.get("describe_max_tokens") or 4096)
    content, truncated = _chat(cfg, prompt, images, budget, timeout)
    if not content and truncated:
        content, truncated = _chat(cfg, prompt, images, min(budget * 2, 16384), timeout)
        if not content and truncated:
            raise DescribeError("The model used its whole output budget thinking and never answered. "
                                "Raise “Max output” in Configure → AI.")
    return content


def _chat(cfg: dict, prompt: str, images: list, num_predict: int, timeout: float) -> tuple:
    body = {
        "model": cfg["describe_model"],
        "stream": False,
        "keep_alive": cfg.get("describe_keep_alive") or "10m",
        "messages": [{"role": "user", "content": prompt,
                      "images": [base64.b64encode(i).decode() for i in images]}],
        "options": {"num_predict": num_predict, "temperature": 0.2},
    }
    if not cfg.get("describe_think"):
        body["think"] = False
    try:
        r = httpx.post(f"{cfg['ollama_url']}/api/chat", json=body, timeout=timeout)
    except httpx.HTTPError as e:
        raise ai.AIUnavailable(f"Ollama not reachable: {e}") from e
    if r.status_code == 404:
        raise ai.AIUnavailable(f"Model '{cfg['describe_model']}' is not installed on the Mac. "
                               f"Run: ollama pull {cfg['describe_model']}")
    if r.status_code != 200:
        raise ai.AIUnavailable(f"Ollama error {r.status_code}: {r.text[:300]}")
    data = r.json()
    content = ((data.get("message") or {}).get("content") or "").strip()
    return content, data.get("done_reason") == "length"


def parse(raw: str) -> tuple:
    """Split the model's answer into (description, keyword tags)."""
    tags: list = []
    m = TAGS_RE.search(raw)
    if m:
        tags = [t.strip(" .#*\"'") for t in m.group(1).split(",")]
        tags = [t for t in tags if t and len(t) <= 40][:15]
        raw = raw[:m.start()]
    desc = re.sub(r"^\s*(description|summary)\s*:\s*", "", raw.strip(), flags=re.I)
    desc = re.sub(r"\s+\n", "\n", desc).strip()
    return desc, tags


# ---------------------------------------------------------------- per asset / batch

def _file_path(asset: Asset) -> Optional[str]:
    for f in sorted(asset.files, key=lambda f: (f.source != "library", f.id)):
        if os.path.isfile(f.path):
            return f.path
    return None


def describe_asset(session: Session, cfg: dict, asset: Asset) -> bool:
    """Describe one asset. Returns False (and records why) if the file can't be described.
    Raises ai.AIUnavailable when the Mac/model is the problem, so the batch can pause."""
    path = _file_path(asset)
    if not path:
        asset.description_error = "No copy of this file is reachable"
        session.commit()
        return False
    try:
        images, context = frames_for(asset, path, cfg)
    except FrameError as e:
        asset.description_error = f"Could not read the file: {e}"
        session.commit()
        return False
    try:
        raw = call_model(cfg, build_prompt(asset, cfg, context), images)
    except DescribeError as e:
        asset.description_error = str(e)
        session.commit()
        return False
    desc, tags = parse(raw)
    if not desc:
        asset.description_error = "The model returned an empty description"
        session.commit()
        return False
    asset.description, asset.description_tags = desc, tags
    asset.description_model = cfg["describe_model"]
    asset.described_at = datetime.now(timezone.utc)
    asset.description_error = None
    asset.search_text = build_search_text(asset, session)
    # The searchable text changed, so the old embedding is stale.
    session.query(Embedding).filter(Embedding.asset_id == asset.id).delete()
    events.log(session, "described", actor="model", asset_id=asset.id,
               model=cfg["describe_model"], frames=len(images))
    session.commit()
    return True


def run_batch(session: Session, cfg: dict, report: Callable[[dict], bool],
              asset_ids: Optional[list] = None, redescribe_before: Optional[str] = None,
              limit: Optional[int] = None) -> dict:
    """Describe pending (or given) assets one by one. Resumable: each asset commits,
    and a resumed batch only picks up what is still pending."""
    if asset_ids:
        ids = list(asset_ids)
    else:
        ids = pending_ids(session, cfg, limit=limit, redescribe_before=redescribe_before)
    progress = {"total": len(ids), "done": 0, "described": 0, "failed": 0,
                "model": cfg["describe_model"], "current": None}
    if not report(progress):
        return progress
    for asset_id in ids:
        asset = session.get(Asset, asset_id)
        if asset is None:
            progress["done"] += 1
            continue
        progress["current"] = asset.files[0].filename if asset.files else None
        if not report(progress):
            break
        if describe_asset(session, cfg, asset):
            progress["described"] += 1
        else:
            progress["failed"] += 1
        progress["done"] += 1
    progress["current"] = None
    report(progress)
    return progress
