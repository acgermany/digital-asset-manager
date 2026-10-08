"""SMB/NFS shares mounted by the app (needs CAP_SYS_ADMIN in the container).

Passwords are written to a root-only credentials file under DATA_DIR/secrets,
never to the database or logs.
"""
import os
import re
import subprocess
from typing import Optional

from sqlalchemy.orm import Session

from ..models import NetworkMount
from .locations import MOUNT_BASE

DATA_DIR = os.environ.get("DATA_DIR", "/data")
SECRETS_DIR = os.path.join(DATA_DIR, "secrets")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SERVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,254}$")
SHARE_RE = re.compile(r"^[^,\"'\\\x00-\x1f]{1,512}$")
OPTIONS_RE = re.compile(r"^[A-Za-z0-9=_.,:\-]*$")
FORBIDDEN_OPTIONS = ("credentials", "password", "pass=", "username", "user=")


def target(m: NetworkMount) -> str:
    return os.path.join(MOUNT_BASE, m.name)


def cred_file(m: NetworkMount) -> str:
    return os.path.join(SECRETS_DIR, f"mount-{m.id}.cred")


def validate(name: str, protocol: str, server: str, share: str, options: Optional[str]) -> None:
    if not NAME_RE.match(name or ""):
        raise ValueError("Name: letters, digits, - and _ only (max 64)")
    if protocol not in ("smb", "nfs"):
        raise ValueError("Protocol must be smb or nfs")
    if not SERVER_RE.match(server or ""):
        raise ValueError("Server must be a hostname or IP address")
    if not SHARE_RE.match(share or ""):
        raise ValueError("Invalid share / export path")
    if protocol == "nfs" and not share.startswith("/"):
        raise ValueError("NFS export path must start with /")
    if options:
        if not OPTIONS_RE.match(options):
            raise ValueError("Options may only contain letters, digits and = _ . , : -")
        if any(f in options.lower() for f in FORBIDDEN_OPTIONS):
            raise ValueError("Set username/password in their own fields, not in options")


def save_password(m: NetworkMount, password: Optional[str]) -> None:
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    path = cred_file(m)
    if password is None:
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"username={m.username or ''}\npassword={password}\n")


def delete_password(m: NetworkMount) -> None:
    try:
        os.unlink(cred_file(m))
    except FileNotFoundError:
        pass


def is_mounted(m: NetworkMount) -> bool:
    return os.path.ismount(target(m))


def _command(m: NetworkMount) -> list:
    mode = "ro" if m.read_only else "rw"
    if m.protocol == "smb":
        opts = [mode, "iocharset=utf8", "uid=0", "gid=0", "file_mode=0664", "dir_mode=0775"]
        if m.username:
            if m.has_password:
                opts.append(f"credentials={cred_file(m)}")
            else:
                opts.append(f"username={m.username}")
        else:
            opts.append("guest")
        if m.options:
            opts.append(m.options)
        return ["mount", "-t", "cifs", f"//{m.server}/{m.share}", target(m), "-o", ",".join(opts)]
    opts = [mode, "nolock", "soft", "timeo=100"]
    if m.options:
        opts.append(m.options)
    return ["mount", "-t", "nfs", f"{m.server}:{m.share}", target(m), "-o", ",".join(opts)]


def mount(m: NetworkMount) -> None:
    if is_mounted(m):
        return
    os.makedirs(target(m), exist_ok=True)
    try:
        proc = subprocess.run(_command(m), capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError("mount tools are not installed in this container")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timed out connecting to the server")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        if "Operation not permitted" in msg and "mount error" not in msg:
            msg += " — the container needs the SYS_ADMIN capability (see docker-compose.yml)"
        raise RuntimeError(msg or f"mount exited with {proc.returncode}")


def unmount(m: NetworkMount) -> None:
    if not is_mounted(m):
        return
    proc = subprocess.run(["umount", target(m)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        # Busy (a scan is reading it): detach lazily so it frees once idle
        proc = subprocess.run(["umount", "-l", target(m)], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip())


def mount_all(session: Session) -> None:
    """Re-mount auto-mount shares (container start)."""
    for m in session.query(NetworkMount).filter_by(auto_mount=True).all():
        try:
            mount(m)
            m.last_error = None
        except Exception as e:  # noqa: BLE001 — record and keep going
            m.last_error = str(e)
    session.commit()
