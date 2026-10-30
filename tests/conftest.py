import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clauditing  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") or os.environ.get("CLAUDITING_FFMPEG_DIR")), reason="ffmpeg not installed"
)


def make_video(path, duration=40, size="640x360", loud=((10, 13), (28, 31)), audio=True):
    """A synthetic 'gameplay' video: moving test pattern, quiet tone with loud bursts."""
    vol = "+".join(f"between(t,{a},{b})" for a, b in loud) or "0"
    cmd = [
        clauditing._tool("ffmpeg"),
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=30:duration={duration}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}"]
        cmd += ["-filter_complex", f"[1:a]volume='if({vol},1.0,0.03)':eval=frame[a]", "-map", "0:v", "-map", "[a]"]
        cmd += ["-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def gameplay(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    return make_video(str(d / "gameplay.mp4"))


@pytest.fixture(scope="session")
def silent_vertical(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    return make_video(str(d / "vertical.mp4"), duration=12, size="360x640", loud=(), audio=False)


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return tmp_path
