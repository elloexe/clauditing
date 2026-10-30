# Contributing to Clauditing

Thanks for wanting to help! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug or asking for a feature

Open an [issue](https://github.com/elloexe/clauditing/issues/new/choose) and pick a template. For bugs, the most useful things to include are:

- what you ran or clicked
- what you expected and what happened instead
- the error message, or the log shown in the **Make clips** screen
- your Windows version (or OS) and Clauditing version (shown at the bottom of **Settings**)

## Working on the code

```bash
git clone https://github.com/elloexe/clauditing && cd clauditing
pip install -e ".[dev,app]"
```

You'll also need [ffmpeg](https://ffmpeg.org/download.html) on your `PATH` for the tests.

Before opening a pull request:

```bash
ruff format .
ruff check .
pytest
```

CI runs the same three checks on every pull request.

### Where things live

| Area | File |
|---|---|
| Highlight scoring, cutting, layouts, captions | `clauditing.py` |
| App backend (GitHub, downloads, jobs) | `app/server.py` |
| App interface | `app/ui/index.html` |
| Windows build | `.github/workflows/build-exe.yml` |

### Guidelines

- Keep the editor (`clauditing.py`) usable on its own, without the app.
- New editing options should work from the command line, in `plan.json` and in `config.json`.
- Add a test for anything you fix or add. `tests/conftest.py` can make synthetic test videos, so tests never need real footage.
- Add a line to `CHANGELOG.md` under an **Unreleased** heading.

## Testing the app without Windows

```bash
python -m app.server
```

This prints a local URL. Open it in a browser to use the full interface. The only part that needs the desktop window is the native file picker; in a browser you paste a file path instead.
