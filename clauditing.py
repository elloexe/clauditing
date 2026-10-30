#!/usr/bin/env python3
"""
Clauditing - turns raw Roblox gameplay into vertical Shorts / TikToks.

Commands
  scan  VIDEO              find the best moments (loud + action-heavy) and make a preview sheet
  cut   VIDEO --start --end  edit one clip into a 1080x1920 vertical video
  auto  VIDEO --count 7    scan + cut the top N moments in one go (one clip per day)
  batch PLAN.json          cut a list of clips described in a JSON plan

Needs: ffmpeg + ffprobe on PATH, Python 3.8+, numpy.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920

DEFAULT_CONFIG = {
    "handle": "",                 # e.g. "@mygame" - shown small at the bottom
    "layout": "blur",             # blur | crop | zoom
    "clip_length": 15,            # seconds, used by scan/auto
    "caption_color": "white",
    "caption_outline": "black",
    "caption_size": 92,
    "font": "",                   # path to a .ttf/.otf; empty = auto-detect a bold font
    "speed": 1.0,
    "loudnorm": True,
    "default_captions": [
        "wait for it...",
        "this was NOT planned",
        "how did this even work",
        "rate this 1-10",
        "new update goes crazy",
        "bro thought he was safe",
        "I can't believe this happened",
    ],
}

FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Black.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    # Linux
    "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


# ---------------------------------------------------------------- helpers

class ClauditingError(Exception):
    pass


def die(msg):
    raise ClauditingError(msg)


def _tool(name):
    """Find ffmpeg/ffprobe: bundled next to the app first, then PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    dirs = [os.environ.get("CLAUDITING_FFMPEG_DIR", "")]
    if getattr(sys, "frozen", False):
        dirs += [os.path.join(getattr(sys, "_MEIPASS", ""), "bin"), os.path.dirname(sys.executable)]
    dirs.append(os.path.join(HERE, "bin"))
    for d in dirs:
        if d and os.path.exists(os.path.join(d, exe)):
            return os.path.join(d, exe)
    return shutil.which(name) or name


NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # don't flash console windows on Windows
LOG = print  # the desktop app swaps this for its own progress logger


def load_config(path=None):
    cfg = dict(DEFAULT_CONFIG)
    path = path or os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def check_tools():
    for tool in ("ffmpeg", "ffprobe"):
        if not os.path.exists(_tool(tool)) and not shutil.which(tool):
            die(f"{tool} not found. Install ffmpeg (https://ffmpeg.org/download.html) and try again.")


def run(cmd, capture=False):
    cmd = [_tool(cmd[0])] + list(cmd[1:])
    res = subprocess.run(cmd, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                         stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    if res.returncode != 0:
        die("ffmpeg failed:\n" + res.stderr.decode(errors="replace")[-1500:])
    return res.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", path], capture=True)
    info = json.loads(out)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        die(f"{path} has no video stream")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    return {
        "duration": float(info["format"]["duration"]),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "has_audio": has_audio,
    }


def find_font(cfg):
    if cfg.get("font") and os.path.exists(cfg["font"]):
        return cfg["font"]
    local = os.path.join(HERE, "fonts")
    if os.path.isdir(local):
        for name in sorted(os.listdir(local)):
            if name.lower().endswith((".ttf", ".otf")):
                return os.path.join(local, name)
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def ff_path(p):
    """Escape a file path for use inside an ffmpeg filter argument."""
    p = os.path.abspath(p).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")


def fmt_time(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


# ---------------------------------------------------------------- scanning

def audio_energy(path, duration, step):
    """RMS loudness per `step` seconds."""
    sr = 8000
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
               "-f", "s16le", "-"], capture=True)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n = int(duration / step)
    hop = int(sr * step)
    out = np.zeros(n)
    for i in range(n):
        seg = a[i * hop:(i + 1) * hop]
        out[i] = np.sqrt(np.mean(seg ** 2)) if len(seg) else 0
    return out


