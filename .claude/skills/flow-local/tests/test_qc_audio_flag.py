import unittest, subprocess, tempfile, pathlib, sys
QC = str(pathlib.Path(__file__).resolve().parents[2] / "asset-qc-local" / "asset_qc.py")

def mk(path, with_audio):
    v = ["ffmpeg","-y","-f","lavfi","-i","testsrc=size=720x1280:rate=24:duration=8"]
    a = ["-f","lavfi","-i","sine=frequency=440:duration=8"] if with_audio else []
    codec = ["-c:v","libx264","-pix_fmt","yuv420p"] + (["-c:a","aac"] if with_audio else ["-an"])
    subprocess.run(v + a + codec + [str(path)], check=True, capture_output=True)

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pathlib.Path(tempfile.mkdtemp())
        cls.silent = cls.d / "silent.mp4"; mk(cls.silent, False)
        cls.voiced = cls.d / "voiced.mp4"; mk(cls.voiced, True)
    def run_qc(self, *args):
        return subprocess.run([sys.executable, QC, *args], capture_output=True, text=True)
    def test_default_forbid_fails_voiced(self):          # existing behavior preserved
        r = self.run_qc(str(self.d))
        self.assertIn("voiced.mp4", r.stdout + r.stderr)
        self.assertIn("HAS-AUDIO", r.stdout + r.stderr)
    def test_require_fails_silent_passes_voiced(self):
        r = self.run_qc(str(self.d), "--audio", "require")
        out = r.stdout + r.stderr
        self.assertIn("silent.mp4", out)
        self.assertIn("AUDIO-MISSING", out)
        # voiced.mp4 must NOT be flagged under --audio require: only silent.mp4 should fail
        voiced_line = next(line for line in out.splitlines() if "voiced.mp4" in line)
        self.assertNotIn("AUDIO-MISSING", voiced_line)
        self.assertIn("1 flagged", out)   # 2 assets total, exactly 1 (silent.mp4) flagged

if __name__ == "__main__":
    unittest.main()
