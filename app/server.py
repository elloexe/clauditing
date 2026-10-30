"""
Clauditing desktop backend.

A tiny local web server (127.0.0.1 only) that the app window talks to:
  - signs in to GitHub with a personal access token
  - lists the clip batches Claude published as GitHub Releases (tag "clips-...")
  - downloads clips, tracks which ones you've posted
  - runs the Clauditing editor on videos from your PC
"""

import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))
import clauditing  # noqa: E402

UI_DIR = (
    os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(ROOT)), "app", "ui")
    if getattr(sys, "frozen", False)
    else os.path.join(ROOT, "ui")
)
VERSION = "0.3.0"
DEFAULT_REPO = "elloexe/clauditing"
GITHUB = "https://api.github.com"


# ---------------------------------------------------------------- storage


def app_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "Clauditing")
    os.makedirs(d, exist_ok=True)
    return d


def default_library():
    videos = os.path.join(os.path.expanduser("~"), "Videos")
    return os.path.join(videos if os.path.isdir(videos) else os.path.expanduser("~"), "Clauditing")


class Settings:
    DEFAULTS = {
        "token": "",
        "user": None,
        "repo": DEFAULT_REPO,
        "library": "",
        "handle": "",
        "layout": "blur",
        "clip_length": 15,
        "captions": clauditing.DEFAULT_CONFIG["default_captions"],
        "posted": {},
    }

    def __init__(self):
        self.path = os.path.join(app_dir(), "settings.json")
        self.data = dict(self.DEFAULTS)
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, ValueError):
                pass
        if not self.data["library"]:
            self.data["library"] = default_library()
        self.lock = threading.Lock()

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def public(self):
        d = {k: v for k, v in self.data.items() if k != "token"}
        d["signed_in"] = bool(self.data["token"])
        return d

    def editor_config(self):
        cfg = dict(clauditing.DEFAULT_CONFIG)
        cfg.update(
            {
                "handle": self["handle"],
                "layout": self["layout"],
                "clip_length": self["clip_length"],
                "default_captions": self["captions"] or cfg["default_captions"],
            }
        )
        return cfg


# ---------------------------------------------------------------- GitHub


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class GitHubError(Exception):
    pass


def gh_request(token, path, accept="application/vnd.github+json", follow_assets=False):
    url = path if path.startswith("http") else GITHUB + path
    req = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "Clauditing/" + VERSION,
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": "Bearer " + token} if token else {}),
        },
    )
    opener = urllib.request.build_opener(NoRedirect) if follow_assets else urllib.request.build_opener()
    try:
        return opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        if follow_assets and e.code in (301, 302, 303, 307, 308):
            # asset downloads redirect to a signed storage URL that must NOT get our token
            return urllib.request.urlopen(
                urllib.request.Request(e.headers["Location"], headers={"User-Agent": "Clauditing/" + VERSION}),
                timeout=60,
            )
        if e.code == 401:
            raise GitHubError("GitHub says that token isn't valid (it may have expired).")
        if e.code == 404:
            raise GitHubError("Couldn't find that repository, or the token can't see it.")
        if e.code == 403:
            raise GitHubError("GitHub refused the request (token permissions or rate limit).")
        raise GitHubError(f"GitHub error {e.code}")
    except urllib.error.URLError:
        raise GitHubError("Can't reach GitHub. Check your internet connection.")


def gh_json(token, path):
    with gh_request(token, path) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- jobs


class Jobs:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, fn, *args):
        jid = secrets.token_hex(6)
        job = {"id": jid, "state": "running", "progress": 0.0, "log": [], "result": None, "error": None}
        with self.lock:
            self.jobs[jid] = job

        def runner():
            try:
                job["result"] = fn(job, *args)
                job["state"] = "done"
                job["progress"] = 1.0
            except Exception as e:  # report every failure to the UI
                job["state"] = "error"
                job["error"] = str(e)

        threading.Thread(target=runner, daemon=True).start()
        return jid

    def get(self, jid):
        return self.jobs.get(jid)


