#!/usr/bin/env python3
"""flow-local refserver: serves local ref images to the Flow page's hidden iframe.
127.0.0.1 only. See spec §4. First stdout line is PORT=<n>."""
import argparse, os, pathlib, socket, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

CORS = {
    "Access-Control-Allow-Origin": "https://labs.google",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Private-Network": "true",
}

def confine_path(root, translated_path):
    """Check if translated_path is under root; return safe path or forbidden marker."""
    root_resolved = pathlib.Path(root).resolve()
    p = pathlib.Path(translated_path).resolve()
    return str(p) if str(p).startswith(str(root_resolved)) else str(root_resolved / "_forbidden_")

def make_handler(root: pathlib.Path):
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)
        def end_headers(self):
            for k, v in CORS.items():
                self.send_header(k, v)
            super().end_headers()
        def do_OPTIONS(self):
            self.send_response(204)
            self.end_headers()
        def translate_path(self, path):
            # resolve and confine to root; outside -> a path that 404s
            p = super().translate_path(path)
            return confine_path(root, p)
        def send_head(self):
            # A request resolving to a directory must 404, never a directory listing —
            # a listing served as if it were the requested file is silent wrong bytes
            # downstream (C1a).
            path = self.translate_path(self.path)
            if os.path.isdir(path):
                self.send_error(404, "Not Found")
                return None
            return super().send_head()
        def log_message(self, *a):  # quiet
            pass
    return H

class CustomHTTPServer(HTTPServer):
    allow_reuse_address = False
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._serve_forever_thread = None
    def server_bind(self):
        # SO_EXCLUSIVEADDRUSE (Windows) prevents multiple binds to same port
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
    def serve_forever(self, poll_interval=0.5):
        """Track that serve_forever is running; then call parent."""
        self._serve_forever_thread = threading.current_thread()
        super().serve_forever(poll_interval)
    def shutdown(self):
        """Signal shutdown, wait for loop to exit (if running), then close socket."""
        self._BaseServer__shutdown_request = True
        # Only wait if serve_forever() was actually called in a thread
        if self._serve_forever_thread is not None and self._serve_forever_thread.is_alive():
            self._BaseServer__is_shut_down.wait(timeout=1)
        self.server_close()

def serve(root, port, max_walk=20):
    root = pathlib.Path(root)
    for p in range(port, port + max_walk + 1):
        try:
            return CustomHTTPServer(("127.0.0.1", p), make_handler(root)), p
        except OSError:
            continue
    raise OSError(f"no free port in {port}..{port+max_walk}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--port", type=int, default=8477)
    a = ap.parse_args()
    srv, actual = serve(a.root, a.port)
    print(f"PORT={actual}", flush=True)
    srv.serve_forever()
