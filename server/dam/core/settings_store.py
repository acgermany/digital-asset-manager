"""Editable settings: stored in app_settings, defaulting to environment variables."""
import time
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings as env
from ..models import AppSetting

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
