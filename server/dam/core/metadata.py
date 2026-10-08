"""Metadata extraction via external tools (mediainfo, exiftool, pdftotext).

Each extractor returns None when its tool is missing or fails, so ingest never
blocks on optional metadata.
"""
import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

TIMEOUT = 120

# Identity-number shapes. These are detected and redacted, never stored.
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
LONG_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){8,19}\b")


def _run(cmd: list, timeout: int = TIMEOUT) -> Optional[str]:
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
        return out.stdout.decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("%s failed: %s", cmd[0], e)
        return None


def mediainfo(path: str) -> Optional[dict]:
    raw = _run(["mediainfo", "--Output=JSON", path])
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def media_tracks(info: Optional[dict]) -> list:
    if not info:
        return []
    return [t.get("@type") for t in info.get("media", {}).get("track", []) if isinstance(t, dict)]


def is_media(info: Optional[dict]) -> bool:
    return any(t in ("Video", "Audio") for t in media_tracks(info))


def exif(path: str) -> Optional[dict]:
    """Selected EXIF/QuickTime/XMP tags. Binary blobs are skipped."""
    raw = _run(["exiftool", "-json", "-n", "-api", "LargeFileSupport=1",
                "-Make", "-Model", "-Software", "-LensModel",
                "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate", "-ContentCreateDate",
                "-GPSLatitude", "-GPSLongitude", "-ImageWidth", "-ImageHeight",
                "-Duration", "-UserComment", "-Description", "-Title", "-Keywords",
                "-PageCount", "-Producer", "-Creator", "-Author", path])
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not data:
        return None
    tags = {k: v for k, v in data[0].items() if k != "SourceFile" and v not in ("", None)}
    return tags or None


_EXIF_DATE_RE = re.compile(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def captured_at(tags: Optional[dict]) -> Optional[datetime]:
    """Original capture time from EXIF/QuickTime tags, if plausible."""
    if not tags:
        return None
    for key in ("DateTimeOriginal", "ContentCreateDate", "MediaCreateDate", "CreateDate"):
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        m = _EXIF_DATE_RE.search(value)
        if not m:
            continue
        try:
            dt = datetime(*map(int, m.groups()), tzinfo=timezone.utc)
        except ValueError:
            continue
        if 1970 < dt.year <= datetime.now().year + 1:  # QuickTime zero dates come out as 1904
            return dt
    return None


def pdf_text(path: str, max_pages: int = 5) -> Optional[str]:
    return _run(["pdftotext", "-l", str(max_pages), "-layout", path, "-"])


def plain_text(path: str, limit: int = 200_000) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


def document_text(path: str) -> Optional[str]:
    lower = path.lower()
    if lower.endswith(".pdf"):
        return pdf_text(path)
    if lower.endswith((".txt", ".csv", ".qfx", ".ofx", ".qif")):
        return plain_text(path)
    return None


def redact(text: str) -> str:
    text = SSN_RE.sub("[REDACTED-SSN]", text)
    text = EIN_RE.sub("[REDACTED-EIN]", text)
    return LONG_NUMBER_RE.sub("[REDACTED-NUMBER]", text)


def summarize_mediainfo(info: Optional[dict]) -> dict:
    """Small, flat summary of the interesting mediainfo fields."""
    out: dict = {}
    if not info:
        return out
    for track in info.get("media", {}).get("track", []):
        kind = track.get("@type")
        if kind == "General":
            for k in ("Format", "Duration", "Encoded_Application", "Encoded_Library_Name",
                      "Recorded_Date", "Encoded_Date"):
                if track.get(k):
                    out[f"general_{k.lower()}"] = track[k]
            extra = track.get("extra") or {}
            for k in ("com_apple_quicktime_make", "com_apple_quicktime_model"):
                if extra.get(k):
                    out[k] = extra[k]
        elif kind == "Video" and "video_format" not in out:
            out.update(video_format=track.get("Format"), width=track.get("Width"),
                       height=track.get("Height"), frame_rate=track.get("FrameRate"))
        elif kind == "Audio" and "audio_format" not in out:
            out.update(audio_format=track.get("Format"), channels=track.get("Channels"),
                       sample_rate=track.get("SamplingRate"))
    return {k: v for k, v in out.items() if v is not None}
