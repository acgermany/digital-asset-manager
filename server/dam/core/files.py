"""Filesystem helpers: filename rules, kinds, safe moves."""
import mimetypes
import os
import shutil
from typing import Optional

from .hashing import sha256_file

IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
IGNORED_SUFFIXES = (".part", ".partial", ".crdownload", ".download", ".tmp", ".dam-tmp")
IGNORED_DIRS = {"_review", "_trash"}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff",
             ".bmp", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".mpg", ".mpeg",
             ".mts", ".m2ts", ".ts", ".3gp", ".mxf"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif",
             ".wma", ".amr", ".caf", ".m4b"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".rtf", ".odt", ".ods",
           ".pages", ".numbers", ".qfx", ".ofx", ".qif"}


def name_key(filename: str) -> str:
    """Filename comparison key for exact-duplicate checks: case-insensitive."""
    return filename.strip().casefold()


def is_ignored(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if parts[0] in IGNORED_DIRS:
        return True
    for part in parts:
        if part.startswith("."):  # hidden files, AppleDouble "._x", .smbdelete*
            return True
    name = parts[-1]
    return name in IGNORED_NAMES or name.lower().endswith(IGNORED_SUFFIXES)


def is_sidecar(path: str) -> bool:
    """`clip.mov.json` next to `clip.mov` carries provenance metadata, not an asset."""
    return path.lower().endswith(".json") and os.path.exists(path[:-5])


def kind_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DOC_EXT:
        return "document"
    return "other"


def mime_for(filename: str) -> Optional[str]:
    return mimetypes.guess_type(filename)[0]


def unique_path(path: str) -> str:
    """`a/b.mov` -> `a/b (1).mov` if taken."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def verified_move(src: str, dest_dir: str, filename: str, expected_sha256: str) -> str:
    """Copy to the destination (renamed `name (1).ext` if taken), verify, then remove the source."""
    return verified_move_to(src, unique_path(os.path.join(dest_dir, filename)), expected_sha256)


def verified_move_to(src: str, dest: str, expected_sha256: str) -> str:
    """Copy to exactly `dest`, verify the checksum there, then remove the source.

    The library is on the NAS (a different filesystem), so a move is a copy. The
    source is only removed once the copy is proven identical. Never overwrites:
    the caller decides what a taken `dest` means.
    """
    if os.path.exists(dest):
        raise FileExistsError(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".dam-tmp"
    shutil.copy2(src, tmp)
    actual = sha256_file(tmp)
    if actual != expected_sha256:
        os.unlink(tmp)
        raise IOError(f"Checksum mismatch after copying to {dest}")
    os.replace(tmp, dest)
    os.unlink(src)
    return dest


def local_move(src: str, dest_dir: str, filename: str) -> str:
    """Move within the hot folder's filesystem (review / trash)."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = unique_path(os.path.join(dest_dir, filename))
    shutil.move(src, dest)
    return dest
