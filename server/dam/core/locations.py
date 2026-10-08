"""What the app can see on the host, and how container paths map to host paths.

Docker only exposes host folders that are bind-mounted into the container. Those
mounts are declared in HOST_ROOTS as `Label|container_path|host_path` entries
separated by `;`, e.g.

    HOST_ROOTS="Home|/host/home|/Users/me;Drives|/host/volumes|/Volumes"

Network shares mounted by the app itself live under MOUNT_BASE (see mounts.py).
Users only ever see host paths (or smb://… for shares); container paths stay internal.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable, Optional

MOUNT_BASE = os.environ.get("MOUNT_BASE", "/mnt/dam")

# Filesystem calls on a stalled mount (a disconnected share, or Docker Desktop
# waiting on a macOS privacy prompt) block forever. Run them on a side pool with
# a timeout so one bad location can't hang the API.
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fsprobe")
PROBE_TIMEOUT = float(os.environ.get("FS_PROBE_TIMEOUT", "3"))


class LocationUnavailable(OSError):
    pass


def probe(fn: Callable, *args, timeout: float = PROBE_TIMEOUT):
    """Run a filesystem call with a timeout; raises LocationUnavailable if it stalls."""
    future = _pool.submit(fn, *args)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise LocationUnavailable("Location is not responding (disconnected, or access not granted)")


def is_dir(path: str) -> bool:
    try:
        return probe(os.path.isdir, path)
    except LocationUnavailable:
        return False


@dataclass(frozen=True)
class Root:
    label: str
    path: str       # inside the container
    host_path: str  # what the user recognises
    kind: str       # host|drives|network


def _parse_roots(spec: str) -> list:
    roots = []
    for entry in filter(None, (e.strip() for e in spec.split(";"))):
        parts = entry.split("|")
        if len(parts) != 3:
            continue
        label, path, host_path = (p.strip() for p in parts)
        kind = "drives" if host_path.rstrip("/") in ("/Volumes", "/media", "/mnt", "/run/media") else "host"
        roots.append(Root(label, path.rstrip("/") or "/", host_path.rstrip("/") or "/", kind))
    return roots


def host_roots() -> list:
    return _parse_roots(os.environ.get("HOST_ROOTS", ""))


def network_roots(mounts: list) -> list:
    """Root entries for mounted network shares (NetworkMount rows)."""
    roots = []
    for m in mounts:
        url = f"smb://{m.server}/{m.share}" if m.protocol == "smb" else f"nfs://{m.server}{m.share}"
        roots.append(Root(m.name, os.path.join(MOUNT_BASE, m.name), url, "network"))
    return roots


def all_roots(mounts: Optional[list] = None) -> list:
    return host_roots() + network_roots(mounts or [])


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def to_host(path: Optional[str], mounts: Optional[list] = None) -> Optional[str]:
    """Container path -> path as the user knows it. Unmapped paths are returned as-is."""
    if not path:
        return path
    for root in sorted(all_roots(mounts), key=lambda r: -len(r.path)):
        for base in {root.path, os.path.realpath(root.path)}:
            if _within(path, base):
                rest = path[len(base):].lstrip("/")
                return f"{root.host_path.rstrip('/')}/{rest}" if rest else root.host_path
    return path


def to_container(path: str, mounts: Optional[list] = None) -> Optional[str]:
    """Host path (or smb:// URL) typed/picked by the user -> container path, if visible."""
    if not path:
        return None
    path = path.rstrip("/") or "/"
    for root in sorted(all_roots(mounts), key=lambda r: -len(r.host_path)):
        if _within(path, root.host_path):
            rest = path[len(root.host_path):].lstrip("/")
            return os.path.join(root.path, rest) if rest else root.path
    # Already a container path inside a root?
    for root in all_roots(mounts):
        if _within(path, root.path):
            return path
    return None


def root_for(path: str, mounts: Optional[list] = None) -> Optional[Root]:
    real = os.path.realpath(path)
    for root in sorted(all_roots(mounts), key=lambda r: -len(r.path)):
        if _within(real, os.path.realpath(root.path)):
            return root
    return None


def browse(path: str, mounts: Optional[list] = None) -> dict:
    """List subfolders of a container path that lies inside a known root."""
    return probe(_browse, path, mounts, timeout=PROBE_TIMEOUT * 4)


def _browse(path: str, mounts: Optional[list]) -> dict:
    root = root_for(path, mounts)
    if not root:
        raise PermissionError("That location is not available to the app")
    real = os.path.realpath(path)
    dirs, file_count = [], 0
    with os.scandir(real) as it:
        for entry in it:
            if entry.name.startswith((".", "$", "#")):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(entry.name)
                elif entry.is_file(follow_symlinks=False):
                    file_count += 1
            except OSError:
                continue
    dirs.sort(key=str.casefold)
    parent = os.path.dirname(real) if real != os.path.realpath(root.path) else None
    return {
        "path": real,
        "host_path": to_host(real, mounts),
        "root": root.label,
        "parent": parent,
        "parent_host_path": to_host(parent, mounts) if parent else None,
        "dirs": [{"name": d, "path": os.path.join(real, d), "host_path": to_host(os.path.join(real, d), mounts)}
                 for d in dirs],
        "file_count": file_count,
        "writable": os.access(real, os.W_OK),
    }


def describe(path: Optional[str], mounts: Optional[list] = None) -> dict:
    """Status of a configured path, for the Configure page."""
    if not path:
        return {"path": None, "host_path": None, "exists": False, "writable": False, "visible": False}
    try:
        exists = probe(os.path.isdir, path)
        writable = exists and probe(os.access, path, os.W_OK)
        visible = probe(root_for, path, mounts) is not None
    except LocationUnavailable:
        exists = writable = False
        visible = True
    return {"path": path, "host_path": to_host(path, mounts),
            "visible": visible, "exists": exists, "writable": writable}


def drives(mounts: Optional[list] = None) -> list:
    """Removable/external drives currently attached (children of drive roots)."""
    try:
        return probe(_drives, mounts, timeout=PROBE_TIMEOUT * 2)
    except LocationUnavailable:
        return []


def _drives(mounts: Optional[list]) -> list:
    found = []
    for root in host_roots():
        if root.kind != "drives" or not os.path.isdir(root.path):
            continue
        try:
            names = sorted(os.listdir(root.path), key=str.casefold)
        except OSError:
            continue
        for name in names:
            full = os.path.join(root.path, name)
            if name.startswith(".") or not os.path.isdir(full):
                continue
            # macOS lists the boot volume under /Volumes as a symlink to /
            if os.path.islink(full):
                continue
            try:
                st = probe(os.statvfs, full, timeout=1)
                total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
            except OSError:
                total = free = None
            found.append({"name": name, "path": full, "host_path": to_host(full, mounts),
                          "total_bytes": total, "free_bytes": free})
    return found