# ---------------------------------------------------------------- the app


class App:
    def __init__(self):
        self.settings = Settings()
        self.jobs = Jobs()
        self.window = None  # set by main.py when running as a desktop window
        self.cache_dir = os.path.join(app_dir(), "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.edit_lock = threading.Lock()

    # ---- paths
    def library(self):
        d = self.settings["library"]
        os.makedirs(d, exist_ok=True)
        return d

    def media_url(self, path):
        rel = os.path.relpath(path, self.library()).replace(os.sep, "/")
        return "/media/" + urllib.parse.quote(rel)

    # ---- account
    def state(self):
        return {
            "version": VERSION,
            "settings": self.settings.public(),
            "library": self.library(),
            "desktop": self.window is not None,
        }

    def signin(self, token, repo):
        token = (token or "").strip()
        repo = (repo or DEFAULT_REPO).strip().strip("/")
        if repo.startswith("https://github.com/"):
            repo = repo[len("https://github.com/") :]
        if not token:
            raise GitHubError("Paste your GitHub token first.")
        user = gh_json(token, "/user")
        gh_json(token, f"/repos/{repo}")  # make sure the token can see the repo
        self.settings["token"] = token
        self.settings["repo"] = repo
        self.settings["user"] = {
            "login": user["login"],
            "avatar": user.get("avatar_url", ""),
            "name": user.get("name") or user["login"],
        }
        self.settings.save()
        return self.state()

    def signout(self):
        self.settings["token"] = ""
        self.settings["user"] = None
        self.settings.save()
        return self.state()

    # ---- Claude's batches
    def batches(self):
        token, repo = self.settings["token"], self.settings["repo"]
        if not token:
            return []
        releases = gh_json(token, f"/repos/{repo}/releases?per_page=30")
        out = []
        for rel in releases:
            if not rel["tag_name"].startswith("clips-"):
                continue
            assets = {a["name"]: a for a in rel["assets"]}
            manifest = None
            if "manifest.json" in assets:
                manifest = self._cached_json(token, assets["manifest.json"])
            clips = []
            entries = (manifest or {}).get("clips") or [
                {"file": n} for n in sorted(assets) if n.lower().endswith(".mp4")
            ]
            for e in entries:
                a = assets.get(e["file"])
                if not a:
                    continue
                key = f"{rel['tag_name']}/{e['file']}"
                local = os.path.join(self.library(), rel["tag_name"], e["file"])
                thumb = assets.get(e.get("thumb") or os.path.splitext(e["file"])[0] + ".jpg")
                clips.append(
                    {
                        "key": key,
                        "file": e["file"],
                        "asset_id": a["id"],
                        "size": a["size"],
                        "title": e.get("title") or os.path.splitext(e["file"])[0],
                        "caption": e.get("caption", ""),
                        "hashtags": e.get("hashtags", ""),
                        "post_on": e.get("post_on", ""),
                        "thumb": f"/thumb/{thumb['id']}" if thumb else "",
                        "downloaded": os.path.exists(local),
                        "local": local if os.path.exists(local) else "",
                        "url": self.media_url(local) if os.path.exists(local) else "",
                        "posted": key in self.settings["posted"],
                    }
                )
            title = (manifest or {}).get("title") or rel["name"] or rel["tag_name"]
            out.append(
                {
                    "tag": rel["tag_name"],
                    "title": title,
                    "notes": (manifest or {}).get("notes") or rel.get("body") or "",
                    "date": rel.get("published_at") or rel.get("created_at"),
                    "clips": clips,
                }
            )
        return out

    def _cached_json(self, token, asset):
        p = os.path.join(self.cache_dir, f"asset_{asset['id']}_{asset['updated_at'].replace(':', '')}.json")
        if not os.path.exists(p):
            with gh_request(token, asset["url"], accept="application/octet-stream", follow_assets=True) as r:
                data = r.read()
            with open(p, "wb") as f:
                f.write(data)
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def thumb_path(self, asset_id):
        p = os.path.join(self.cache_dir, f"thumb_{int(asset_id)}.jpg")
        if not os.path.exists(p):
            repo = self.settings["repo"]
            with gh_request(
                self.settings["token"],
                f"/repos/{repo}/releases/assets/{int(asset_id)}",
                accept="application/octet-stream",
                follow_assets=True,
            ) as r:
                data = r.read()
            with open(p, "wb") as f:
                f.write(data)
        return p

    def download(self, tag, file, asset_id):
        safe = os.path.basename(file)
        dest_dir = os.path.join(self.library(), os.path.basename(tag))
        dest = os.path.join(dest_dir, safe)
        token, repo = self.settings["token"], self.settings["repo"]

        def work(job):
            os.makedirs(dest_dir, exist_ok=True)
            with gh_request(
                token,
                f"/repos/{repo}/releases/assets/{int(asset_id)}",
                accept="application/octet-stream",
                follow_assets=True,
            ) as r:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            job["progress"] = got / total
            os.replace(tmp, dest)
            return {"local": dest, "url": self.media_url(dest)}

        return self.jobs.start(work)

    def set_posted(self, key, posted):
        p = dict(self.settings["posted"])
        if posted:
            p[key] = datetime.now().isoformat(timespec="minutes")
        else:
            p.pop(key, None)
        self.settings["posted"] = p
        self.settings.save()
        return {"posted": posted}

    # ---- editing on this PC
    def make(self, video, count, length, layout):
        if not video or not os.path.exists(video):
            raise clauditing.ClauditingError("Choose a video first.")
        cfg = self.settings.editor_config()
        stamp = datetime.now().strftime("%Y-%m-%d %H.%M")
        out_dir = os.path.join(self.library(), "Made on this PC", stamp)

        def work(job):
            with self.edit_lock:
                old_log = clauditing.LOG
                clauditing.LOG = lambda msg: job["log"].append(str(msg).strip())
                try:
                    clauditing.check_tools()
                    job["log"].append("Finding the best moments...")

                    def prog(i, n):
                        job["progress"] = 0.1 + 0.9 * (i / max(n, 1))
                        if i < n:
                            job["log"].append(f"Editing clip {i + 1} of {n}...")

                    job["progress"] = 0.05
                    made = clauditing.make_week(video, out_dir, cfg, int(count), float(length), layout, prog)
                    for m in made:
                        self._make_thumb(m["file"])
                    job["log"].append(f"Done! {len(made)} clips saved.")
                    return {"folder": out_dir}
                finally:
                    clauditing.LOG = old_log

        return self.jobs.start(work)

    def _make_thumb(self, mp4):
        jpg = os.path.splitext(mp4)[0] + ".jpg"
        try:
            clauditing.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", mp4, "-frames:v", "1", "-vf", "scale=360:-2", jpg]
            )
        except clauditing.ClauditingError:
            pass

    def local_clips(self):
        root = os.path.join(self.library(), "Made on this PC")
        groups = []
        if not os.path.isdir(root):
            return groups
        for name in sorted(os.listdir(root), reverse=True):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            clips = []
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".mp4"):
                    p = os.path.join(d, f)
                    jpg = os.path.splitext(p)[0] + ".jpg"
                    key = "local/" + name + "/" + f
                    clips.append(
                        {
                            "key": key,
                            "file": f,
                            "title": os.path.splitext(f)[0],
                            "url": self.media_url(p),
                            "local": p,
                            "downloaded": True,
                            "thumb": self.media_url(jpg) if os.path.exists(jpg) else "",
                            "caption": "",
                            "hashtags": "",
                            "posted": key in self.settings["posted"],
                        }
                    )
            if clips:
                groups.append({"tag": "local/" + name, "title": name, "folder": d, "clips": clips})
        return groups

    def pick_video(self):
        if self.window is None:
            return {"path": ""}
        import webview

        types = ("Videos (*.mp4;*.mov;*.mkv;*.webm;*.avi)", "All files (*.*)")
        res = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=types)
        return {"path": res[0] if res else ""}

    def open_path(self, path):
        path = path or self.library()
        if not os.path.exists(path):
            raise FileNotFoundError("That file or folder doesn't exist anymore.")
        if os.name == "nt":
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if os.path.isfile(path) else "", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path) if os.path.isfile(path) else path])
        return {"ok": True}

    def save_settings(self, data):
        for k in ("handle", "layout", "clip_length", "captions", "library"):
            if k in data:
                v = data[k]
                if k == "captions":
                    v = [c.strip() for c in v if c.strip()]
                if k == "clip_length":
                    v = max(5, min(60, float(v)))
                if k == "layout" and v not in ("blur", "crop", "zoom"):
                    continue
                self.settings[k] = v
        self.settings.save()
        return self.state()

    def open_url(self, url):
        if url.startswith("https://github.com/"):
            import webbrowser

            webbrowser.open(url)
        return {"ok": True}


