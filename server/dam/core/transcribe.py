"""Speech-to-text for videos and audio.

Audio is extracted with ffmpeg (16 kHz mono WAV) and sent to a Whisper server:
whisper.cpp's `whisper-server` (/inference) by default, or any OpenAI-compatible
server (/v1/audio/transcriptions).
"""
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Asset, Embedding
from . import ai, events, media
from .assets import build_search_text

log = logging.getLogger(__name__)

TIER_ORDER = "CASE a.value_tier WHEN 'precious' THEN 0 WHEN 'critical' THEN 1 " \
             "WHEN 'standard' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"

# Whisper invents these on silence or music; a transcript that is only these is "no speech".
HALLUCINATIONS = {
    "", "you", "thank you", "thank you.", "thanks for watching!", "thanks for watching.",
    "thank you for watching.", "thank you for watching!", "bye.", "bye!", "[music]", "(music)",
    "[blank_audio]", "[silence]", "♪", "...", "subtitles by the amara.org community",
}


class NoSpeech(Exception):
    """The file has no audio track or nothing intelligible was said."""


class TranscribeError(Exception):
    """This file can't be transcribed (unreadable audio, too long)."""


# ---------------------------------------------------------------- eligibility

def _eligible_sql(cfg: dict) -> tuple:
    kinds = [k for k in (cfg.get("transcribe_kinds") or []) if k in ("video", "audio")] or ["none"]
    clause = "a.kind = ANY(:kinds)"
    if not cfg.get("transcribe_critical"):
        clause += " AND a.value_tier <> 'critical'"
    return clause, {"kinds": kinds}


def pending_where(cfg: dict, retranscribe_before: Optional[str] = None) -> tuple:
    """SQL condition (alias `a`) for assets that still need a transcript."""
    clause, params = _eligible_sql(cfg)
    if retranscribe_before:
        clause += " AND (a.transcribed_at IS NULL OR a.transcribed_at < CAST(:before AS timestamptz))"
        params["before"] = retranscribe_before
    else:
        clause += " AND a.transcribed_at IS NULL AND a.transcript_error IS NULL"
    return clause, params


def pending_ids(session: Session, cfg: dict, limit: Optional[int] = None,
                retranscribe_before: Optional[str] = None) -> list:
    clause, params = pending_where(cfg, retranscribe_before)
    sql = f"SELECT a.id FROM assets a WHERE {clause} ORDER BY {TIER_ORDER}, a.id"
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit
    return [r[0] for r in session.execute(text(sql), params)]


def coverage(session: Session, cfg: dict) -> dict:
    clause, params = _eligible_sql(cfg)
    row = session.execute(text(f"""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE a.transcribed_at IS NOT NULL),
               COUNT(*) FILTER (WHERE a.transcribed_at IS NOT NULL AND coalesce(a.transcript, '') = ''),
               COUNT(*) FILTER (WHERE a.transcript_error IS NOT NULL AND a.transcribed_at IS NULL)
        FROM assets a WHERE {clause}"""), params).one()
    eligible, done, silent, failed = row
    return {"eligible": eligible, "transcribed": done, "no_speech": silent, "failed": failed,
            "pending": max(0, eligible - done - failed)}


# ---------------------------------------------------------------- audio

def extract_audio(path: str, max_minutes: int) -> str:
    """16 kHz mono WAV in a temp file (caller deletes). Raises NoSpeech/TranscribeError."""
    info = media.probe(path)
    if not info.get("audio_codec"):
        raise NoSpeech("No audio track")
    duration = info.get("duration") or 0
    if duration and duration > max_minutes * 60:
        raise TranscribeError(f"Longer than the {max_minutes}-minute limit (Configure → AI)")
    fd, out = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                            "-c:a", "pcm_s16le", out], capture_output=True,
                           timeout=max(120, int(duration * 0.5) + 60))
    except subprocess.TimeoutExpired:
        os.unlink(out)
        raise TranscribeError("Timed out extracting the audio")
    if r.returncode != 0 or os.path.getsize(out) < 1000:
        log.info("audio extraction failed: %s", r.stderr.decode("utf-8", "replace")[-400:])
        os.unlink(out)
        raise TranscribeError("Could not read the audio (damaged or unsupported format)")
    return out


# ---------------------------------------------------------------- server call

def _segments(raw: list) -> list:
    out = []
    for s in raw or []:
        t = (s.get("text") or "").strip()
        if t:
            out.append({"start": round(float(s.get("start", 0)), 2), "end": round(float(s.get("end", 0)), 2),
                        "text": t})
    return out


