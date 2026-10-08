"""Configure page: settings, host locations, network shares, AI connection test."""
import os
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...config import settings as env
from ...core import events, locations, mounts as mount_ops, settings_store
from ...db import get_session
from ...models import NetworkMount
from ..deps import mounts

router = APIRouter()


# ---------------------------------------------------------------- helpers

def _root_info(root, session) -> dict:
    available = locations.is_dir(root.path) and (root.kind != "network" or os.path.ismount(root.path))
    return {"label": root.label, "path": root.path, "host_path": root.host_path,
            "kind": root.kind, "available": available}


def _library_status(path: Optional[str], ms: list) -> dict:
    info = locations.describe(path, ms)
    try:
        info["marker"] = bool(path) and locations.probe(os.path.isfile, os.path.join(path, env.library_marker))
    except locations.LocationUnavailable:
        info["marker"] = False
    return info


def _config_payload(session: Session) -> dict:
    ms = mounts(session)
    cfg = settings_store.all_settings(session, fresh=True)
    return {
        "settings": {
            **cfg,
            "hotfolder": locations.to_host(cfg["hotfolder"], ms),
            "library": locations.to_host(cfg["library"], ms),
            "catalog_sources": [locations.to_host(p, ms) for p in cfg["catalog_sources"] or []],
        },
        "status": {
            "hotfolder": locations.describe(cfg["hotfolder"], ms),
            "library": _library_status(cfg["library"], ms),
            "catalog_sources": [locations.describe(p, ms) for p in cfg["catalog_sources"] or []],
        },
        "roots": [_root_info(r, session) for r in locations.all_roots(ms)],
        "drives": locations.drives(ms),
        "mounts": [_mount_info(m) for m in ms],
        "embed_dims": env.embed_dims,
        "defaults": {k: settings_store.DEFAULTS[k] for k in ("describe_image_prompt", "describe_video_prompt",
                                                            "describe_model")},
    }


def _resolve_dir(value: str, ms: list, label: str) -> str:
    path = locations.to_container(value, ms)
    if not path:
        raise HTTPException(400, f"{label}: '{value}' is not inside a location the app can see. "
                                 "Pick it with the folder browser, or add the drive/share first.")
    if not locations.is_dir(path):
        raise HTTPException(400, f"{label}: folder does not exist or is not responding")
    return path


# ---------------------------------------------------------------- settings

@router.get("/config")
def get_config(session: Session = Depends(get_session)):
    return _config_payload(session)


class ConfigUpdate(BaseModel):
    hotfolder: Optional[str] = None
    hotfolder_enabled: Optional[bool] = None
    library: Optional[str] = None
    catalog_sources: Optional[list[str]] = None
    delete_mode: Optional[str] = None
    stable_seconds: Optional[float] = None
    ollama_url: Optional[str] = None
    embed_model: Optional[str] = None
    embed_keep_alive: Optional[str] = None
    embed_sensitive_text: Optional[bool] = None
    describe_model: Optional[str] = None
    describe_image_prompt: Optional[str] = None
    describe_video_prompt: Optional[str] = None
    describe_kinds: Optional[list[str]] = None
    describe_video_frames: Optional[int] = None
    describe_frame_size: Optional[int] = None
    describe_auto: Optional[bool] = None
    describe_critical: Optional[bool] = None
    describe_keep_alive: Optional[str] = None
    describe_think: Optional[bool] = None
    describe_max_tokens: Optional[int] = None


