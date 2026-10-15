#!/usr/bin/env python3
"""flow_recv.py — localhost receiver that lets the Flow page push generated media straight to disk.

Why this exists (2026-09-18): after Flow moved to flow.google.com the old
MEDIA-URL -> navigate -> signed-CDN-URL -> PowerShell download route died, and the
replacement (an in-page <a download> click) is unusable at scale because Chrome
silently drops the 2nd+ automatic download from a site until a human grants the
per-site "automatic downloads" permission, and the server overrides the filename anyway.

The page can already reach http://127.0.0.1:<port> (that is how IFRAME-UPLOAD works, and
127.0.0.1 is "potentially trustworthy" so an https page may POST to it without mixed-content
blocking). So: the page fetches the media same-origin, then POSTs the bytes here, and this
writes them to disk under --root with the exact name we want. No download gate, no rename
dance, and no base64 through the model's context.

Usage:
    python flow_recv.py --root "<root>/<project>" [--port 8478]

Prints PORT=<n> as its first stdout line (walks up on collision), same contract as refserver.py.
"""
import argparse
import os
import pathlib
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = None
SAFE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        # Chrome Private Network Access: an https page POSTing to 127.0.0.1 is a
        # public -> private request and is REFUSED (the promise just hangs, with no
        # error) unless the preflight is answered with this header.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Same-origin helper page. The Flow page plants this in an iframe and postMessages
        # {name, buf} to it; the iframe POSTs same-origin, which sidesteps PNA entirely.
        # This mirrors upload_helper.html in refserver.py, in the opposite direction.
        if self.path.split("?", 1)[0] != "/recv_helper.html":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        page = (
            "<!doctype html><script>\n"
            "window.addEventListener('message', function (ev) {\n"
            "  var d = ev.data; if (!d || !d.name || !d.buf) return;\n"
            "  fetch('/save/' + d.name, { method: 'POST', body: d.buf })\n"
            "    .then(function (r) { return r.text(); })\n"
            "    .then(function (t) { ev.source.postMessage({ saved: d.name, bytes: t }, '*'); })\n"
            "    .catch(function (e) { ev.source.postMessage({ saved: d.name, err: String(e) }, '*'); });\n"
            "});\n</script>"
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/save/"):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        name = path[len("/save/"):]
        if not SAFE.match(name) or ".." in name:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"bad name")
            return
        dest = (ROOT / name).resolve()
        if ROOT not in dest.parents and dest.parent != ROOT:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"outside root")
            return
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else b""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        print("WROTE %s %d" % (name, len(data)), flush=True)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(str(len(data)).encode())

    def log_message(self, *a):
        pass


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--port", type=int, default=8478)
    a = ap.parse_args()
    ROOT = pathlib.Path(a.root).resolve()
    ROOT.mkdir(parents=True, exist_ok=True)
    port = a.port
    for _ in range(40):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        sys.exit("no free port")
    print("PORT=%d" % port, flush=True)
    print("ROOT=%s" % ROOT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
