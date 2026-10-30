# Clauditing

Turns raw Roblox gameplay into ready-to-post vertical clips (1080×1920) for YouTube Shorts and TikTok.

It finds the best moments automatically (loud audio + lots of action), cuts them, puts the gameplay on a blurred vertical background, adds a big caption and your @handle, evens out the volume, and adds quick fades.

## Setup (once)

1. Install **Python 3.8+** and **ffmpeg** (Windows: `winget install ffmpeg`, Mac: `brew install ffmpeg`).
2. `pip install -r requirements.txt`
3. Copy `config.example.json` to `config.json` and put your handle in it.

## Use it

Make a week of clips from one recording (one per day):

```
python clauditing.py auto my_gameplay.mp4 --count 7
```

Clips land in `out/` as `my_gameplay_day01.mp4`, `day02`, …

Other commands:

| Command | What it does |
|---|---|
| `python clauditing.py scan video.mp4` | Lists the best moments and saves a preview picture (`out/video_sheet.jpg`) |
| `python clauditing.py cut video.mp4 --start 72 --end 88 --caption "bro really tried"` | Edits one exact clip |
| `python clauditing.py batch plan.json` | Edits a list of clips with custom captions (see `plan.example.json`) |

Options for `cut` / `auto`: `--layout blur|crop|zoom` (blur = whole screen visible, crop = fills the screen, zoom = in between), `--speed 1.25`.

## Settings (`config.json`)

| Setting | Meaning |
|---|---|
| `handle` | Text at the bottom, e.g. `@mygame` |
| `layout` | Default layout: `blur`, `crop` or `zoom` |
| `clip_length` | Seconds per clip for `scan`/`auto` (Shorts: keep under 60) |
| `caption_size`, `caption_color`, `caption_outline` | Caption style |
| `font` | Path to a `.ttf`; or drop one into a `fonts/` folder |
| `default_captions` | Captions `auto` rotates through |

## Posting

Upload the finished clips with a scheduler that's approved for TikTok (Buffer, Later, Metricool, Blotato) and queue one per day to TikTok + YouTube Shorts.
