"""Clauditing desktop app: opens the UI in a native window."""

import os
import sys

if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(sys._MEIPASS))  # noqa
else:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webview  # noqa: E402

from app.server import App, start_server  # noqa: E402


def main():
    app = App()
    httpd, key, url = start_server(app)
    window = webview.create_window(
        "Clauditing", url, width=1240, height=820, min_size=(900, 620), background_color="#FAF9F5", text_select=True
    )
    app.window = window
    try:
        webview.start(
            private_mode=False,
            storage_path=os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Clauditing", "webview"),
        )
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
