import json
import os
import time
import urllib.error
import urllib.request

import pytest
from conftest import needs_ffmpeg

from app import server


@pytest.fixture
def running(appdata, tmp_path):
    app = server.App()
    app.settings["library"] = str(tmp_path / "library")
    httpd, key, url = server.start_server(app)
    base = url.split("#")[0]

    def call(name, body=None, key_override=None):
        req = urllib.request.Request(
            base + "api/" + name,
            data=json.dumps(body or {}).encode(),
            headers={"X-Clauditing-Key": key if key_override is None else key_override},
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    yield app, base, key, call
    httpd.shutdown()


def get(url, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}))


def test_serves_the_ui(running):
    _, base, _, _ = running
    html = get(base).read().decode()
    assert "<title>Clauditing</title>" in html
    assert get(base + "logo.svg").headers["Content-Type"] == "image/svg+xml"


def test_api_needs_the_key(running):
    _, _, _, call = running
    with pytest.raises(urllib.error.HTTPError) as e:
        call("state", key_override="wrong")
    assert e.value.code == 403


def test_unknown_route_is_404(running):
    _, _, _, call = running
    with pytest.raises(urllib.error.HTTPError) as e:
        call("does_not_exist")
    assert e.value.code == 404


def test_state_never_leaks_the_token(running):
    app, _, _, call = running
    app.settings["token"] = "secret-token"
    st = call("state")
    assert st["settings"]["signed_in"] is True
    assert "secret-token" not in json.dumps(st)


def test_settings_are_validated_and_saved(running, appdata):
    _, _, _, call = running
    st = call("settings", {"handle": "@me", "layout": "sideways", "clip_length": 999, "captions": ["a", " ", "b "]})
    s = st["settings"]
    assert s["handle"] == "@me" and s["layout"] == "blur" and s["clip_length"] == 60 and s["captions"] == ["a", "b"]
    saved = json.load(open(os.path.join(os.environ["APPDATA"], "Clauditing", "settings.json")))
    assert saved["handle"] == "@me"


def test_posted_toggle(running):
    app, _, _, call = running
    call("posted", {"key": "clips-x/a.mp4", "posted": True})
    assert "clips-x/a.mp4" in app.settings["posted"]
    call("posted", {"key": "clips-x/a.mp4", "posted": False})
    assert "clips-x/a.mp4" not in app.settings["posted"]


def test_media_supports_ranges_and_blocks_escapes(running):
    app, base, key, _ = running
    lib = app.library()
    with open(os.path.join(lib, "clip.mp4"), "wb") as f:
        f.write(bytes(range(256)) * 4)
    r = get(base + "media/clip.mp4?k=" + key, {"Range": "bytes=10-19"})
    assert r.status == 206 and r.read() == bytes(range(10, 20))
    assert r.headers["Content-Range"] == "bytes 10-19/1024"
    for bad in ["media/../../etc/passwd", "media/%2e%2e/%2e%2e/etc/passwd"]:
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base + bad + "?k=" + key)
        assert e.value.code in (403, 404)
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "media/clip.mp4")  # no key
    assert e.value.code == 403


def test_signin_errors_are_friendly(running, monkeypatch):
    _, _, _, call = running

    def boom(token, path):
        raise server.GitHubError("GitHub says that token isn't valid (it may have expired).")

    monkeypatch.setattr(server, "gh_json", boom)
    with pytest.raises(urllib.error.HTTPError) as e:
        call("signin", {"token": "bad", "repo": "elloexe/clauditing"})
    assert e.value.code == 400 and "token" in json.loads(e.value.read())["error"]


def test_signin_and_batches_from_releases(running, monkeypatch):
    app, _, _, call = running
    releases = [
        {
            "tag_name": "clips-2026-11-02",
            "name": "Week of Nov 2",
            "body": "",
            "published_at": "2026-11-01T10:00:00Z",
            "assets": [
                {"name": "manifest.json", "id": 1, "url": "u1", "updated_at": "t", "size": 10},
                {"name": "day01.mp4", "id": 2, "url": "u2", "updated_at": "t", "size": 1000},
                {"name": "day01.jpg", "id": 3, "url": "u3", "updated_at": "t", "size": 10},
            ],
        },
        {"tag_name": "app", "name": "App", "assets": []},  # not a clips release: ignored
    ]
    manifest = {
        "title": "Week of Nov 2",
        "notes": "hi",
        "clips": [{"file": "day01.mp4", "title": "Boss", "caption": "c"}],
    }

    def fake_json(token, path):
        if path == "/user":
            return {"login": "you", "name": "You", "avatar_url": ""}
        if path.startswith("/repos/you/repo/releases"):
            return releases
        if path == "/repos/you/repo":
            return {"full_name": "you/repo"}
        raise AssertionError(path)

    monkeypatch.setattr(server, "gh_json", fake_json)
    monkeypatch.setattr(server.App, "_cached_json", lambda self, token, asset: manifest)
    st = call("signin", {"token": "t", "repo": "https://github.com/you/repo"})
    assert st["settings"]["repo"] == "you/repo" and st["settings"]["user"]["login"] == "you"
    batches = call("batches")
    assert len(batches) == 1
    clip = batches[0]["clips"][0]
    assert clip["title"] == "Boss" and clip["thumb"] == "/thumb/3" and clip["downloaded"] is False


@needs_ffmpeg
def test_make_job_runs_end_to_end(running, gameplay):
    app, _, _, call = running
    job = call("make", {"video": gameplay, "count": 2, "length": 6, "layout": "zoom"})["job"]
    for _ in range(300):
        j = call("job", {"id": job})
        if j["state"] != "running":
            break
        time.sleep(0.2)
    assert j["state"] == "done", j.get("error")
    groups = call("local")
    assert len(groups) == 1 and len(groups[0]["clips"]) == 2
    assert all(c["thumb"] for c in groups[0]["clips"])


def test_make_without_video_is_an_error(running):
    _, _, _, call = running
    with pytest.raises(urllib.error.HTTPError) as e:
        call("make", {"video": "/nope.mp4"})
    assert e.value.code == 400
