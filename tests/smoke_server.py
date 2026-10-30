"""Starts the app backend, loads the UI and calls a few API routes."""
import json
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APPDATA"] = tempfile.mkdtemp()

from app.server import App, start_server  # noqa: E402

app = App()
httpd, key, url = start_server(app)
base = url.split("#")[0]

html = urllib.request.urlopen(base).read().decode()
assert "Clauditing" in html, "UI did not load"


def call(name, body=None):
    req = urllib.request.Request(base + "api/" + name, data=json.dumps(body or {}).encode(),
                                 headers={"X-Clauditing-Key": key, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


state = call("state")
assert state["settings"]["signed_in"] is False
assert call("batches") == []
assert call("local") == []

try:
    urllib.request.urlopen(urllib.request.Request(base + "api/state", data=b"{}"))
    raise SystemExit("API answered without the key")
except urllib.error.HTTPError as e:
    assert e.code == 403

httpd.shutdown()
print("smoke test passed")