def motion_energy(path, duration, step):
    """Average frame-to-frame pixel change per `step` seconds (tiny grayscale frames)."""
    fw, fh = 96, 54
    fps = 1.0 / step
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-vf",
               f"fps={fps},scale={fw}:{fh},format=gray", "-f", "rawvideo", "-"], capture=True)
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, fh, fw).astype(np.float32)
    n = int(duration / step)
    diff = np.zeros(n)
    if len(frames) > 1:
        d = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
        m = min(n - 1, len(d))
        diff[1:m + 1] = d[:m]
    return diff


def robust_z(x):
    med = np.median(x)
    mad = np.median(np.abs(x - med)) + 1e-6
    return np.clip((x - med) / (1.4826 * mad), -10, 10)


def smooth(x, k):
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def find_moments(path, clip_len, top):
    info = probe(path)
    dur = info["duration"]
    step = 0.25
    motion = motion_energy(path, dur, step)
    score = robust_z(motion) * 0.4
    if info["has_audio"]:
        audio = audio_energy(path, dur, step)
        n = min(len(audio), len(score))
        score = score[:n] + robust_z(audio[:n]) * 0.6
    score = smooth(score, int(1.0 / step))

    moments = []
    taken = np.zeros(len(score), dtype=bool)
    gap = int(clip_len / step)
    order = np.argsort(score)[::-1]
    for idx in order:
        if len(moments) >= top:
            break
        if taken[max(0, idx - gap):idx + gap].any():
            continue
        peak = idx * step
        start = max(0.0, peak - clip_len * 0.4)
        end = min(dur, start + clip_len)
        start = max(0.0, end - clip_len)
        if end - start < min(3.0, dur):
            continue
        taken[idx] = True
        moments.append({"start": round(start, 2), "end": round(end, 2),
                        "peak": round(peak, 2), "score": round(float(score[idx]), 2)})
    return moments, info


def contact_sheet(path, moments, out_jpg):
    """One labelled thumbnail per moment, tiled, so you can eyeball them quickly."""
    if not moments:
        return None
    tmp = tempfile.mkdtemp()
    try:
        thumbs = []
        for i, m in enumerate(moments):
            t = os.path.join(tmp, f"{i:02d}.jpg")
            label = f"#{i + 1}  {fmt_time(m['start'])}-{fmt_time(m['end'])}"
            label_file = os.path.join(tmp, f"{i:02d}.txt")
            with open(label_file, "w", encoding="utf-8") as f:
                f.write(label)
            font = find_font(DEFAULT_CONFIG)
            dt = (f",drawtext=textfile='{ff_path(label_file)}':fontsize=28:fontcolor=white:"
                  f"box=1:boxcolor=black@0.6:boxborderw=8:x=10:y=10"
                  + (f":fontfile='{ff_path(font)}'" if font else ""))
            run(["ffmpeg", "-v", "error", "-y", "-ss", str(m["peak"]), "-i", path,
                 "-frames:v", "1", "-vf", "scale=480:270" + dt, t])
            thumbs.append(t)
        cols = min(3, len(thumbs))
        rows = (len(thumbs) + cols - 1) // cols
        run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(tmp, "%02d.jpg"),
             "-vf", f"tile={cols}x{rows}:padding=6:color=black", "-frames:v", "1", out_jpg])
        return out_jpg
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- editing

