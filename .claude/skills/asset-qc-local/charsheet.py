#!/usr/bin/env python3
"""Character model sheets - mint the prompt, crop the panels, verify a lock.

A character lock is not a hero still. It is a MODEL SHEET - one generation holding the
height chart, front, profile, three-quarter, seated and face closeup - cropped into
per-angle panels that ride along as references. Without it the model invents every angle
it was never shown, which is where identity drift and scale drift come from
(the user, 2026-09-21: "Yes. Strictly."). Doctrine + schema: REF_LOCKS.md.

    # 1. the prompt to fire on the skill's own engine (NB2/NB Pro/gpt_image_2, 16:9)
    python charsheet.py --prompt "Priya, 34, South Asian, shoulder-length dark hair" --name CHAR_PRIYA

    # 2. after the user approves the sheet, cut the panels
    python charsheet.py --crop "<project>/Elements/Stills/_anchors/CHAR_PRIYA_sheet.png" \
                        --out  "<project>/Elements/Stills/_anchors/CHAR_PRIYA"

    # 3. what preflight --refs calls: the four required panels exist
    python charsheet.py --verify "<project>/Elements/Stills/_anchors/CHAR_PRIYA"

Exit 1 when a required panel is missing.
"""
import argparse, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# fractions of the sheet width, measured off the 2026-09-21 reference sheet (1280x716)
LAYOUTS = {
    "sheet5": [("height", 0.00, 0.18), ("front", 0.18, 0.36), ("profile", 0.36, 0.50),
               ("three_quarter", 0.50, 0.64), ("seated", 0.64, 0.73), ("closeup", 0.73, 1.00)],
}
REQUIRED = ("front", "profile", "three_quarter", "closeup")

PROMPT = """Character model sheet, single image, 16:9, plain off-white studio sweep, even soft
frontal key with a gentle falloff, no props, no text except the height mark.

SUBJECT: {desc}

LAYOUT, left to right, all figures at a consistent scale on one shared ground line:
  1. a flat grey height silhouette with the height marked at its left edge
  2. FULL BODY FRONT, arms relaxed at the sides, neutral expression, eyes to camera
  3. FULL BODY PROFILE, the same pose turned 90 degrees
  4. FULL BODY THREE-QUARTER, turned about 45 degrees, eyes to camera
  5. SEATED on a plain wooden stool, hands on the thighs
  6. FACE CLOSEUP at the right, head and shoulders, eyes to camera, same light

WARDROBE (identical in every panel, this is the lock): {outfit}

Photographic, real person, consistent face and body across all panels - the same individual
from six angles, not six similar people. No stylisation, no beauty retouching, no depth of
field, no vignette, no watermark, no captions, no logos, no borders between panels.
"""


def crop(sheet, out, layout, boxes):
    from PIL import Image
    im = Image.open(sheet)
    im.load()
    W, H = im.size
    spec = boxes or LAYOUTS[layout]
    os.makedirs(out, exist_ok=True)
    made = []
    for name, x0, x1 in spec:
        p = os.path.join(out, name + ".png")
        im.crop((int(W * x0), 0, int(W * x1), H)).save(p)
        made.append((name, p))
        print("  %-14s %s" % (name, p))
    # a contact strip so the panels get ONE eye-pass read instead of six
    strip_h = 300
    tiles = [Image.open(p).convert("RGB") for _, p in made]
    tiles = [t.resize((max(1, int(t.width * strip_h / t.height)), strip_h)) for t in tiles]
    strip = Image.new("RGB", (sum(t.width for t in tiles), strip_h), "white")
    x = 0
    for t in tiles:
        strip.paste(t, (x, 0)); x += t.width
    sp = os.path.join(out, "_panels_preview.png")
    strip.save(sp)
    print("  %-14s %s   <- read THIS one at the gate" % ("preview", sp))
    return made


def verify(d):
    missing = [n for n in REQUIRED if not os.path.isfile(os.path.join(d, n + ".png"))]
    if missing:
        print("MODEL SHEET INCOMPLETE in %s - missing panel(s): %s" % (d, ", ".join(missing)))
        print("mint the sheet (--prompt), get the user's approval, then --crop it. A character lock "
              "without panels is not a lock.")
        return 1
    print("model sheet OK: %s (%s)" % (d, ", ".join(REQUIRED)))
    return 0


def parse_boxes(vals):
    out = []
    for v in vals:
        name, rng = v.split(":", 1)
        x0, x1 = rng.split(",")
        out.append((name.strip(), float(x0), float(x1)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", metavar="DESC", help="print the house model-sheet prompt for this character")
    ap.add_argument("--name", default="CHAR", help="entity id, used in the printed filenames")
    ap.add_argument("--outfit", default="state the ONE look the character wears for the whole ad, "
                                        "garment by garment, colour by colour")
    ap.add_argument("--crop", metavar="SHEET", help="approved sheet image to cut into panels")
    ap.add_argument("--out", metavar="DIR", help="panel folder (default: <sheet>_panels)")
    ap.add_argument("--layout", default="sheet5", choices=sorted(LAYOUTS))
    ap.add_argument("--boxes", action="append", default=[], metavar="NAME:X0,X1",
                    help="override the layout, fractions of width (repeatable)")
    ap.add_argument("--verify", metavar="DIR", help="exit 1 unless the required panels exist")
    a = ap.parse_args()

    if a.prompt:
        print(PROMPT.format(desc=a.prompt, outfit=a.outfit))
        print("-" * 78)
        print("fire ONE (16:9 is deliberate here - a sheet is a reference, not a deliverable).")
        print("the user approves it -> import to a stable media id -> crop:")
        print('  python charsheet.py --crop "<...>/_anchors/%s_sheet.png" --out "<...>/_anchors/%s"'
              % (a.name, a.name))
        return 0
    if a.crop:
        out = a.out or os.path.splitext(a.crop)[0] + "_panels"
        crop(a.crop, out, a.layout, parse_boxes(a.boxes))
        return 0
    if a.verify:
        return verify(a.verify)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
