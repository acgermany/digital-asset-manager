"""Status of the AI orchestrator (if one is configured), for the AI Orchestrator tab."""
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...core import settings_store
from ...db import get_session

router = APIRouter()


@router.get("/orchestrator/status")
def status(session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session)
    url = cfg.get("orchestrator_url") or ""
    routed = {"ollama": bool(url) and cfg["ollama_url"].rstrip("/") == url,
              "whisper": bool(url) and cfg["transcribe_url"].rstrip("/") == url}
    if not url:
        return {"configured": False, "url": None, "routed": routed}
    headers = {}
    if cfg.get("orchestrator_admin_token"):
        headers["Authorization"] = f"Bearer {cfg['orchestrator_admin_token']}"
    try:
        r = httpx.get(f"{url}/admin/status", headers=headers, timeout=5)
    except httpx.HTTPError as e:
        return {"configured": True, "url": url, "routed": routed, "error": f"Not reachable at {url}: {e}"}
    if r.status_code == 401:
        return {"configured": True, "url": url, "routed": routed,
                "error": "The orchestrator wants an admin token (set it under Connection)"}
    if r.status_code != 200:
        return {"configured": True, "url": url, "routed": routed, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return {"configured": True, "url": url, "routed": routed, "error": None, "status": r.json()}
