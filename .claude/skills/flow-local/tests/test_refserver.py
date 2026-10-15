import unittest, threading, urllib.request, urllib.error, tempfile, pathlib, sys, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import refserver

class TestPathConfinement(unittest.TestCase):
    """Test the confine_path() function directly."""
    def test_confine_path_allows_files_under_root(self):
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "allowed.txt").write_text("content")
        subdir = root / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("content")

        # Files under root should be allowed
        result = refserver.confine_path(root, str(root / "allowed.txt"))
        self.assertEqual(result, str((root / "allowed.txt").resolve()))

        result = refserver.confine_path(root, str(subdir / "nested.txt"))
        self.assertEqual(result, str((subdir / "nested.txt").resolve()))

    def test_confine_path_rejects_traversal(self):
        root = pathlib.Path(tempfile.mkdtemp())
        parent = root.parent

        # Path outside root should be rejected with _forbidden_ marker
        outside_path = str(parent / "outside.txt")
        result = refserver.confine_path(root, outside_path)
        # The result should contain _forbidden_ in the path to indicate rejection
        self.assertIn("_forbidden_", result)

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(tempfile.mkdtemp())
        (cls.root / "ref.png").write_bytes(b"\x89PNG_fake_bytes")
        cls.srv, cls.port = refserver.serve(cls.root, 8477)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_get_serves_bytes(self):
        r = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/ref.png")
        self.assertEqual(r.read(), b"\x89PNG_fake_bytes")
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://labs.google")

    def test_directory_request_returns_404(self):
        # C1a: a request that resolves to a directory must 404 loudly, never serve a
        # directory listing as if it were the requested file (silent wrong bytes).
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/")
        self.assertEqual(cm.exception.code, 404)

    def test_options_preflight_has_private_network_header(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/ref.png", method="OPTIONS")
        r = urllib.request.urlopen(req)
        self.assertIn(r.status, (200, 204))
        self.assertEqual(r.headers["Access-Control-Allow-Private-Network"], "true")

    def test_traversal_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/..%2f..%2fsecret.txt")
        self.assertIn(cm.exception.code, (403, 404))

    def test_port_collision_walks_up(self):
        srv2, port2 = refserver.serve(self.root, self.port)  # same port busy
        try:
            self.assertGreater(port2, self.port)
        finally:
            srv2.shutdown()

if __name__ == "__main__":
    unittest.main()
