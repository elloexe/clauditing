import json
import os

import numpy as np
import pytest
from conftest import needs_ffmpeg

import clauditing

pytestmark = needs_ffmpeg


def stream_info(path):
    out = clauditing.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path], capture=True
    )
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return int(v["width"]), int(v["height"]), float(info["format"]["duration"]), info["streams"]


# ---------------------------------------------------------------- helpers


def test_robust_z_is_clipped():
    x = np.zeros(100)
    x[50] = 1e9
    z = clauditing.robust_z(x)
    assert z.max() == 10 and z.min() >= -10


def test_fmt_time():
    assert clauditing.fmt_time(0) == "0:00.0"
    assert clauditing.fmt_time(75.5) == "1:15.5"


def test_ff_path_escapes_colons_and_quotes():
    p = clauditing.ff_path("C:\\Users\\me\\it's.txt")
    assert "\\:" in p and "\\'" in p and "\\\\" not in p


def test_missing_file_raises_clean_error(tmp_path):
    with pytest.raises(clauditing.ClauditingError):
        clauditing.probe(str(tmp_path / "nope.mp4"))


# ---------------------------------------------------------------- finding moments


def test_finds_the_loud_moments(gameplay):
    moments, info = clauditing.find_moments(gameplay, clip_len=8, top=2)
    assert info["has_audio"] and abs(info["duration"] - 40) < 0.5
    assert len(moments) == 2
    peaks = sorted(m["peak"] for m in moments)
    assert 9 <= peaks[0] <= 14 and 27 <= peaks[1] <= 32


def test_moments_do_not_overlap_and_stay_in_bounds(gameplay):
    moments, info = clauditing.find_moments(gameplay, clip_len=6, top=6)
    spans = sorted((m["start"], m["end"]) for m in moments)
    for (_, a1), (b0, _) in zip(spans, spans[1:]):
        assert a1 <= b0 + 1e-6
    for s, e in spans:
        assert 0 <= s < e <= info["duration"] + 1e-6


def test_works_without_audio(silent_vertical):
    moments, info = clauditing.find_moments(silent_vertical, clip_len=5, top=2)
    assert not info["has_audio"] and moments


# ---------------------------------------------------------------- editing


@pytest.mark.parametrize("layout", ["blur", "crop", "zoom"])
def test_cut_makes_vertical_1080x1920(gameplay, tmp_path, layout):
    out = clauditing.cut_clip(
        gameplay, 10, 15, "wait for it...", str(tmp_path / f"{layout}.mp4"), clauditing.DEFAULT_CONFIG, layout
    )
    w, h, dur, streams = stream_info(out)
    assert (w, h) == (1080, 1920)
    assert abs(dur - 5) < 0.3
    assert any(s["codec_type"] == "audio" for s in streams)


def test_speed_shortens_the_clip(gameplay, tmp_path):
    out = clauditing.cut_clip(gameplay, 0, 6, "", str(tmp_path / "fast.mp4"), clauditing.DEFAULT_CONFIG, speed=1.5)
    assert abs(stream_info(out)[2] - 4) < 0.3


def test_tricky_captions_and_handle(gameplay, tmp_path):
    cfg = dict(clauditing.DEFAULT_CONFIG, handle="@it's_me: 100%")
    cap = 'he said: "it\'s 100% over" \\ {not} [really] %{pts}'
    out = clauditing.cut_clip(gameplay, 1, 3, cap, str(tmp_path / "tricky.mp4"), cfg, caption_y=0.3)
    assert os.path.getsize(out) > 0


def test_vertical_silent_source(silent_vertical, tmp_path):
    out = clauditing.cut_clip(silent_vertical, 0, 4, "hi", str(tmp_path / "v.mp4"), clauditing.DEFAULT_CONFIG, "crop")
    w, h, _, streams = stream_info(out)
    assert (w, h) == (1080, 1920)
    assert not any(s["codec_type"] == "audio" for s in streams)


def test_end_before_start_is_an_error(gameplay, tmp_path):
    with pytest.raises(clauditing.ClauditingError):
        clauditing.cut_clip(gameplay, 10, 5, "", str(tmp_path / "x.mp4"), clauditing.DEFAULT_CONFIG)


def test_make_week_returns_clips_in_time_order(gameplay, tmp_path):
    logs, ticks = [], []
    old = clauditing.LOG
    clauditing.LOG = logs.append
    try:
        made = clauditing.make_week(
            gameplay, str(tmp_path), clauditing.DEFAULT_CONFIG, count=5, length=10,
            progress=lambda i, n: ticks.append((i, n)),
        )  # fmt: skip
    finally:
        clauditing.LOG = old
    assert 1 <= len(made) <= 4
    assert [m["start"] for m in made] == sorted(m["start"] for m in made)
    assert all(os.path.exists(m["file"]) for m in made)
    assert ticks[-1] == (len(made), len(made))
    assert any("only room for" in line for line in logs)  # asked for 5, 40s only fits 4


def test_cli_batch(gameplay, tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"clips": [{"name": "a", "video": gameplay, "start": 1, "end": 3, "caption": "yo"}]}))
    monkeypatch.setattr("sys.argv", ["clauditing", "--out", str(tmp_path / "out"), "batch", str(plan)])
    clauditing.main()
    assert (tmp_path / "out" / "a.mp4").exists()
