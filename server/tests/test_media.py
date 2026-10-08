import os
import shutil
import subprocess

import pytest

from dam.core import media

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_video(path, codec_args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=duration=2", *codec_args, path], check=True)


def test_non_browser_video_gets_proxy_and_thumbnail(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "CACHE", str(tmp_path / "cache"))
    src = str(tmp_path / "old.avi")
    make_video(src, ["-c:v", "mpeg4", "-c:a", "mp3"])
    info = media.probe(src)
    assert info["video_codec"] == "mpeg4" and not media.browser_playable(src, info)

    fractions = []
    out = media.make_proxy(src, "a" * 64, lambda f: fractions.append(f) or True)
    assert out and os.path.getsize(out) > 0 and fractions[-1] == 1.0
    proxy = media.probe(out)
    assert proxy["video_codec"] == "h264" and media.browser_playable(out, proxy)

    thumb = media.thumbnail(src, "a" * 64, "video")
    assert thumb and os.path.getsize(thumb) > 0


def test_h264_mp4_plays_directly(tmp_path):
    src = str(tmp_path / "ok.mp4")
    make_video(src, ["-c:v", "libx264", "-c:a", "aac"])
    assert media.browser_playable(src, media.probe(src))


def test_view_types():
    assert media.view_type("IMG_1.HEIC") == "image"
    assert media.view_type("clip.MTS") == "video"
    assert media.view_type("scan.pdf") == "pdf"
    assert media.view_type("memo.m4a") == "audio"
