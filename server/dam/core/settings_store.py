"""Editable settings: stored in app_settings, defaulting to environment variables."""
import os
import time
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings as env
from ..models import AppSetting

DEFAULT_IMAGE_PROMPT = (
    "Describe this photo for a personal media library so it can be found by search later. "
    "In 2-3 sentences cover: who or what is in it (people by apparent age or role, never guess names), "
    "the setting and type of place, the activity or occasion, and notable objects, text or landmarks. "
    "Then on a new line write 'Tags:' followed by 5-10 short, comma-separated keywords."
)
DEFAULT_VIDEO_PROMPT = (
    "These {frames} frames are evenly spaced, in order, through a {duration} video. "
    "Summarize the video for a personal media library in 2-4 sentences: who is in it (by apparent "
    "age or role, never guess names), the setting, what happens from start to finish, and any "
    "notable moments, objects or text. "
    "Then on a new line write 'Tags:' followed by 5-10 short, comma-separated keywords."
)

DEFAULTS: dict = {
    "hotfolder": env.hotfolder,
    "hotfolder_enabled": True,
    "library": env.library,
    "catalog_sources": [env.catalog_root] if env.catalog_root else [],
    "delete_mode": env.delete_mode,          # delete|trash
    "stable_seconds": env.stable_seconds,
    "ollama_url": env.ollama_url,
    "embed_model": env.embed_model,
    "embed_keep_alive": env.embed_keep_alive,
    "embed_sensitive_text": env.embed_sensitive_text,
    # Vision descriptions (captions for images, summaries for videos)
    "describe_model": "qwen3-vl:8b",
    "describe_image_prompt": DEFAULT_IMAGE_PROMPT,
    "describe_video_prompt": DEFAULT_VIDEO_PROMPT,
    "describe_kinds": ["image", "video"],
    "describe_video_frames": 8,
    "describe_frame_size": 768,
    "describe_auto": False,
    "describe_critical": False,
    "describe_keep_alive": "10m",
    "describe_think": False,
    "describe_max_tokens": 4096,  # thinking models spend much of this before answering
    "describe_use_transcript": True,  # give the video summarizer what was said
    # Speech-to-text (whisper.cpp server on the Mac, or any OpenAI-compatible server)
    "transcribe_url": os.environ.get("TRANSCRIBE_URL", "http://host.docker.internal:8178"),
    "transcribe_api": "whispercpp",   # whispercpp | openai
    "transcribe_model": "whisper-1",  # sent to OpenAI-compatible servers only
    "transcribe_language": "auto",
    "transcribe_kinds": ["video", "audio"],
    "transcribe_auto": False,
    "transcribe_critical": False,
    "transcribe_max_minutes": 120,
}

EDITABLE = set(DEFAULTS)

_cache: dict = {}
_cache_at = 0.0
CACHE_SECONDS = 3.0


def all_settings(session: Session, fresh: bool = False) -> dict:
    """Effective settings. Cached briefly because watcher/worker loops read them often."""
    global _cache, _cache_at
    if not fresh and _cache and time.monotonic() - _cache_at < CACHE_SECONDS:
        return dict(_cache)
    values = dict(DEFAULTS)
    for row in session.query(AppSetting).all():
        if row.key in EDITABLE:
            values[row.key] = row.value.get("v")
    _cache, _cache_at = values, time.monotonic()
    return dict(values)


def get(session: Session, key: str) -> Any:
    return all_settings(session)[key]


def update(session: Session, changes: dict) -> dict:
    global _cache_at
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    for key, value in changes.items():
        row = session.get(AppSetting, key)
        if row:
            row.value = {"v": value}
        else:
            session.add(AppSetting(key=key, value={"v": value}))
    session.flush()
    _cache_at = 0.0
    return all_settings(session, fresh=True)