@router.put("/config")
def update_config(body: ConfigUpdate, session: Session = Depends(get_session)):
    ms = mounts(session)
    current = settings_store.all_settings(session, fresh=True)
    changes = body.model_dump(exclude_unset=True)

    if "hotfolder" in changes:
        if changes["hotfolder"]:
            path = _resolve_dir(changes["hotfolder"], ms, "Hot folder")
            if not os.access(path, os.W_OK):
                raise HTTPException(400, "Hot folder must be writable (duplicates are deleted from it)")
            changes["hotfolder"] = path
        else:
            changes["hotfolder"] = ""

    if "library" in changes:
        if changes["library"]:
            path = _resolve_dir(changes["library"], ms, "Library")
            if not os.access(path, os.W_OK):
                raise HTTPException(400, "Library must be writable")
            changes["library"] = path
        else:
            changes["library"] = ""

    hot = changes.get("hotfolder", current["hotfolder"])
    lib = changes.get("library", current["library"])
    if hot and lib:
        h, l = os.path.realpath(hot), os.path.realpath(lib)
        if h == l or l.startswith(h + "/") or h.startswith(l + "/"):
            raise HTTPException(400, "Hot folder and library must be separate folders, not inside each other")

    if "catalog_sources" in changes:
        resolved = []
        for value in changes["catalog_sources"] or []:
            p = _resolve_dir(value, ms, "Catalog source")
            if p not in resolved:
                resolved.append(p)
        changes["catalog_sources"] = resolved

    if "delete_mode" in changes and changes["delete_mode"] not in ("delete", "trash"):
        raise HTTPException(400, "delete_mode must be 'delete' or 'trash'")
    if "stable_seconds" in changes and not 1 <= changes["stable_seconds"] <= 600:
        raise HTTPException(400, "stable_seconds must be between 1 and 600")
    if "ollama_url" in changes:
        url = (changes["ollama_url"] or "").strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(:\d+)?(/.*)?$", url):
            raise HTTPException(400, "Ollama URL must look like http://host:11434")
        changes["ollama_url"] = url
    for key in ("embed_model", "describe_model"):
        if key in changes:
            if not re.match(r"^[\w.\-:/]{1,128}$", changes[key] or ""):
                raise HTTPException(400, "Invalid model name")
            # Ollama treats "x" and "x:latest" as one model; store one spelling so
            # embeddings/descriptions keyed by model name stay consistent.
            changes[key] = re.sub(r":latest$", "", changes[key])
    for key in ("describe_image_prompt", "describe_video_prompt"):
        if key in changes:
            changes[key] = (changes[key] or "").strip() or settings_store.DEFAULTS[key]
            if len(changes[key]) > 4000:
                raise HTTPException(400, "Prompts are limited to 4000 characters")
    if "describe_kinds" in changes:
        changes["describe_kinds"] = [k for k in changes["describe_kinds"] or [] if k in ("image", "video")]
    for key, lo, hi in (("describe_video_frames", 1, 32), ("describe_frame_size", 256, 2048),
                        ("describe_max_tokens", 128, 8192)):
        if key in changes and not lo <= changes[key] <= hi:
            raise HTTPException(400, f"{key} must be between {lo} and {hi}")
    for key in ("embed_keep_alive", "describe_keep_alive"):
        if key in changes and not re.match(r"^(-1|0|\d+(s|m|h))$", changes[key] or ""):
            raise HTTPException(400, "Keep-alive must look like 10m, 1h, 0 or -1")

    # Mark the library so an unmounted drive/NAS is never mistaken for an empty library.
    if changes.get("library"):
        marker = os.path.join(changes["library"], env.library_marker)
        if not os.path.exists(marker):
            with open(marker, "w") as f:
                f.write("Digital Asset Manager library. Do not delete: the app uses this file to "
                        "confirm the library drive is mounted.\n")

    settings_store.update(session, changes)
    events.log(session, "settings_changed", actor="user",
               keys=sorted(changes), **{k: v for k, v in changes.items() if k != "ollama_url"})
    session.commit()
    return _config_payload(session)


# ---------------------------------------------------------------- locations

@router.get("/locations/browse")
def browse(path: Optional[str] = None, session: Session = Depends(get_session)):
    ms = mounts(session)
    if not path:
        return {"path": None, "host_path": None, "parent": None, "dirs": [
            {"name": r.label, "path": r.path, "host_path": r.host_path, "kind": r.kind}
            for r in locations.all_roots(ms) if locations.is_dir(r.path)
        ], "file_count": 0, "writable": False}
    target = locations.to_container(path, ms) or path
    try:
        return locations.browse(target, ms)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except locations.LocationUnavailable as e:
        raise HTTPException(504, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "Folder not found")
    except OSError as e:
        raise HTTPException(400, f"Cannot open folder: {e.strerror or e}")


class MkdirBody(BaseModel):
    path: str
    name: str


@router.post("/locations/mkdir")
def mkdir(body: MkdirBody, session: Session = Depends(get_session)):
    ms = mounts(session)
    parent = locations.to_container(body.path, ms) or body.path
    if not locations.root_for(parent, ms):
        raise HTTPException(403, "That location is not available to the app")
    name = body.name.strip()
    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        raise HTTPException(400, "Invalid folder name")
    new = os.path.join(os.path.realpath(parent), name)
    try:
        os.makedirs(new, exist_ok=False)
    except FileExistsError:
        raise HTTPException(400, "A folder with that name already exists")
    except OSError as e:
        raise HTTPException(400, f"Could not create folder: {e.strerror or e}")
    return locations.browse(new, ms)