def build_filter(info, cfg, caption_file, handle_file, font, layout, speed, clip_dur):
    v = []
    if layout == "crop":
        v.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base]")
    else:
        # blurred, darkened full-screen background + sharp gameplay on top
        fg_w = W
        if layout == "zoom":
            v.append(f"[0:v]split=2[bgsrc][fgsrc]")
            v.append(f"[fgsrc]crop='min(iw,ih*1.2)':ih,scale={W}:-2,setsar=1[fg]")
        else:
            v.append(f"[0:v]split=2[bgsrc][fgsrc]")
            v.append(f"[fgsrc]scale={fg_w}:-2,setsar=1[fg]")
        v.append(f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                 f"gblur=sigma=30,eq=brightness=-0.12,setsar=1[bg]")
        v.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")

    chain = "[base]"
    steps = []
    fontarg = f":fontfile='{ff_path(font)}'" if font else ""
    if caption_file:
        steps.append(
            f"drawtext=textfile='{ff_path(caption_file)}'{fontarg}:fontsize={cfg['caption_size']}:"
            f"fontcolor={cfg['caption_color']}:borderw=7:bordercolor={cfg['caption_outline']}:"
            f"line_spacing=14:text_align=center:x=(w-text_w)/2:y=h*0.13"
        )
    if handle_file:
        steps.append(
            f"drawtext=textfile='{ff_path(handle_file)}'{fontarg}:fontsize=44:fontcolor=white@0.85:"
            f"borderw=4:bordercolor=black@0.7:x=(w-text_w)/2:y=h*0.86"
        )
    if speed != 1.0:
        steps.append(f"setpts=PTS/{speed}")
    out_dur = clip_dur / speed
    steps.append(f"fade=t=in:st=0:d=0.25,fade=t=out:st={max(0, out_dur - 0.35):.2f}:d=0.35")
    steps.append("fps=30,format=yuv420p")
    v.append(chain + ",".join(steps) + "[v]")

    a = None
    if info["has_audio"]:
        parts = []
        if speed != 1.0:
            s, tempo = speed, []
            while s > 2.0:
                tempo.append("atempo=2.0"); s /= 2.0
            while s < 0.5:
                tempo.append("atempo=0.5"); s /= 0.5
            tempo.append(f"atempo={s:.4f}")
            parts += tempo
        if cfg.get("loudnorm", True):
            parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        parts.append(f"afade=t=in:st=0:d=0.2,afade=t=out:st={max(0, out_dur - 0.35):.2f}:d=0.35")
        parts.append("aresample=48000")
        a = "[0:a]" + ",".join(parts) + "[a]"
    return ";".join(v + ([a] if a else []))


def text_filter_supports_align():
    out = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-h", "filter=drawtext"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW).stdout.decode(errors="replace")
    return "text_align" in out


def cut_clip(src, start, end, caption, out_path, cfg, layout=None, speed=None):
    info = probe(src)
    start = max(0.0, float(start))
    end = min(info["duration"], float(end))
    if end <= start:
        die(f"end ({end}) must be after start ({start})")
    speed = float(speed or cfg.get("speed", 1.0))
    if (end - start) / speed > 60:
        LOG("  note: clip is over 60s; YouTube Shorts must be 60s or less for some accounts.")
    layout = layout or cfg.get("layout", "blur")
    font = find_font(cfg)
    if not font:
        LOG("  warning: no bold font found; put a .ttf in the fonts/ folder for nicer captions.")

    tmp = tempfile.mkdtemp()
    try:
        cap_file = None
        if caption:
            cap_file = os.path.join(tmp, "caption.txt")
            lines = textwrap.wrap(caption.strip(), width=18) or [caption]
            with open(cap_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        handle_file = None
        if cfg.get("handle"):
            handle_file = os.path.join(tmp, "handle.txt")
            with open(handle_file, "w", encoding="utf-8") as f:
                f.write(cfg["handle"])

        fc = build_filter(info, cfg, cap_file, handle_file, font, layout, speed, end - start)
        if not text_filter_supports_align():
            fc = fc.replace(":text_align=center", "")

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
               "-i", src, "-filter_complex", fc, "-map", "[v]"]
        if info["has_audio"]:
            cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-movflags", "+faststart", out_path]
        run(cmd)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    LOG(f"  saved {os.path.basename(out_path)}  ({fmt_time(start)}-{fmt_time(end)}{', ' + str(speed) + 'x' if speed != 1 else ''})")
    return out_path


# ---------------------------------------------------------------- commands

