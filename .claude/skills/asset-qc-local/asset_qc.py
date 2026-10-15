#!/usr/bin/env python3
"""Deterministic spec-QC for generated 9:16 assets.

Checks every clip (duration, 9:16 aspect, resolution, audio-track) and every
still (aspect, resolution, integrity) in a folder, and reports off-spec files
so they can be fixed before the editor sees them. This is the SPEC-QC step in
each generation check-in — it catches spec violations before anyone watches a
clip. It does NOT judge visual quality; that's
the crv motion pass + the human check-in.

Usage:
    python asset_qc.py <dir> [--aspect 9:16] [--min-still 1024]
                             [--min-duration 1.0] [--max-duration 0] [--json]

Requires: ffprobe on PATH (or under the winget Packages dir), Pillow (PIL).
Exit code 1 if anything is flagged, else 0.
"""
import sys, os, json, subprocess, glob, argparse, shutil

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def find_ffprobe():
    p = shutil.which("ffprobe")
    if p:
        return p
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            if "ffprobe.exe" in files:
                return os.path.join(root, "ffprobe.exe")
    return None


def parse_aspect(s):
    w, h = s.split(":")
    return int(w) / int(h)


def probe_video(ffprobe, path):
    out = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True, text=True)
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = float(data.get("format", {}).get("duration", 0) or 0)
    return dur, int(v.get("width", 0)), int(v.get("height", 0)), has_audio


def check_dir(d, aspect, min_still, min_dur, max_dur, audio="forbid"):
    ffprobe = find_ffprobe()
    try:
        from PIL import Image
    except ImportError:
        Image = None
    rows = []
    for path in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
        if os.path.isdir(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        name = os.path.relpath(path, d)
        flags = []
        if ext in VIDEO_EXT:
            if not ffprobe:
                rows.append((name, "CLIP", "SKIP-no-ffprobe")); continue
            dur, w, h, has_audio = probe_video(ffprobe, path)
            if dur and dur < min_dur:
                flags.append(f"TRUNCATED?({dur:.2f}s)")
            if max_dur and dur > max_dur:
                flags.append(f"OVER-DURATION({dur:.2f}s)")
            if w and h and abs((w / h) - aspect) > 0.02:
                flags.append(f"WRONG-ASPECT({w}x{h})")
            if audio == "forbid":
                if has_audio:
                    flags.append("HAS-AUDIO(strip-muted)")
            elif audio == "require":
                if not has_audio:
                    flags.append("AUDIO-MISSING")
            note = f"{dur:.2f}s {w}x{h}"
            rows.append((name, "CLIP", (", ".join(flags) + f"  [{note}]") if flags else f"OK  [{note}]"))
        elif ext in IMAGE_EXT:
            if Image is None:
                rows.append((name, "STILL", "SKIP-no-PIL")); continue
            try:
                im = Image.open(path); im.load()
                w, h = im.size
                if abs((w / h) - aspect) > 0.02:
                    flags.append(f"WRONG-ASPECT({w}x{h})")
                if min(w, h) < min_still:
                    flags.append(f"LOW-RES({w}x{h})")
                note = f"{w}x{h}"
                rows.append((name, "STILL", (", ".join(flags) + f"  [{note}]") if flags else f"OK  [{note}]"))
            except Exception as e:
                rows.append((name, "STILL", f"CORRUPT({type(e).__name__})"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--min-still", type=int, default=1024, help="flag stills whose short side is below this")
    ap.add_argument("--min-duration", type=float, default=1.0, help="flag clips shorter than this (likely truncated)")
    ap.add_argument("--max-duration", type=float, default=0.0, help="flag clips longer than this (0 = off)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--audio", choices=["forbid", "require"], default="forbid",
                     help="forbid: any audio track fails (b-roll, default). require: missing audio fails (talking assets)")
    a = ap.parse_args()
    if not os.path.isdir(a.dir):
        print(f"not a directory: {a.dir}"); sys.exit(2)
    rows = check_dir(a.dir, parse_aspect(a.aspect), a.min_still, a.min_duration, a.max_duration, a.audio)
    flagged = [r for r in rows if not (r[2].startswith("OK") or r[2].startswith("SKIP"))]
    if a.json:
        print(json.dumps([{"file": r[0], "kind": r[1], "result": r[2]} for r in rows], indent=2))
    else:
        for name, kind, res in rows:
            mark = "ok " if res.startswith("OK") else ("-- " if res.startswith("SKIP") else "!! ")
            print(f"{mark}[{kind:5}] {name}: {res}")
        print(f"\n{len(rows)} assets - {len(flagged)} flagged")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
