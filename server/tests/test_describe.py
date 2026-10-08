import os
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from dam.api.app import app
from dam.core import ai, describe, media, settings_store
from dam.core.ingest import process, register
from dam.models import Asset, Embedding

client = TestClient(app)
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_parse_splits_tags():
    desc, tags = describe.parse("Two kids on a beach at sunset.\n\n**Tags:** beach, kids, sunset, #ocean.")
    assert desc == "Two kids on a beach at sunset."
    assert tags == ["beach", "kids", "sunset", "ocean"]
    assert describe.parse("Just a caption.") == ("Just a caption.", [])


def test_prompt_placeholders_do_not_break_on_other_braces():
    a = Asset(kind="video")
    cfg = {"describe_video_prompt": "{frames} frames of a {duration} video {unknown} {}",
           "describe_image_prompt": "x"}
    assert describe.build_prompt(a, cfg, {"frames": 8, "duration": "20-second"}) == \
        "8 frames of a 20-second video {unknown} {}"


def ingest(session, hot, lib, rel, data):
    p = os.path.join(hot, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    st = os.stat(p)
    i = register(session, hot, p, st.st_size, st.st_mtime)
    session.commit()
    process(session, i, {"hotfolder": hot, "library": lib, "delete_mode": "delete"})
    return session.get(Asset, i.asset_id)


def png(color: str) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=64x48",
                           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                          capture_output=True, check=True).stdout


@needs_ffmpeg
def test_batch_describes_images_skips_critical_and_drops_stale_embedding(session, dirs, monkeypatch, tmp_path):
    hot, lib = dirs
    monkeypatch.setattr(media, "CACHE", str(tmp_path / "cache"))
    calls = []

    def fake_model(cfg, prompt, images, timeout=0):
        calls.append((prompt, len(images)))
        return "A red square on a plain background.\nTags: red, square"
    monkeypatch.setattr(describe, "call_model", fake_model)

    photo = ingest(session, hot, lib, "Family/red.png", png("red"))
    receipt = ingest(session, hot, lib, "Taxes/receipt.png", png("blue"))
    receipt.value_tier = "critical"
    session.add(Embedding(asset_id=photo.id, model="m", source_hash="old", embedding=[0.0] * 768))
    session.commit()

    cfg = settings_store.all_settings(session, fresh=True)
    assert describe.pending_ids(session, cfg) == [photo.id]          # critical excluded by default
    progress = describe.run_batch(session, cfg, lambda p: True)
    assert progress["described"] == 1 and calls[0][1] == 1
    session.refresh(photo)
    assert photo.description.startswith("A red square") and photo.description_tags == ["red", "square"]
    assert "description: A red square" in photo.search_text
    assert session.query(Embedding).filter_by(asset_id=photo.id).count() == 0
    assert describe.coverage(session, cfg)["pending"] == 0

    detail = client.get(f"/api/assets/{photo.id}").json()["asset"]
    assert detail["description_model"] == "qwen3-vl:8b" and detail["description_tags"] == ["red", "square"]


@needs_ffmpeg
def test_video_sends_sampled_frames(session, dirs, monkeypatch, tmp_path):
    hot, lib = dirs
    vid = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=10",
                    "-c:v", "libx264", str(vid)], check=True)
    asset = ingest(session, hot, lib, "Home/clip.mp4", vid.read_bytes())
    seen = {}

    def fake_model(cfg, prompt, images, timeout=0):
        seen.update(prompt=prompt, n=len(images))
        return "A test pattern."
    monkeypatch.setattr(describe, "call_model", fake_model)
    settings_store.update(session, {"describe_video_frames": 4})
    session.commit()
    assert describe.describe_asset(session, settings_store.all_settings(session, fresh=True), asset)
    assert seen["n"] == 4 and "4 frames" in seen["prompt"] and "4-second" in seen["prompt"]


def test_unreadable_file_is_recorded_not_fatal(session, dirs, monkeypatch):
    hot, lib = dirs
    asset = ingest(session, hot, lib, "Family/broken.jpg", b"not really a jpeg")
    monkeypatch.setattr(describe, "call_model", lambda *a, **k: pytest.fail("model should not be called"))
    ok = describe.describe_asset(session, settings_store.all_settings(session, fresh=True), asset)
    assert not ok and asset.description_error


def test_model_outage_pauses_instead_of_failing(session, dirs, monkeypatch):
    hot, lib = dirs
    asset = ingest(session, hot, lib, "Family/x.png", b"x")
    monkeypatch.setattr(describe, "frames_for", lambda a, p, c: ([b"jpg"], {}))

    def down(*a, **k):
        raise ai.AIUnavailable("Ollama not reachable")
    monkeypatch.setattr(describe, "call_model", down)
    with pytest.raises(ai.AIUnavailable):
        describe.describe_asset(session, settings_store.all_settings(session, fresh=True), asset)
    session.refresh(asset)
    assert asset.description_error is None and asset.described_at is None   # still pending


def test_describe_settings_validation(session):
    assert client.put("/api/config", json={"describe_video_frames": 99}).status_code == 400
    assert client.put("/api/config", json={"describe_keep_alive": "forever"}).status_code == 400
    r = client.put("/api/config", json={"describe_image_prompt": "   "})
    assert r.json()["settings"]["describe_image_prompt"] == settings_store.DEFAULT_IMAGE_PROMPT


def test_thinking_overrun_is_retried_with_bigger_budget(monkeypatch):
    budgets = []

    def fake_chat(cfg, prompt, images, num_predict, timeout):
        budgets.append(num_predict)
        return ("", True) if num_predict < 3000 else ("A photo.", False)
    monkeypatch.setattr(describe, "_chat", fake_chat)
    assert describe.call_model({"describe_max_tokens": 1536}, "p", [b"x"]) == "A photo."
    assert budgets == [1536, 3072]


def test_thinking_overrun_twice_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(describe, "_chat", lambda *a: ("", True))
    with pytest.raises(describe.DescribeError, match="Max output"):
        describe.call_model({"describe_max_tokens": 1000}, "p", [b"x"])
