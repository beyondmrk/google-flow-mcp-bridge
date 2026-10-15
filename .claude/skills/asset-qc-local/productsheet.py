#!/usr/bin/env python3
"""Product model sheets - assemble from SUPPLIED material, crop the panels, verify a lock.

The symmetric half of charsheet.py (the user, 2026-09-21: "product sheet? Where is it?").
A character locks to a model sheet because one angle means the model invents the rest. A
PRODUCT has exactly the same failure and had no equivalent - one client's whole device lock
was one three-quarter render, and the trap table already records a "wrong product" round
caused by geometry guessed at angles the render never showed.

THE ONE DIFFERENCE FROM A CHARACTER SHEET: a product sheet is never GENERATED. Characters
are minted; products are photographed. Every panel here is cropped from supplied stills or
pulled as a frame from the client's own real footage, and `--build` REFUSES a source that
lives under a generated stills/clips path. That keeps REF_LOCKS.md's product rule intact:
nothing about the packaging is ever drawn from memory.

    # 1. assemble from a spec of supplied sources (images and/or video frames)
    python productsheet.py --build spec.json --out "<registry>/PROD_X_sheet.png"

    # 2. cut the panels
    python productsheet.py --crop "<registry>/PROD_X_sheet.png" --out "<registry>/PROD_X_panels"

    # 3. what preflight --refs calls
    python productsheet.py --verify "<registry>/PROD_X_panels"

spec.json: {"panels": [{"label": "face", "src": "...jpg", "box": [x0,y0,x1,y1]},
                       {"label": "plugged", "src": "...MOV", "t": 2.5}, ...]}
  box  - optional crop as fractions of the source, for lifting a device off a marketing card
  t    - optional timestamp; omitted means the SHARPEST sampled frame is chosen
         (Laplacian variance - a machine check, not a look)

Exit 1 when a required panel is missing, or when a source is generated.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Generic across clients: a plug-in device, a tablet tube and a box all have a face, a
# three-quarter and a scale cue. Anything device-specific (plugged, box, top_down, profile,
# back, detail) is a useful EXTRA, never the contract.
REQUIRED = ("face", "three_quarter", "scale")
VIDEO = (".mov", ".mp4", ".m4v", ".webm", ".mkv")

# A product panel may never come from something a model drew. These are the paths the
# generation skills write their own output to.
GENERATED_MARKERS = (
    os.sep + "elements" + os.sep + "stills" + os.sep,
    os.sep + "elements" + os.sep + "clips" + os.sep,
    os.sep + "02_stills" + os.sep,
    os.sep + "03_clips" + os.sep,
    os.sep + "01_anchors" + os.sep,
    os.sep + "_anchors" + os.sep,
)


def refuse_if_generated(src):
    """REF_LOCKS.md: a product is never generated. A sheet panel is not a loophole."""
    p = os.sep + os.path.normcase(os.path.abspath(src)).strip(os.sep) + os.sep
    for m in GENERATED_MARKERS:
        if m in p:
            raise ValueError(
                "%s is a GENERATED asset (%s) - a product sheet panel must be supplied "
                "photography or a frame of the client's real footage, never a render. "
                "See REF_LOCKS.md." % (os.path.basename(src), m.strip(os.sep)))


def sharpest_frame(path, samples=9):
    """Pick the least-blurred sampled frame by Laplacian variance. Deterministic."""
    import numpy as np
    from PIL import Image
    dur = probe_duration(path)
    lo, hi = dur * 0.15, dur * 0.85
    best, best_t = None, lo
    for i in range(samples):
        t = lo + (hi - lo) * (i / max(1, samples - 1))
        im = frame_at(path, t)
        if im is None:
            continue
        g = np.asarray(im.convert("L"), dtype="float32")
        lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1])
        score = float(lap.var())
        if best is None or score > best:
            best, best_t = score, t
    return best_t, best


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def frame_at(path, t):
    from PIL import Image
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % t, "-i", path,
                    "-frames:v", "1", tmp], capture_output=True)
    try:
        im = Image.open(tmp).convert("RGB")
        im.load()
        return im
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def load_panel(p):
    from PIL import Image
    src = p["src"]
    if not os.path.isfile(src):
        raise ValueError("%s: source not found: %s" % (p.get("label", "?"), src))
    refuse_if_generated(src)
    if src.lower().endswith(VIDEO):
        t = p.get("t")
        note = ""
        if t is None:
            t, score = sharpest_frame(src)
            note = " (sharpest of 9, t=%.2fs, var=%.0f)" % (t, score or 0)
        im = frame_at(src, float(t))
        if im is None:
            raise ValueError("%s: could not read a frame at %.2fs from %s"
                             % (p.get("label"), float(t), src))
        origin = "frame @%.2fs%s" % (float(t), note)
    else:
        im = Image.open(src).convert("RGB")
        origin = "still"
    if p.get("box"):
        x0, y0, x1, y1 = p["box"]
        w, h = im.size
        im = im.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
        origin += " cropped"
    return im, origin


def build(spec_path, out):
    from PIL import Image, ImageDraw
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    panels = spec["panels"]
    TH = spec.get("panel_height", 760)
    ims, meta = [], []
    for p in panels:
        im, origin = load_panel(p)
        w = max(1, int(im.width * (TH / im.height)))
        ims.append(im.resize((w, TH), Image.LANCZOS))
        meta.append((p["label"], os.path.basename(p["src"]), origin))
    pad, lab = 10, 26
    W = sum(i.width for i in ims) + pad * (len(ims) + 1)
    H = TH + lab + pad * 2
    sheet = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    x = pad
    boxes = {}
    for (label, _, _), im in zip(meta, ims):
        sheet.paste(im, (x, pad + lab))
        d.text((x + 4, pad + 6), label, fill=(20, 20, 20))
        boxes[label] = (x / W, (x + im.width) / W)
        x += im.width + pad
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sheet.save(out)
    side = os.path.splitext(out)[0] + "_sources.json"
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"sheet": os.path.basename(out), "boxes": boxes,
                   "panels": [{"label": l, "source": s, "origin": o} for l, s, o in meta]},
                  f, indent=1, ensure_ascii=False)
    print("wrote %s  %dx%d  %d panel(s)" % (out, W, H, len(ims)))
    for l, s, o in meta:
        print("  %-14s %-46s %s" % (l, s[:46], o))
    print("provenance: %s" % side)
    return out


def crop(sheet, outdir):
    from PIL import Image
    side = os.path.splitext(sheet)[0] + "_sources.json"
    if not os.path.isfile(side):
        raise ValueError("no %s beside the sheet - rebuild it with --build so the panel "
                         "boxes are recorded" % os.path.basename(side))
    with open(side, encoding="utf-8") as f:
        boxes = json.load(f)["boxes"]
    im = Image.open(sheet).convert("RGB")
    outdir = outdir or os.path.splitext(sheet)[0] + "_panels"
    os.makedirs(outdir, exist_ok=True)
    W, H = im.size
    for label, (a, b) in boxes.items():
        im.crop((int(a * W), 0, int(b * W), H)).save(os.path.join(outdir, label + ".png"))
        print("  %-14s %s" % (label, os.path.join(outdir, label + ".png")))
    prev = os.path.join(outdir, "_panels_preview.png")
    im.resize((min(W, 1800), int(H * (min(W, 1800) / W))), Image.LANCZOS).save(prev)
    print("  %-14s %s   <- read THIS one at the gate" % ("preview", prev))
    return outdir


def verify(outdir):
    missing = [p for p in REQUIRED if not os.path.isfile(os.path.join(outdir, p + ".png"))]
    if missing:
        print("product sheet INCOMPLETE: %s missing from %s" % (", ".join(missing), outdir))
        return 1
    print("product sheet OK: %s (%s)" % (outdir, ", ".join(REQUIRED)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", metavar="SPEC")
    ap.add_argument("--crop", metavar="SHEET")
    ap.add_argument("--verify", metavar="DIR")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args()
    if a.build:
        if not a.out:
            ap.error("--build needs --out")
        build(a.build, a.out)
    elif a.crop:
        crop(a.crop, a.out)
    elif a.verify:
        sys.exit(verify(a.verify))
    else:
        ap.error("one of --build / --crop / --verify")


if __name__ == "__main__":
    main()