def call_server(cfg: dict, wav_path: str, duration: float = 0) -> dict:
    """Returns {"text", "language", "segments"}. Raises ai.AIUnavailable if the server is down."""
    url = cfg["transcribe_url"].rstrip("/")
    lang = cfg.get("transcribe_language") or "auto"
    timeout = max(300.0, duration * 2)
    with open(wav_path, "rb") as f:
        files = {"file": ("audio.wav", f, "audio/wav")}
        if cfg.get("transcribe_api") == "openai":
            data = {"model": cfg.get("transcribe_model") or "whisper-1", "response_format": "verbose_json"}
            if lang != "auto":
                data["language"] = lang
            endpoint = f"{url}/v1/audio/transcriptions"
        else:
            data = {"response_format": "verbose_json", "language": lang, "temperature": "0.0"}
            endpoint = f"{url}/inference"
        try:
            r = httpx.post(endpoint, data=data, files=files, timeout=timeout)
        except httpx.HTTPError as e:
            raise ai.AIUnavailable(f"Transcription server not reachable at {url}: {e}") from e
    if r.status_code >= 500 or r.status_code == 404:
        raise ai.AIUnavailable(f"Transcription server error {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        raise TranscribeError(f"Server rejected the audio ({r.status_code}): {r.text[:200]}")
    body = r.json()
    if "error" in body and not body.get("text"):
        raise TranscribeError(str(body["error"])[:300])
    language = body.get("language") or body.get("detected_language")
    return {"text": (body.get("text") or "").strip(), "language": language,
            "segments": _segments(body.get("segments"))}


def server_status(cfg: dict) -> dict:
    url = cfg["transcribe_url"].rstrip("/")
    try:
        r = httpx.get(url + ("/v1/models" if cfg.get("transcribe_api") == "openai" else "/"), timeout=4)
        return {"reachable": r.status_code < 500, "error": None if r.status_code < 500 else f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        return {"reachable": False, "error": f"Not reachable at {url}: {e}"}


def is_hallucination(text_: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text_.strip().lower())
    return cleaned in HALLUCINATIONS or not re.search(r"\w", cleaned)


# ---------------------------------------------------------------- per asset / batch

def _file_path(asset: Asset) -> Optional[str]:
    for f in sorted(asset.files, key=lambda f: (f.source != "library", f.id)):
        if os.path.isfile(f.path):
            return f.path
    return None


def _save(session: Session, cfg: dict, asset: Asset, text_: str, language: Optional[str], segments: list) -> None:
    asset.transcript = text_
    asset.transcript_segments = segments
    asset.transcript_language = language
    asset.transcript_model = "whisper.cpp" if cfg.get("transcribe_api") != "openai" else cfg.get("transcribe_model")
    asset.transcribed_at = datetime.now(timezone.utc)
    asset.transcript_error = None
    asset.search_text = build_search_text(asset, session)
    session.query(Embedding).filter(Embedding.asset_id == asset.id).delete()  # stale now


def transcribe_asset(session: Session, cfg: dict, asset: Asset) -> str:
    """Returns "transcribed", "no_speech" or "failed". Raises ai.AIUnavailable if the server is down."""
    path = _file_path(asset)
    if not path:
        asset.transcript_error = "No copy of this file is reachable"
        session.commit()
        return "failed"
    try:
        wav = extract_audio(path, int(cfg.get("transcribe_max_minutes") or 120))
    except NoSpeech:
        _save(session, cfg, asset, "", None, [])
        session.commit()
        return "no_speech"
    except TranscribeError as e:
        asset.transcript_error = str(e)
        session.commit()
        return "failed"
    try:
        result = call_server(cfg, wav, media.probe(path).get("duration") or 0)
    except TranscribeError as e:
        asset.transcript_error = str(e)
        session.commit()
        return "failed"
    finally:
        os.unlink(wav)
    if is_hallucination(result["text"]):
        _save(session, cfg, asset, "", result["language"], [])
        outcome = "no_speech"
    else:
        segments = [s for s in result["segments"] if not is_hallucination(s["text"])]
        _save(session, cfg, asset, result["text"], result["language"], segments)
        outcome = "transcribed"
    events.log(session, "transcribed", actor="model", asset_id=asset.id,
               outcome=outcome, language=result["language"], chars=len(asset.transcript or ""))
    session.commit()
    return outcome