def cmd_scan(args, cfg):
    length = args.length or cfg["clip_length"]
    moments, info = find_moments(args.video, length, args.top)
    base = os.path.splitext(os.path.basename(args.video))[0]
    os.makedirs(args.out, exist_ok=True)
    jpath = os.path.join(args.out, f"{base}_moments.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"video": os.path.abspath(args.video), "moments": moments}, f, indent=2)
    sheet = contact_sheet(args.video, moments, os.path.join(args.out, f"{base}_sheet.jpg"))
    print(f"{args.video}: {fmt_time(info['duration'])} long, "
          f"{'with' if info['has_audio'] else 'NO'} audio")
    for i, m in enumerate(moments, 1):
        print(f"  #{i}  {fmt_time(m['start'])} - {fmt_time(m['end'])}   score {m['score']}")
    print(f"moments -> {jpath}")
    if sheet:
        print(f"preview -> {sheet}")


def cmd_cut(args, cfg):
    out = args.output or os.path.join(
        args.out, f"{os.path.splitext(os.path.basename(args.video))[0]}_{args.start}-{args.end}.mp4")
    cut_clip(args.video, args.start, args.end, args.caption, out, cfg, args.layout, args.speed)


def cmd_auto(args, cfg):
    length = args.length or cfg["clip_length"]
    return make_week(args.video, args.out, cfg, args.count, length, args.layout)


def make_week(video, out_dir, cfg, count=7, length=None, layout=None, progress=None):
    """Scan a video and cut its best `count` moments in time order. Returns clip info dicts."""
    length = length or cfg["clip_length"]
    moments, _ = find_moments(video, length, count)
    moments.sort(key=lambda m: m["start"])
    caps = cfg.get("default_captions") or [""]
    base = os.path.splitext(os.path.basename(video))[0]
    LOG(f"making {len(moments)} clips from {os.path.basename(video)}")
    made = []
    for i, m in enumerate(moments, 1):
        if progress:
            progress(i - 1, len(moments))
        out = os.path.join(out_dir, f"{base}_day{i:02d}.mp4")
        cap = caps[(i - 1) % len(caps)]
        cut_clip(video, m["start"], m["end"], cap, out, cfg, layout)
        made.append({"file": out, "caption": cap, "start": m["start"], "end": m["end"]})
    if progress:
        progress(len(moments), len(moments))
    return made


def cmd_batch(args, cfg):
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    clips = plan["clips"] if isinstance(plan, dict) else plan
    plan_dir = os.path.dirname(os.path.abspath(args.plan))
    for i, c in enumerate(clips, 1):
        src = c["video"] if os.path.isabs(c["video"]) else os.path.join(plan_dir, c["video"])
        name = c.get("name") or f"clip{i:02d}"
        out = os.path.join(args.out, f"{name}.mp4")
        cut_clip(src, c["start"], c["end"], c.get("caption", ""), out, cfg,
                 c.get("layout"), c.get("speed"))


def main():
    p = argparse.ArgumentParser(prog="clauditing", description="Roblox gameplay -> vertical Shorts/TikToks")
    p.add_argument("--config", help="path to config.json (default: next to this script)")
    p.add_argument("--out", default="out", help="output folder (default: out)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="find the best moments in a video")
    s.add_argument("video")
    s.add_argument("--top", type=int, default=9)
    s.add_argument("--length", type=float)

    c = sub.add_parser("cut", help="edit one clip")
    c.add_argument("video")
    c.add_argument("--start", type=float, required=True)
    c.add_argument("--end", type=float, required=True)
    c.add_argument("--caption", default="")
    c.add_argument("--layout", choices=["blur", "crop", "zoom"])
    c.add_argument("--speed", type=float)
    c.add_argument("-o", "--output")

    a = sub.add_parser("auto", help="scan + cut the top N moments")
    a.add_argument("video")
    a.add_argument("--count", type=int, default=7)
    a.add_argument("--length", type=float)
    a.add_argument("--layout", choices=["blur", "crop", "zoom"])

    b = sub.add_parser("batch", help="cut clips listed in a JSON plan")
    b.add_argument("plan")

    args = p.parse_args()
    try:
        check_tools()
        cfg = load_config(args.config)
        {"scan": cmd_scan, "cut": cmd_cut, "auto": cmd_auto, "batch": cmd_batch}[args.cmd](args, cfg)
    except ClauditingError as e:
        print(f"clauditing: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
