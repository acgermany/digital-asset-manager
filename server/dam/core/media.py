"""Browser-friendly renditions: thumbnails, display images, video proxies.

Everything is cached under DATA_DIR/cache keyed by content checksum, so every
copy of the same asset shares one rendition and nothing is regenerated.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")
CACHE = os.path.join(DATA_DIR, "cache")
THUMB_SIZE = 400
DISPLAY_MAX = 2560
PROXY_HEIGHT = int(os.environ.get("PROXY_HEIGHT", "720"))

BROWSER_IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".bmp"}
HEIF = {".heic", ".heif"}
RAW = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}
CONVERT_IMAGES = HEIF | RAW | {".tif", ".tiff"}
VIDEO = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".mpg", ".mpeg",
         ".mts", ".m2ts", ".ts", ".3gp", ".mxf"}
AUDIO = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif", ".wma",
         ".amr", ".caf", ".m4b"}
TEXT = {".txt", ".md", ".csv", ".json", ".xml", ".log", ".yml", ".yaml", ".ini", ".conf", ".qif"}

PLAYABLE_VIDEO = {"h264", "vp8", "vp9", "av1"}
PLAYABLE_AUDIO = {"aac", "mp3", "opus", "vorbis"}
PLAYABLE_CONTAINERS = {".mp4", ".m4v", ".mov", ".webm"}

# Rendering thumbnails is CPU-heavy; a grid of 50 must not start 50 ffmpegs at once.
_render_slots = threading.BoundedSemaphore(int(os.environ.get("RENDER_CONCURRENCY", "2")))


def view_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in BROWSER_IMAGES or ext in CONVERT_IMAGES:
        return "image"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT:
        return "text"
    return "other"


def _cache_path(kind: str, sha: str, ext: str) -> str:
    d = os.path.join(CACHE, kind, sha[:2])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{sha}{ext}")


def _run(cmd: list, timeout: int = 120) -> bool:
    if not shutil.which(cmd[0]):
        log.warning("%s not installed", cmd[0])
        return False
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("%s timed out", cmd[0])
        return False
    if r.returncode != 0:
        log.info("%s failed: %s", cmd[0], r.stderr.decode("utf-8", "replace")[-300:])
    return r.returncode == 0


def _atomic(out: str, produce: Callable[[str], bool]) -> Optional[str]:
    tmp = out + ".tmp" + os.path.splitext(out)[1]
    try:
        if produce(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            return out
    except OSError as e:
        log.info("render failed: %s", e)
    if os.path.exists(tmp):
        os.unlink(tmp)
    return None


# ---------------------------------------------------------------- images

def display_image(path: str, sha: str) -> Optional[str]:
    """A JPEG the browser can show, for formats it can't (HEIC, RAW, TIFF)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in BROWSER_IMAGES:
        return path
    out = _cache_path("display", sha, ".jpg")
    if os.path.exists(out):
        return out
    with _render_slots:
        if os.path.exists(out):
            return out

        def produce(tmp: str) -> bool:
            if ext in HEIF:
                return _run(["heif-convert", "-q", "88", path, tmp])
            if ext in RAW:
                # Cameras embed a full-size JPEG preview; much faster than demosaicing.
                for tag in ("-JpgFromRaw", "-PreviewImage"):
                    with open(tmp, "wb") as f:
                        r = subprocess.run(["exiftool", "-b", tag, path], stdout=f,
                                           stderr=subprocess.DEVNULL, timeout=60)
                    if r.returncode == 0 and os.path.getsize(tmp) > 1000:
                        return True
                return False
            return _run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
                         f"scale='min({DISPLAY_MAX},iw)':-2", "-frames:v", "1", tmp])
        return _atomic(out, produce)


# ---------------------------------------------------------------- probing

def probe(path: str) -> dict:
    """ffprobe summary: duration, size, codecs."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,width,height:format=duration,format_name",
                            "-of", "json", path], capture_output=True, timeout=30)
        data = json.loads(r.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    out: dict = {"duration": None, "video_codec": None, "audio_codec": None}
    try:
        out["duration"] = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not out["video_codec"] and s.get("codec_name") not in ("mjpeg", "png"):
            out.update(video_codec=s.get("codec_name"), width=s.get("width"), height=s.get("height"))
        elif s.get("codec_type") == "audio" and not out["audio_codec"]:
            out["audio_codec"] = s.get("codec_name")
    return out


def browser_playable(path: str, info: dict) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext not in PLAYABLE_CONTAINERS:
        return False
    if info.get("video_codec") not in PLAYABLE_VIDEO:
        return False
    return info.get("audio_codec") in (None, *PLAYABLE_AUDIO)


# ---------------------------------------------------------------- thumbnails

def thumbnail(path: str, sha: str, vtype: str) -> Optional[str]:
    if vtype not in ("image", "video", "pdf"):
        return None
    out = _cache_path("thumbs", sha, ".jpg")
    if os.path.exists(out):
        return out
    src = path
    if vtype == "image":
        src = display_image(path, sha)
        if not src:
            return None
    with _render_slots:
        if os.path.exists(out):
            return out

        def produce(tmp: str) -> bool:
            if vtype == "pdf":
                base = tmp[:-4]
                ok = _run(["pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-scale-to", str(THUMB_SIZE),
                           "-singlefile", src, base], timeout=60)
                if ok and base + ".jpg" != tmp:
                    os.replace(base + ".jpg", tmp)
                return ok
            if vtype == "video":
                dur = probe(src).get("duration") or 0
                seek = f"{min(max(dur * 0.1, 0), 30):.2f}"
                return _run(["ffmpeg", "-v", "error", "-y", "-ss", seek, "-i", src, "-frames:v", "1",
                             "-vf", f"scale={THUMB_SIZE}:-2", tmp], timeout=60)
            return _run(["ffmpeg", "-v", "error", "-y", "-i", src, "-frames:v", "1",
                         "-vf", f"scale='min({THUMB_SIZE},iw)':-2", tmp], timeout=60)
        return _atomic(out, produce)


# ---------------------------------------------------------------- video proxies

def proxy_path(sha: str) -> str:
    return _cache_path("proxies", sha, ".mp4")


def has_proxy(sha: str) -> bool:
    return os.path.exists(proxy_path(sha))


def make_proxy(path: str, sha: str, report: Callable[[float], bool]) -> Optional[str]:
    """Transcode to H.264/AAC MP4 (<= PROXY_HEIGHT), reporting 0..1. report() False cancels."""
    out = proxy_path(sha)
    if os.path.exists(out):
        return out
    duration = probe(path).get("duration") or 0
    tmp = out + ".tmp.mp4"
    cmd = ["nice", "-n", "10", "ffmpeg", "-v", "error", "-y", "-i", path,
           "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)'",
           "-c:a", "aac", "-b:a", "128k", "-ac", "2",
           "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", tmp]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cancelled = False
    last = -1.0
    for line in proc.stdout:
        if line.startswith("out_time_us=") and duration:
            try:
                frac = min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration)
            except ValueError:
                continue
            if frac - last >= 0.02:
                last = frac
                if not report(frac):
                    cancelled = True
                    proc.kill()
                    break
    proc.wait()
    if cancelled or proc.returncode != 0:
        if not cancelled:
            log.warning("proxy failed for %s: %s", path, (proc.stderr.read() or "")[-500:])
        if os.path.exists(tmp):
            os.unlink(tmp)
        if cancelled:
            return None
        raise RuntimeError("ffmpeg could not create a preview for this video")
    os.replace(tmp, out)
    report(1.0)
    return out
