# Changelog

All notable changes to Clauditing are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-10-30

### Added
- Caption position option (`caption_y` in settings, `--caption-y` on the command line, `caption_y` in batch plans) to keep captions clear of stream banners and watermarks.
- A note when a video only has room for fewer clips than you asked for.
- Full test suite (editor and app server) and CI on Python 3.9, 3.12 and 3.13.
- Tests run on Windows with the bundled ffmpeg before every `.exe` build.
- `pyproject.toml`: install with `pip install -e .` and run `clauditing` from anywhere.
- Project docs: README with screenshots, changelog, contributing guide, security policy, issue and pull request templates.

### Fixed
- Captions and handles containing `%{...}` are now shown as plain text instead of being treated as ffmpeg expressions.

### Changed
- GitHub Actions updated to their latest major versions.
- Code formatted and linted with ruff.

## [0.2.0] - 2026-10-30

### Added
- Windows desktop app: **Made by Claude** library, **Make clips** on your PC, **Settings**.
- GitHub token sign-in. Clip batches are read from releases tagged `clips-…`.
- In-app player with copy-caption and a **Posted** tick box.
- `tools/publish_batch.py` to publish a batch of clips with thumbnails and a manifest.
- GitHub Actions workflow that builds `Clauditing.exe` with ffmpeg bundled.

## [0.1.0] - 2026-10-30

### Added
- Command-line editor with `scan`, `cut`, `auto` and `batch`.
- Highlight detection from audio loudness and on-screen motion.
- Blur, zoom and crop vertical layouts at 1080×1920.
- Captions, @handle, loudness normalisation and fades.

[0.3.0]: https://github.com/elloexe/clauditing/compare/89cd0ed...main
[0.2.0]: https://github.com/elloexe/clauditing/compare/10d5c3e...89cd0ed
[0.1.0]: https://github.com/elloexe/clauditing/commit/10d5c3e