# ---------------------------------------------------------------- network shares

def _mount_info(m: NetworkMount) -> dict:
    url = f"smb://{m.server}/{m.share}" if m.protocol == "smb" else f"nfs://{m.server}{m.share}"
    return {"id": m.id, "name": m.name, "protocol": m.protocol, "server": m.server, "share": m.share,
            "url": url, "username": m.username, "has_password": m.has_password, "options": m.options,
            "read_only": m.read_only, "auto_mount": m.auto_mount,
            "mounted": mount_ops.is_mounted(m), "last_error": m.last_error,
            "path": mount_ops.target(m)}


class MountBody(BaseModel):
    name: str
    protocol: str = "smb"
    server: str
    share: str
    username: Optional[str] = None
    password: Optional[str] = None
    options: Optional[str] = None
    read_only: bool = True
    auto_mount: bool = True


@router.get("/mounts")
def list_mounts(session: Session = Depends(get_session)):
    return {"mounts": [_mount_info(m) for m in mounts(session)]}


@router.post("/mounts")
def add_mount(body: MountBody, session: Session = Depends(get_session)):
    share = body.share.strip().strip("/") if body.protocol == "smb" else body.share.strip()
    try:
        mount_ops.validate(body.name, body.protocol, body.server.strip(), share, body.options)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if session.query(NetworkMount).filter_by(name=body.name).first():
        raise HTTPException(400, "A share with that name already exists")
    m = NetworkMount(name=body.name, protocol=body.protocol, server=body.server.strip(), share=share,
                     username=(body.username or None), has_password=bool(body.password),
                     options=body.options or None, read_only=body.read_only, auto_mount=body.auto_mount)
    session.add(m)
    session.flush()
    mount_ops.save_password(m, body.password)
    try:
        mount_ops.mount(m)
        m.last_error = None
    except Exception as e:  # noqa: BLE001 — keep the definition so the user can fix and retry
        m.last_error = str(e)
    events.log(session, "share_added", actor="user", name=m.name, url=_mount_info(m)["url"])
    session.commit()
    return _mount_info(m)


def _get_mount(session: Session, mount_id: int) -> NetworkMount:
    m = session.get(NetworkMount, mount_id)
    if not m:
        raise HTTPException(404, "Share not found")
    return m


@router.post("/mounts/{mount_id}/mount")
def do_mount(mount_id: int, session: Session = Depends(get_session)):
    m = _get_mount(session, mount_id)
    try:
        mount_ops.mount(m)
        m.last_error = None
    except Exception as e:  # noqa: BLE001
        m.last_error = str(e)
    session.commit()
    return _mount_info(m)


@router.post("/mounts/{mount_id}/unmount")
def do_unmount(mount_id: int, session: Session = Depends(get_session)):
    m = _get_mount(session, mount_id)
    try:
        mount_ops.unmount(m)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not unmount: {e}")
    session.commit()
    return _mount_info(m)


@router.delete("/mounts/{mount_id}")
def delete_mount(mount_id: int, session: Session = Depends(get_session)):
    m = _get_mount(session, mount_id)
    cfg = settings_store.all_settings(session, fresh=True)
    target = mount_ops.target(m)
    in_use = [k for k in ("hotfolder", "library") if (cfg[k] or "").startswith(target)]
    in_use += ["catalog source"] * any(p.startswith(target) for p in cfg["catalog_sources"] or [])
    if in_use:
        raise HTTPException(400, f"This share is used as: {', '.join(in_use)}. Change that setting first.")
    try:
        mount_ops.unmount(m)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not unmount: {e}")
    mount_ops.delete_password(m)
    try:
        os.rmdir(target)
    except OSError:
        pass
    events.log(session, "share_removed", actor="user", name=m.name)
    session.delete(m)
    session.commit()
    return {"success": True}


# ---------------------------------------------------------------- AI connection

@router.post("/ai/test")
def test_ai(session: Session = Depends(get_session)):
    cfg = settings_store.all_settings(session, fresh=True)
    url, model = cfg["ollama_url"], cfg["embed_model"]
    try:
        r = httpx.get(f"{url}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": f"Could not reach Ollama at {url}: {e}", "models": []}
    installed = any(n == model or n.split(":")[0] == model for n in models)
    return {"reachable": True, "models": models, "model_installed": installed,
            "error": None if installed else f"Model '{model}' is not installed. On the Mac: ollama pull {model}"}