# ---------------------------------------------------------------- HTTP


def make_handler(app, key):
    routes = {
        "state": lambda b: app.state(),
        "signin": lambda b: app.signin(b.get("token"), b.get("repo")),
        "signout": lambda b: app.signout(),
        "batches": lambda b: app.batches(),
        "local": lambda b: app.local_clips(),
        "download": lambda b: {"job": app.download(b["tag"], b["file"], b["asset_id"])},
        "posted": lambda b: app.set_posted(b["key"], bool(b.get("posted"))),
        "make": lambda b: {
            "job": app.make(b.get("video"), b.get("count", 7), b.get("length", 15), b.get("layout", "blur"))
        },
        "job": lambda b: app.jobs.get(b["id"]) or {"state": "error", "error": "unknown job"},
        "pick_video": lambda b: app.pick_video(),
        "open": lambda b: app.open_path(b.get("path")),
        "settings": lambda b: app.save_settings(b),
        "open_url": lambda b: app.open_url(b.get("url", "")),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _authed(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return secrets.compare_digest(self.headers.get("X-Clauditing-Key") or (q.get("k") or [""])[0], key)

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if not path.startswith("/api/") or not self._authed():
                return self._send(403, {"error": "forbidden"})
            name = path[5:]
            if name not in routes:
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                self._send(200, routes[name](body))
            except (GitHubError, clauditing.ClauditingError, FileNotFoundError, KeyError, ValueError) as e:
                self._send(400, {"error": str(e)})
            except Exception as e:
                self._send(500, {"error": f"Something went wrong: {e}"})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            if path.startswith("/media/") or path.startswith("/thumb/"):
                if not self._authed():
                    return self._send(403, b"forbidden", "text/plain")
                try:
                    if path.startswith("/thumb/"):
                        f = app.thumb_path(path[7:])
                    else:
                        lib = os.path.realpath(app.library())
                        f = os.path.realpath(os.path.join(lib, path[7:]))
                        if not f.startswith(lib + os.sep):
                            return self._send(403, b"forbidden", "text/plain")
                    return self._file(f)
                except (GitHubError, ValueError, OSError):
                    return self._send(404, b"not found", "text/plain")
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = os.path.realpath(os.path.join(UI_DIR, rel))
            if not f.startswith(os.path.realpath(UI_DIR)) or not os.path.isfile(f):
                return self._send(404, b"not found", "text/plain")
            self._file(f)

        def _file(self, f):
            if not os.path.isfile(f):
                return self._send(404, b"not found", "text/plain")
            size = os.path.getsize(f)
            ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else max(0, size - int(b))
                end = int(b) if (a and b) else size - 1
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(f, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                try:
                    while left > 0:
                        chunk = fh.read(min(1 << 16, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    return Handler


def start_server(app, port=0):
    key = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, key))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, key, f"http://127.0.0.1:{httpd.server_address[1]}/#k={key}"


if __name__ == "__main__":
    # dev mode: run the backend and open the UI in a normal browser
    port = int(os.environ.get("CLAUDITING_PORT", "0"))
    app = App()
    httpd, key, url = start_server(app, port)
    print(url, flush=True)
    while True:
        time.sleep(3600)
