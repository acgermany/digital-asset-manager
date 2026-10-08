import os
import shutil
import subprocess

import httpx
import pytest

from dam.core import ai, describe, settings_store, transcribe
from dam.core.ingest import process, register
from dam.models import Asset, Embedding

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def ingest_bytes(session, hot, lib, rel, data):
    p = os.path.join(hot, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    st = os.stat(p)
    i = register(session, hot, p, st.st_size, st.st_mtime)
    session.commit()
    process(session, i, {"hotfolder": hot, "library": lib, "delete_mode": "delete"})
    return session.get(Asset, i.asset_id)


def make_video(tmp_path, name, audio=True):
    out = tmp_path / name
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac"]
    subprocess.run(args + ["-c:v", "libx264", str(out)], check=True)
    return out.read_bytes()


def cfg(session, **extra):
    settings_store.update(session, extra) if extra else None
    session.commit()
    return settings_store.all_settings(session, fresh=True)


@needs_ffmpeg
def test_video_without_audio_is_no_speech(session, dirs, tmp_path, monkeypatch):
    hot, lib = dirs
    asset = ingest_bytes(session, hot, lib, "Home/silent.mp4", make_video(tmp_path, "s.mp4", audio=False))
    monkeypatch.setattr(transcribe, "call_server", lambda *a, **k: pytest.fail("no audio, no call"))
    assert transcribe.transcribe_asset(session, cfg(session), asset) == "no_speech"
    assert asset.transcribed_at and asset.transcript == ""


@needs_ffmpeg
def test_transcript_is_saved_searchable_and_invalidates_embedding(session, dirs, tmp_path, monkeypatch):
    hot, lib = dirs
    asset = ingest_bytes(session, hot, lib, "Home/party.mp4", make_video(tmp_path, "p.mp4"))
    session.add(Embedding(asset_id=asset.id, model="m", source_hash="old", embedding=[0.0] * 768))
    session.commit()
    monkeypatch.setattr(transcribe, "call_server", lambda c, w, d=0: {
        "text": "Happy birthday Emma.", "language": "en",
        "segments": [{"start": 0.0, "end": 1.2, "text": "Happy birthday Emma."},
                     {"start": 1.2, "end": 2.0, "text": "Thank you."}]})
    assert transcribe.transcribe_asset(session, cfg(session), asset) == "transcribed"
    assert asset.transcript_language == "en" and "transcript: Happy birthday Emma." in asset.search_text
    assert [s["text"] for s in asset.transcript_segments] == ["Happy birthday Emma."]  # hallucinated tail dropped
    assert session.query(Embedding).filter_by(asset_id=asset.id).count() == 0
    assert transcribe.coverage(session, cfg(session))["transcribed"] == 1


@needs_ffmpeg
def test_hallucination_only_counts_as_no_speech(session, dirs, tmp_path, monkeypatch):
    hot, lib = dirs
    asset = ingest_bytes(session, hot, lib, "Home/hum.mp4", make_video(tmp_path, "h.mp4"))
    monkeypatch.setattr(transcribe, "call_server", lambda *a, **k: {"text": " Thank you.", "language": "en", "segments": []})
    assert transcribe.transcribe_asset(session, cfg(session), asset) == "no_speech"


@needs_ffmpeg
def test_server_down_leaves_it_pending(session, dirs, tmp_path, monkeypatch):
    hot, lib = dirs
    asset = ingest_bytes(session, hot, lib, "Home/x.mp4", make_video(tmp_path, "x.mp4"))

    def down(*a, **k):
        raise ai.AIUnavailable("not reachable")
    monkeypatch.setattr(transcribe, "call_server", down)
    with pytest.raises(ai.AIUnavailable):
        transcribe.transcribe_asset(session, cfg(session), asset)
    assert transcribe.pending_ids(session, cfg(session)) == [asset.id]


@needs_ffmpeg
def test_too_long_is_skipped_with_reason(session, dirs, tmp_path, monkeypatch):
    hot, lib = dirs
    asset = ingest_bytes(session, hot, lib, "Home/long.mp4", make_video(tmp_path, "l.mp4"))
    monkeypatch.setattr(transcribe.media, "probe", lambda p: {"audio_codec": "aac", "duration": 7200})
    assert transcribe.transcribe_asset(session, cfg(session, transcribe_max_minutes=60), asset) == "failed"
    assert "60-minute" in asset.transcript_error


def test_video_prompt_includes_transcript():
    a = Asset(kind="video", transcript="We are at the lake.")
    c = {"describe_video_prompt": "Summarize.", "describe_image_prompt": "x", "describe_use_transcript": True}
    assert "We are at the lake." in describe.build_prompt(a, c, {})
    assert "lake" not in describe.build_prompt(a, {**c, "describe_use_transcript": False}, {})


def _whisper_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8178/", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not (shutil.which("say") and shutil.which("ffmpeg") and _whisper_up()),
                    reason="needs macOS `say` and whisper-server on 127.0.0.1:8178")
def test_live_whisper_server(tmp_path):
    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "The quick brown fox jumps over the lazy dog."], check=True)
    wav = transcribe.extract_audio(str(aiff), 10)
    try:
        r = transcribe.call_server({"transcribe_url": "http://127.0.0.1:8178", "transcribe_api": "whispercpp",
                                    "transcribe_language": "auto"}, wav)
    finally:
        os.unlink(wav)
    assert "quick brown fox" in r["text"].lower()
    assert r["language"] in ("english", "en")
    assert r["segments"] and r["segments"][0]["end"] > 0
