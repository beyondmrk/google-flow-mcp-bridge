#!/usr/bin/env python3
"""Shared QA engine for the AI-gen skills (flow-local and any b-roll folder). One set of checks, one report shape, per-skill differences live in a profile.

Layers (pick with --layers, default = every layer the inputs support):

  spec        ffprobe/PIL: aspect, resolution, fps, duration vs expected, truncation, audio
              mode (require / forbid / by-shot), still integrity.
  framescan   EVERY frame decoded (not keyframe-sampled): freeze runs, whole-clip static,
              diff spikes (jump cut / flicker), black frames, first-frame-only glitch.
              Flagged frames are rendered into _qa/<clip>_flagged.png so the eye pass looks at
              the 6 suspicious frames instead of 12 sampled ones.
  startframe  SSIM of the clip's first frame vs its seed still (silent re-render before motion
              starts) and, when a shot declares an end still, last frame vs end still (wander).
  audio       silencedetect lead / tail / all-silent, integrated loudness (EBU R128), noise
              floor in the quiet gaps and speech-to-floor SNR, sibling-take loudness spread.
  speech      Whisper word timestamps: words/sec vs the expected line (stretched / rushed),
              late start, dead tail, and the word-for-word dialogue diff (dropped opener /
              tail, stutter, claim-word drop) with the profile's notation and digit maps.
  dupes       perceptual hash over stills (and clip first frames) — near-duplicate pairs.

Inputs — one of:
  --dir <folder>                       folder mode (stills + clips, stem-paired)
  --manifest <flow json>               flow-local manifest
plus --profile {broll,flow} (JSON files under profiles/, override any key with
--set key=value).

Verdict: every finding carries a severity from the profile's policy (FAIL / WARN / INFO).
Exit 1 when anything is FAIL. --strict promotes the profile's `strict_promote` codes to FAIL.
Writes <out>/_qa/qa_report.json + flagged-frame strips + a summary table on stdout.

Automated-clean is not deliverable. Pacing, hook strength, b-roll relevance, voice timbre and
"is the performance good" remain the human watch-through.
"""
import argparse, glob, json, math, os, re, shutil, subprocess, sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


# ----------------------------------------------------------------------------- tool lookup
def _find(exe):
    p = shutil.which(exe)
    if p:
        return p
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for root, _d, files in os.walk(base):
            if exe + ".exe" in files:
                return os.path.join(root, exe + ".exe")
    return exe


FFPROBE = _find("ffprobe")
FFMPEG = _find("ffmpeg")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


# ----------------------------------------------------------------------------- profile
DEFAULT_PROFILE = {
    "name": "default",
    "aspect": "9:16",
    "aspect_tol": 0.02,
    "still_min_short_side": 1024,
    "clip_res": None,                 # [720, 1280] to enforce, null = report only
    "clip_res_tol": 0,                # px slack per side; engines drift a little (seedance
                                      # returned 722x1276 for a 720x1280 request, 2026-09-21).
                                      # Keep it small: this must still catch a WRONG size.
    "clip_fps": None,                 # 24 to enforce, null = report only
    "duration_default": None,         # expected seconds when the item has none
    "duration_tol": 0.6,
    "min_duration": 1.0,
    "audio": "forbid",                # forbid | require | by_shot (speaks -> require, else forbid) | any
    "framescan": {
        "scale_w": 160,
        "freeze_diff": 0.6,           # mean |Δ| (0-255) under which two frames are "the same"
        "freeze_min_s": 1.0,          # a same-frame run this long is FREEZE
        "freeze_fail_share": None,    # a freeze covering this share of the clip is FREEZE-MAJOR (FAIL).
                                      # None = off, so every existing profile behaves exactly as before.
        "static_diff": 1.2,           # whole-clip mean under this = STATIC
        "static_px": 0.01,            # AND share of changing pixels under this
        "static_span": 6.0,           # AND first->last mean |Δ| under this (slow pushes score 20-35 here)
        "static_span_ssim": 0.85,     # AND first/last SSIM above this
        "spike_k": 6.0,               # spike = diff > median + k * MAD
        "spike_floor": 18.0,          # and > this absolute mean |Δ|
        "black_luma": 16.0,
        "black_min_frames": 3,
        "first_frame_glitch_k": 3.0   # frame0->1 diff this many x the clip median = FIRST-FRAME-GLITCH
    },
    # measured 2026-09-03: clean i2v clips score 0.95-0.99 vs their seed (measured across several client builds)
    # by_model overrides ssim_fail/ssim_warn for one engine. Empty = the global pair for every engine.
    # Needed because the 0.95+ figure above holds only for engines that COPY the seed frame.
    "startframe": {"ssim_fail": 0.5, "ssim_warn": 0.8, "end_ssim_fail": 0.25, "by_model": {}},
    "audio_checks": {
        "silence_db": -40, "silence_min_s": 0.25,
        "lead_silence_warn_s": 1.2, "tail_silence_warn_s": 1.5,
        # UGC room tone sits at -42..-50 dBFS; the client's "loud background noise" reject is above -40
        "noise_floor_warn_dbfs": -40.0, "snr_warn_db": 15.0,
        "loudness_spread_warn_lu": 4.0
    },
    "speech": {
        "whisper_model": "small",
        "language": "en",
        "min_wps": 1.8, "max_wps": 4.3,
        "late_start_s": 1.2, "dead_tail_s": 1.5,
        "fuzzy_word": 85,            # rapidfuzz ratio: "flavoured" hears "flavored"; "without" never hears "ago"
        "line_field": "line",         # which expectation field holds what should be heard
        "notation": [],               # [["acme labs","acmelabs"], ...] applied before diff
        "digits": {},                 # {"$369": "three hundred sixty nine dollars"}
        "claim_words": []
    },
    # clip first frames share a seed across shots by design (68 false pairs on an earlier build) -> stills only by default
    "dupes": {"hash_bits": 16, "max_hamming": 4, "include_clip_frames": False},
    # measured 2026-09-03 (OpenCV SFace cosine): same person 0.77-0.93 across stills + clip frames,
    # different people 0.03-0.17. Below fail = a different person / re-rendered identity.
    "identity": {"ref": None, "cos_fail": 0.4, "cos_warn": 0.6, "clip_samples": 5, "require_face": False},
    # burned-in text: RapidOCR on stills + sampled clip frames. Strings matching `allow` (regex, case-
    # insensitive) are fine; anything else is TEXT-FOUND at `mode` severity (warn|fail).
    "text": {"mode": "warn", "min_conf": 0.6, "clip_samples": 4, "allow": []},
    # voice (2026-09-26): every talking clip's pitch against the VO master and against the other talking
    # clips of the build. voice_change kept Kling's pitch on an earlier build (+2.2 st) and nothing measured it.
    "voice": {"max_semitones": 2.5},
    # lipsync / objects (2026-09-26): MediaPipe jawOpen vs the audio envelope; YOLOv8n phone / bottle / person
    "lipsync": {"min_r": 0.2, "sample_fps": 8},   # Kling originals 0.36-0.42; voice_change results 0.22-0.25 (new envelope, same lips)
    "objects": {"conf": 0.35, "clip_samples": 3},    # same-voice clips spread up to ~2 st; that defect read 2.6-3.2
    "policy": {
        "FAIL": ["UNREADABLE", "CORRUPT", "WRONG-ASPECT", "TRUNCATED", "AUDIO-MISSING", "HAS-AUDIO",
                 "STATIC", "FREEZE-MAJOR", "BLACK", "CLAIM-DROP", "NUMERIC-MISMATCH", "END-FRAME-DRIFT",
                 "START-FRAME-MISMATCH", "ALL-SILENT", "NO-TRANSCRIPT", "IDENTITY-MISMATCH", "TEXT-FORBIDDEN"],
        "WARN": ["LOW-RES", "RES", "FPS", "DUR", "OVER-DURATION", "FREEZE", "SPIKE", "FLICKER",
                 "FIRST-FRAME-GLITCH", "START-FRAME-DRIFT", "LEAD-SILENCE", "TAIL-SILENCE", "NOISY",
                 "LOW-SNR", "LOUDNESS-SPREAD", "STRETCHED", "RUSHED", "LATE-START", "DEAD-TAIL",
                 "DROPPED-OPENER", "DROPPED-TAIL", "STUTTER", "TRUNCATED-SPEECH", "DRIFT", "DUPLICATE",
                 "MISSING-CLIP", "ORPHAN", "STRAY-AUDIO", "LOW-MOTION", "IDENTITY-DRIFT", "NO-FACE", "TEXT-FOUND",
                 "VOICE-PITCH", "VOICE-DRIFT", "CAST-OUTLIER", "LIPSYNC-LOW", "NO-MOUTH",
                 "PHONE-IN-FRAME", "PRODUCT-COUNT", "PERSON-IN-FRAME"]
    },
    "strict_promote": ["FREEZE", "SPIKE", "DROPPED-OPENER", "DROPPED-TAIL", "STUTTER", "TRUNCATED-SPEECH",
                       "NOISY", "STRETCHED", "START-FRAME-DRIFT", "DUPLICATE", "STRAY-AUDIO", "MISSING-CLIP",
                       "TEXT-FOUND", "IDENTITY-DRIFT", "VOICE-PITCH", "VOICE-DRIFT", "CAST-OUTLIER",
                       "LIPSYNC-LOW", "NO-MOUTH", "PHONE-IN-FRAME", "PRODUCT-COUNT", "PERSON-IN-FRAME"]
}


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_profile(name_or_path, sets):
    prof = dict(DEFAULT_PROFILE)
    if name_or_path:
        p = name_or_path if os.path.exists(name_or_path) else os.path.join(HERE, "profiles", name_or_path + ".json")
        if not os.path.exists(p):
            sys.exit("profile not found: %s" % name_or_path)
        with open(p, encoding="utf-8") as f:
            prof = deep_merge(prof, json.load(f))
    for s in sets or []:
        k, _, v = s.partition("=")
        try:
            v = json.loads(v)
        except Exception:
            pass
        cur = prof
        parts = k.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = v
    return prof


# ----------------------------------------------------------------------------- items
def item(path, kind, id_=None, group=None, **expect):
    return {"file": path, "kind": kind, "id": id_ or os.path.splitext(os.path.basename(path))[0],
            "group": group or id_ or os.path.splitext(os.path.basename(path))[0], "expect": expect}


def _stem(name):
    return re.sub(r"_v\d+$", "", os.path.splitext(os.path.basename(name))[0])


DEFAULT_EXCLUDE = ["BOOK_REFS", "_qa", "_qaf", "_rejects", "_rejected", "_delivery", "__pycache__", "crv-out",
                   "Reference", "PRODUCT_REFS", "_refs", "Creatives", "00_BRIEF", "06_DOCS", "DELIVERY"]


def project_root_from_manifest(mpath):
    """Manifest lives in <root>/Creatives/ (canonical, 2026-09-03) or <root>/Elements/Prompts/ (legacy)."""
    d = os.path.dirname(os.path.abspath(mpath))
    if os.path.basename(d).lower() == "creatives":
        return os.path.dirname(d)
    if os.path.basename(d).lower() == "prompts" and os.path.basename(os.path.dirname(d)).lower() == "elements":
        return os.path.dirname(os.path.dirname(d))
    return d


def stills_dir(root):
    """Elements/Stills (canonical) with legacy fallbacks."""
    for cand in ("Elements/Stills", "Elements/Graphics", "02_REFERENCE_STILLS"):
        p = os.path.join(root, *cand.split("/"))
        if os.path.isdir(p):
            return p
    return os.path.join(root, "Elements", "Stills")


def find_stills(root, name):
    """Resolve a still by file name across the canonical + legacy still folders."""
    for cand in (stills_dir(root), os.path.join(root, "Elements", "Graphics"), os.path.join(root, "Elements", "Footage", "Reference")):
        p = os.path.join(cand, name)
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(root, "Elements", "**", name), recursive=True)
    return hits[0] if hits else None


def find_clips(root, pattern):
    """Clips live in Elements/Clips (canonical); legacy Elements/Footage/<model>/, 03_MOTION_CLIPS."""
    hits = []
    for cand in ("Elements/Clips", "Elements/Footage", "03_MOTION_CLIPS"):
        base = os.path.join(root, *cand.split("/"))
        if os.path.isdir(base):
            hits += glob.glob(os.path.join(base, "**", pattern), recursive=True)
    hits = [h for h in hits if not any(part in ("_rejects", "_rejected", "_qa") for part in h.replace("\\", "/").split("/"))]
    return sorted(set(hits))


def items_from_dir(d, exclude=None):
    ex = set(exclude if exclude is not None else DEFAULT_EXCLUDE)
    stills, clips = {}, []
    for p in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
        rel = os.path.relpath(p, d).replace("\\", "/").split("/")
        if any(part in ex for part in rel[:-1]):
            continue
        ext = os.path.splitext(p)[1].lower()
        if ext in IMAGE_EXT:
            stills.setdefault(_stem(p), p)
        elif ext in VIDEO_EXT:
            clips.append(p)
    items = [item(p, "still", os.path.relpath(p, d)) for p in stills.values()]
    for p in clips:
        st = stills.get(_stem(p))
        items.append(item(p, "clip", os.path.relpath(p, d), group=_stem(p), start_still=st))
    return items, d


def items_from_flow(mpath):
    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)
    root = project_root_from_manifest(mpath)
    P = m.get("project", {})
    char_ref = P.get("char_ref")
    if char_ref and not os.path.isabs(char_ref):
        char_ref = os.path.join(root, char_ref)
    if char_ref and not os.path.exists(char_ref):
        char_ref = None
    # multi-character projects: a shot's `entities` picks that character's `anchor`; `people: false`
    # (two-shots, b-roll) skips the face check; no key -> project.char_ref (2026-10-06, a two-hander build)
    ents_by_id = {e.get("id"): e for e in (P.get("entities") or []) if isinstance(e, dict)}

    def _ident(s):
        if s.get("people") is False:
            return None
        if s.get("entities") is None:
            return char_ref
        for eid in s["entities"]:
            e = ents_by_id.get(eid) or {}
            a = e.get("anchor")
            if e.get("kind") == "character" and a:
                a = a if os.path.isabs(a) else os.path.join(root, a)
                if os.path.exists(a):
                    return a
        return None

    items = []
    for s in m["shots"]:
        still = find_stills(root, s["outName"]) if s.get("outName") else None
        if still:
            items.append(item(still, "still", s["outName"], group=s["tag"], identity_ref=_ident(s),
                              allow_phone=s.get("allow_phone"), qa_accept=s.get("qa_accept"),
                              text_allow=s.get("text_allow")))
        clips = find_clips(root, s["clipName"] + "_v*.mp4") if s.get("clipName") else []
        if not clips:
            items.append({"file": None, "kind": "clip", "id": s.get("clipName", s["tag"]), "group": s["tag"],
                          "expect": {}, "missing": True})
        for c in clips:
            items.append(item(c, "clip", os.path.splitext(os.path.basename(c))[0], group=s["tag"],
                              line=s.get("spoken") or s.get("dialogue"), canon=s.get("dialogue"),
                              # silent b-roll (no dialogue) is not a talking clip, and the shot's own
                              # duration is the expectation (2026-09-30: 4 s b-roll FAILed
                              # "want 8" plus every speech check)
                              speaks=bool(s.get("spoken") or s.get("dialogue")), duration=s.get("duration"),
                              start_still=still, identity_ref=_ident(s),
                              text_allow=s.get("text_allow"), allow_phone=s.get("allow_phone"),
                              # qa_accept reached the other readers but not flow (2026-10-09)
                              qa_accept=s.get("qa_accept")))
    return items, root


# ----------------------------------------------------------------------------- helpers
def probe(path):
    r = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    try:
        j = json.loads(r.stdout or "{}")
    except Exception:
        return None
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return None
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    fr = v.get("avg_frame_rate", "0/1")
    try:
        n, d = fr.split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    except Exception:
        fps = 0.0
    dur = float(j.get("format", {}).get("duration") or v.get("duration") or 0)
    return {"w": int(v.get("width", 0)), "h": int(v.get("height", 0)), "fps": fps, "fps_raw": fr,
            "dur": dur, "audio": a is not None, "nb_frames": int(v.get("nb_frames") or 0)}


def decode_gray(path, w, h, scale_w):
    sw = scale_w
    sh = max(2, int(round(h * sw / w)) // 2 * 2)
    r = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vf", "scale=%d:%d,format=gray" % (sw, sh),
                        "-f", "rawvideo", "-"], capture_output=True)
    buf = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(buf) // (sw * sh)
    return buf[: n * sw * sh].reshape(n, sh, sw).astype(np.float32)


def load_gray(path, size):
    return np.asarray(Image.open(path).convert("L").resize(size, Image.BILINEAR), dtype=np.float32)


def ssim(a, b):
    """Global-ish SSIM on float gray arrays (same shape), 7x7 uniform windows."""
    import cv2
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k = (7, 7)
    mu_a, mu_b = cv2.blur(a, k), cv2.blur(b, k)
    saa = cv2.blur(a * a, k) - mu_a ** 2
    sbb = cv2.blur(b * b, k) - mu_b ** 2
    sab = cv2.blur(a * b, k) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + C1) * (2 * sab + C2)) / ((mu_a ** 2 + mu_b ** 2 + C1) * (saa + sbb + C2))
    return float(m.mean())


def dhash(gray_img, bits=16):
    im = Image.fromarray(gray_img.astype(np.uint8)) if isinstance(gray_img, np.ndarray) else gray_img
    im = im.convert("L").resize((bits + 1, bits), Image.BILINEAR)
    a = np.asarray(im, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def hamming(a, b):
    return int(np.count_nonzero(a != b))


def frame_at(path, t, out_png, scale_w=180):
    run([FFMPEG, "-v", "error", "-y", "-ss", "%.3f" % t, "-i", path, "-frames:v", "1",
         "-vf", "scale=%d:-2" % scale_w, out_png])
    return out_png if os.path.exists(out_png) else None


def strip(path, times_labels, out_png):
    tiles = []
    for t, lab in times_labels:
        p = out_png + ".tmp.png"
        if frame_at(path, t, p):
            tiles.append((lab, Image.open(p).copy()))
            os.remove(p)
    if not tiles:
        return None
    w, h = tiles[0][1].size
    sheet = Image.new("RGB", (len(tiles) * w, h + 16), "black")
    d = ImageDraw.Draw(sheet)
    for i, (lab, im) in enumerate(tiles):
        sheet.paste(im, (i * w, 0))
        d.text((i * w + 3, h + 2), lab, fill="white")
    sheet.save(out_png)
    return out_png


# ----------------------------------------------------------------------------- text norm
_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_MULT = {"hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000}
NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_MULT)


def words_to_digits(tokens):
    """Collapse runs of number words into digit tokens: 'twelve hundred fourteen' -> '1214',
    'a hundred and one' -> '101', 'ninety day' -> '90 day'. Non-number tokens pass through."""
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        # 'a hundred' / 'a thousand'
        if t in ("a", "an") and i + 1 < len(tokens) and tokens[i + 1] in _MULT:
            tokens = tokens[:i] + ["one"] + tokens[i + 1:]
            t = "one"
        if t not in NUMBER_WORDS:
            out.append(t); i += 1; continue
        total, cur, j, last = 0, 0, i, None
        while j < len(tokens):
            w = tokens[j]
            if w in _UNITS:
                if last in ("unit", "teen") or (last == "tens" and _UNITS[w] >= 10):
                    break                                   # 'twelve twelve' = a stutter, not 24
                cur += _UNITS[w]; last = "teen" if _UNITS[w] >= 10 else "unit"
            elif w in _TENS:
                if last in ("unit", "teen", "tens"):
                    break
                cur += _TENS[w]; last = "tens"
            elif w in _MULT:
                m = _MULT[w]
                if m == 100:
                    cur = (cur or 1) * 100
                else:
                    total += (cur or 1) * m; cur = 0
                last = None
            elif w == "and" and j + 1 < len(tokens) and tokens[j + 1] in NUMBER_WORDS and (cur or total):
                pass
            else:
                break
            j += 1
        out.append(str(total + cur))
        i = j
    return out


def norm_text(s, sp):
    s = (s or "").lower().replace("—", " ").replace("–", " ").replace("’", "'").replace("-", " ")
    for a, b in sp.get("notation", []):
        s = s.replace(a.lower(), b.lower())
    for k, v in sorted(sp.get("digits", {}).items(), key=lambda x: -len(x[0])):
        # whole-token only: a map entry "2" must never split "20" into "two 0"
        s = re.sub(r"(?<![\w$])%s(?![\w%%])" % re.escape(k.lower()), " %s " % v.lower(), s)
    s = re.sub(r"(\d),(\d{3})", r"\1\2", s)                  # 1,214 -> 1214
    s = re.sub(r"\$\s*([\d.]+)\s*(k\b)?", lambda m: " %s %s dollars " % (m.group(1), "thousand" if m.group(2) else ""), s)
    s = s.replace("%", " percent ")
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s)            # 90th -> 90
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    toks = words_to_digits(s.split())
    return " ".join(toks)


def heard(w, H, cutoff=85, prv=None, nxt=None):
    """A wanted word counts as heard when a heard word matches it closely (ASR spelling: flavored /
    flavoured, apoquel / apocale) or the compound reads across two heard words (hotspots / hot spots).
    Short words stay exact: "ago" must never be satisfied by "without"."""
    if w in H:
        return True
    if (nxt and w + nxt in H) or (prv and prv + w in H):
        return True                         # wanted "hot spots", heard "hotspots"
    if len(w) < 4:
        return False
    try:
        from rapidfuzz import fuzz
        ratio = lambda a, b: fuzz.ratio(a, b)  # noqa: E731
    except Exception:
        ratio = lambda a, b: 100.0 * difflib.SequenceMatcher(None, a, b).ratio()  # noqa: E731
    for i, h in enumerate(H):
        if ratio(w, h) >= cutoff:
            return True
        if i + 1 < len(H) and h + H[i + 1] == w:
            return True
    return False


def diff_line(want, heard_text, sp):
    W, H = norm_text(want, sp).split(), norm_text(heard_text, sp).split()
    if W == H:
        return "EXACT", [], []
    flags = []
    missing = [w for i, w in enumerate(W)
               if not heard(w, H, sp.get("fuzzy_word", 85), W[i - 1] if i else None, W[i + 1] if i + 1 < len(W) else None)]
    if W and H and H[0] != W[0] and W[0] not in H[:2]:
        flags.append(("DROPPED-OPENER", "'%s'" % W[0]))
    if W and H and W[-1] != H[-1] and W[-1] not in H[-3:]:
        flags.append(("DROPPED-TAIL", "'%s'" % W[-1]))
    for i in range(len(H) - 3):
        if H[i] == H[i + 2] and H[i + 1] == H[i + 3]:
            flags.append(("STUTTER", "'%s %s'" % (H[i], H[i + 1])))
            break
    for i in range(len(H) - 1):
        if H[i] == H[i + 1] and len(H[i]) > 3:
            flags.append(("STUTTER", "'%s %s'" % (H[i], H[i])))
            break
    claims = set(w.lower() for w in sp.get("claim_words", []))
    # 'one' is a pronoun far more often than a number in ad copy ("no one", "one more") -> not a claim number
    heard_nums = [w for w in H if w.isdigit() and w != "1"]
    want_nums = [w for w in W if w.isdigit() and w != "1"]
    if want_nums and heard_nums != want_nums:
        flags.append(("NUMERIC-MISMATCH", "want %s heard %s" % (" ".join(want_nums), " ".join(heard_nums) or "-")))
        flags = [f for f in flags if not (f[0] in ("DROPPED-OPENER", "DROPPED-TAIL") and f[1].strip("'").isdigit())]
    missing = [w for w in missing if not w.isdigit()]   # numbers already judged above
    claim_missing = [w for w in missing if w in claims]
    if claim_missing:
        flags.append(("CLAIM-DROP", ",".join(claim_missing)))
    elif len(missing) > max(2, len(W) * 0.2):
        flags.append(("TRUNCATED-SPEECH", "%d missing" % len(missing)))
    elif missing:
        flags.append(("DRIFT", ",".join(missing[:4])))
    return "drift", flags, missing


# ----------------------------------------------------------------------------- checks
class Report:
    def __init__(self):
        self.rows = {}

    def rec(self, it):
        r = self.rows.get(it["id"])
        if r is None:
            r = {"id": it["id"], "kind": it["kind"], "group": it["group"],
                 "file": os.path.basename(it["file"]) if it.get("file") else None, "findings": [], "metrics": {}}
            self.rows[it["id"]] = r
        return r

    def flag(self, it, code, detail=""):
        # qa_accept (the user, 2026-09-25): a shot may name a finding code it expects, WITH the
        # reason - the eye pass that cleared it. The finding is still recorded and shown, and is
        # downgraded to WARN, never dropped. Same idea as text_allow for the text layer. First
        # use: an earlier build's Seedance clips whose camera move opens on a re-framed
        # composition, and a "dead still for a beat" freeze the prompt asked for.
        f = {"code": code, "detail": detail}
        why = (it.get("expect", {}).get("qa_accept") or {}).get(code)
        if why:
            f["accepted"] = why
            f["detail"] = "%s | ACCEPTED: %s" % (detail, why)
        self.rec(it)["findings"].append(f)


def check_spec(it, prof, rep):
    r = rep.rec(it)
    if it.get("missing"):
        rep.flag(it, "MISSING-CLIP", "expected but not generated"); return
    if it["expect"].get("orphan"):
        rep.flag(it, "ORPHAN", "not in shotlist")
    aw, ah = [int(x) for x in (it["expect"].get("aspect") or prof["aspect"]).split(":")]   # a still may declare its own
    aspect = aw / ah
    if it["kind"] == "still":
        try:
            im = Image.open(it["file"]); im.load()
        except Exception as e:
            rep.flag(it, "CORRUPT", type(e).__name__); return
        w, h = im.size
        r["metrics"].update(res="%dx%d" % (w, h))
        if abs(w / h - aspect) > prof["aspect_tol"]:
            rep.flag(it, "WRONG-ASPECT", "%dx%d" % (w, h))
        if min(w, h) < prof["still_min_short_side"]:
            rep.flag(it, "LOW-RES", "%dx%d" % (w, h))
        return
    pr = probe(it["file"])
    if not pr or not pr["w"]:
        rep.flag(it, "UNREADABLE", ""); return
    it["_probe"] = pr
    r["metrics"].update(res="%dx%d" % (pr["w"], pr["h"]), fps=round(pr["fps"], 3), dur=round(pr["dur"], 2), audio=pr["audio"])
    if os.path.getsize(it["file"]) < 10240 or pr["dur"] < prof["min_duration"]:
        rep.flag(it, "TRUNCATED", "%.2fs %dB" % (pr["dur"], os.path.getsize(it["file"])))
    if abs(pr["w"] / pr["h"] - aspect) > prof["aspect_tol"]:
        rep.flag(it, "WRONG-ASPECT", "%dx%d" % (pr["w"], pr["h"]))
    # a shotlist may override the profile size per engine (CLIP_RES = {"seedance_2_5": [480, 854]})
    want_res = it["expect"].get("clip_res") or prof.get("clip_res")
    if want_res:
        want_w, want_h = want_res
        tol = int(prof.get("clip_res_tol") or 0)
        if abs(pr["w"] - want_w) > tol or abs(pr["h"] - want_h) > tol:
            rep.flag(it, "RES", "%dx%d want %dx%d%s"
                     % (pr["w"], pr["h"], want_w, want_h, " (+/-%d)" % tol if tol else ""))
    if prof.get("clip_fps") and abs(pr["fps"] - prof["clip_fps"]) > 0.05:
        rep.flag(it, "FPS", "%s want %s" % (pr["fps_raw"], prof["clip_fps"]))
    want_dur = it["expect"].get("duration") or prof.get("duration_default")
    if want_dur and abs(pr["dur"] - want_dur) > prof["duration_tol"]:
        rep.flag(it, "DUR", "%.2fs want %s" % (pr["dur"], want_dur))
    mode = prof["audio"]
    if mode == "by_shot":
        # speaking shot -> audio required; silent kind -> a stray track is muted at delivery (WARN),
        # except on a silent build where it fights the VO track (still WARN, --strict promotes)
        if it["expect"].get("speaks") and not it["expect"].get("silent_build"):
            mode = "require"
        else:
            mode = "stray"
    if mode == "require" and not pr["audio"]:
        rep.flag(it, "AUDIO-MISSING", "speech clip with no audio stream")
    elif mode == "forbid" and pr["audio"]:
        rep.flag(it, "HAS-AUDIO", "strip before delivery")
    elif mode == "stray" and pr["audio"]:
        rep.flag(it, "STRAY-AUDIO", "silent kind carries an audio stream - mute at delivery")


def check_framescan(it, prof, rep, qa_dir):
    if it["kind"] != "clip" or it.get("missing"):
        return
    fs = prof["framescan"]
    pr = it.get("_probe") or probe(it["file"])
    if not pr:
        return
    g = decode_gray(it["file"], pr["w"], pr["h"], fs["scale_w"])
    n = len(g)
    r = rep.rec(it)
    if n < 2:
        rep.flag(it, "UNREADABLE", "decoded %d frames" % n); return
    fps = pr["fps"] or (n / pr["dur"] if pr["dur"] else 24.0)
    d = np.abs(np.diff(g, axis=0))
    mean_d = d.mean(axis=(1, 2))                       # per transition
    px_share = (d > 12).mean(axis=(1, 2))
    luma = g.mean(axis=(1, 2))
    span = float(np.abs(g[-1] - g[0]).mean())          # first -> last displacement: a slow push reads
    span_ssim = ssim(g[0], g[-1])                       # low per-frame but large here; a true static reads low on both
    r["metrics"].update(frames=n, motion_mean=round(float(mean_d.mean()), 2),
                        moving_px=round(float(px_share.mean()) * 100, 2),
                        span_diff=round(span, 2), span_ssim=round(span_ssim, 3))
    flagged = []                                        # (time, label)
    # whole-clip static: low per-frame motion AND the last frame is still the first frame
    if (mean_d.mean() < fs["static_diff"] and px_share.mean() < fs["static_px"]
            and span < fs["static_span"] and span_ssim > fs["static_span_ssim"]):
        # a seated talking head that barely moves is LOW-MOTION (queue), b-roll that never moves is STATIC (defect)
        code = "LOW-MOTION" if it["expect"].get("speaks") else "STATIC"
        rep.flag(it, code, "mean %.2f, %.2f%% px, span %.1f" % (mean_d.mean(), px_share.mean() * 100, span))
    # freeze runs
    same = mean_d < fs["freeze_diff"]
    i = 0
    while i < n - 1:
        if same[i]:
            j = i
            while j < n - 1 and same[j]:
                j += 1
            secs = (j - i + 1) / fps
            if secs >= fs["freeze_min_s"] and not (mean_d.mean() < fs["static_diff"]):
                clip_dur = (pr.get("dur") or 0) or (n / fps)
                share = secs / clip_dur if clip_dur else 0.0
                major = fs.get("freeze_fail_share")
                code = "FREEZE-MAJOR" if (major and share >= major) else "FREEZE"
                rep.flag(it, code, "%.2fs at %.2fs (%.0f%% of clip)" % (secs, i / fps, share * 100))
                flagged.append((i / fps, "freeze in %.1fs" % (i / fps)))
                flagged.append((j / fps, "freeze out %.1fs" % (j / fps)))
            i = j
        i += 1
    # spikes
    med = float(np.median(mean_d[1:])) if n > 2 else float(mean_d.mean())
    mad = float(np.median(np.abs(mean_d[1:] - med))) + 1e-6
    thr = max(fs["spike_floor"], med + fs["spike_k"] * mad)
    spikes = [k for k in range(1, n - 1) if mean_d[k] > thr]
    if spikes:
        # collapse neighbours
        groups, cur = [], [spikes[0]]
        for k in spikes[1:]:
            if k - cur[-1] <= 2:
                cur.append(k)
            else:
                groups.append(cur); cur = [k]
        groups.append(cur)
        if len(groups) >= 3:
            rep.flag(it, "FLICKER", "%d spikes (thr %.1f)" % (len(groups), thr))
        else:
            for gp in groups:
                k = gp[0]
                rep.flag(it, "SPIKE", "Δ%.1f at %.2fs (median %.1f)" % (mean_d[k], (k + 1) / fps, med))
        for gp in groups[:4]:
            k = gp[0]
            flagged.append((k / fps, "pre %.2fs" % (k / fps)))
            flagged.append(((k + 1) / fps, "post %.2fs" % ((k + 1) / fps)))
    # first-frame glitch
    if n > 6 and mean_d[0] > max(fs["spike_floor"] * 0.6, med * fs["first_frame_glitch_k"]) and mean_d[0] > thr * 0.6 and 0 not in spikes:
        rep.flag(it, "FIRST-FRAME-GLITCH", "frame0->1 Δ%.1f vs median %.1f" % (mean_d[0], med))
        flagged.append((0, "frame0")); flagged.append((1 / fps, "frame1"))
    # black
    dark = luma < fs["black_luma"]
    if dark.sum() >= fs["black_min_frames"]:
        first = int(np.argmax(dark))
        rep.flag(it, "BLACK", "%d frames, first at %.2fs" % (int(dark.sum()), first / fps))
        flagged.append((first / fps, "black %.2fs" % (first / fps)))
    r["metrics"]["diff_profile"] = [round(float(x), 1) for x in mean_d[:: max(1, n // 40)]]
    if flagged:
        seen, uniq = set(), []
        for t, lab in flagged:
            key = round(t, 2)
            if key not in seen:
                seen.add(key); uniq.append((t, lab))
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(os.path.basename(it["file"]))[0])
        out = os.path.join(qa_dir, "%s_flagged.png" % safe)
        if strip(it["file"], uniq[:8], out):
            r["strip"] = out


def startframe_thresholds(sf, model):
    """(ssim_fail, ssim_warn) for one engine. An engine absent from by_model, or no model at all,
    gets the global pair - so a profile without by_model is unchanged."""
    over = (sf.get("by_model") or {}).get(model) or {}
    return over.get("ssim_fail", sf["ssim_fail"]), over.get("ssim_warn", sf["ssim_warn"])


def check_startframe(it, prof, rep):
    if it["kind"] != "clip" or it.get("missing"):
        return
    sf = prof["startframe"]
    r = rep.rec(it)
    st = it["expect"].get("start_still")
    if st and os.path.exists(st):
        size = (108, 192)
        tmp = it["file"] + ".qa_f0.png"
        if frame_at(it["file"], 0.0, tmp, 108):
            b = load_gray(tmp, size); os.remove(tmp)
            best = max(((ssim(load_gray(c, size), b), c) for c in [st] + (it["expect"].get("start_still_family") or [])
                        if os.path.exists(c)), key=lambda x: x[0])
            s, st = best
            it["expect"]["start_still"] = st          # the take's real start frame; identity uses it too
            r["metrics"]["start_still"] = os.path.basename(st)
            r["metrics"]["start_ssim"] = round(s, 3)
            s_fail, s_warn = startframe_thresholds(sf, it["expect"].get("model"))
            if s < s_fail:
                rep.flag(it, "START-FRAME-MISMATCH", "ssim %.2f vs %s" % (s, os.path.basename(st)))
            elif s < s_warn:
                rep.flag(it, "START-FRAME-DRIFT", "ssim %.2f vs %s" % (s, os.path.basename(st)))
    en = it["expect"].get("end_still")
    if en and os.path.exists(en):
        pr = it.get("_probe") or probe(it["file"])
        size = (108, 192)
        tmp = it["file"] + ".qa_fl.png"
        if pr and frame_at(it["file"], max(0, pr["dur"] - 0.2), tmp, 108):
            a = load_gray(en, size); b = load_gray(tmp, size); os.remove(tmp)
            s = ssim(a, b)
            r["metrics"]["end_ssim"] = round(s, 3)
            if s < sf["end_ssim_fail"]:
                rep.flag(it, "END-FRAME-DRIFT", "ssim %.2f vs %s" % (s, os.path.basename(en)))


def _pcm(path):
    r = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def check_audio(it, prof, rep, group_loud):
    if it["kind"] != "clip" or it.get("missing"):
        return
    pr = it.get("_probe") or probe(it["file"])
    if not pr or not pr["audio"]:
        return
    ac = prof["audio_checks"]
    r = rep.rec(it)
    x = _pcm(it["file"])
    if len(x) < 1600:
        rep.flag(it, "ALL-SILENT", "no audio samples"); return
    win = 1600  # 100 ms
    nwin = len(x) // win
    rms = np.sqrt((x[: nwin * win].reshape(nwin, win) ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    peak_db = float(20 * np.log10(np.abs(x).max() + 1e-9))
    floor = float(np.percentile(db, 10))
    speech = float(np.percentile(db, 90))
    r["metrics"].update(peak_dbfs=round(peak_db, 1), noise_floor_dbfs=round(floor, 1), speech_dbfs=round(speech, 1),
                        snr_db=round(speech - floor, 1))
    if peak_db < -50:
        rep.flag(it, "ALL-SILENT", "peak %.1f dBFS" % peak_db); return
    active = db > ac["silence_db"]
    if active.any():
        lead = float(np.argmax(active)) * 0.1
        tail = float(len(active) - 1 - np.argmax(active[::-1])) * 0.1
        tail_s = pr["dur"] - tail - 0.1
        r["metrics"].update(lead_silence_s=round(lead, 2), tail_silence_s=round(max(0, tail_s), 2))
        if lead > ac["lead_silence_warn_s"]:
            rep.flag(it, "LEAD-SILENCE", "%.2fs before sound" % lead)
        if tail_s > ac["tail_silence_warn_s"]:
            rep.flag(it, "TAIL-SILENCE", "%.2fs after sound (post conform, not a reroll)" % tail_s)
    if it["expect"].get("speaks"):
        if floor > ac["noise_floor_warn_dbfs"]:
            rep.flag(it, "NOISY", "floor %.1f dBFS" % floor)
        if speech - floor < ac["snr_warn_db"]:
            rep.flag(it, "LOW-SNR", "%.1f dB speech-to-floor" % (speech - floor))
    lr = run([FFMPEG, "-v", "info", "-nostats", "-i", it["file"], "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", lr.stderr or "")
    if m:
        lufs = float(m[-1])
        r["metrics"]["lufs"] = lufs
        group_loud[it["group"]].append((it["id"], lufs))


def check_speech(it, prof, rep, model_holder):
    if it["kind"] != "clip" or it.get("missing") or not it["expect"].get("speaks"):
        return
    pr = it.get("_probe") or probe(it["file"])
    if not pr or not pr["audio"]:
        return
    sp = prof["speech"]
    r = rep.rec(it)
    if model_holder.get("model") is None:
        try:
            import whisper
            model_holder["model"] = whisper.load_model(sp["whisper_model"])
        except Exception as e:
            model_holder["model"] = False
            print("NOTE: whisper unavailable (%s) - speech layer skipped" % e)
    model = model_holder["model"]
    if not model:
        return
    wav = it["file"] + ".qa.wav"
    run([FFMPEG, "-v", "error", "-y", "-i", it["file"], "-vn", "-ac", "1", "-ar", "16000", wav])
    try:
        tr = model.transcribe(wav, language=sp.get("language") or None, fp16=False, word_timestamps=True)
    finally:
        if os.path.exists(wav):
            os.remove(wav)
    heard = (tr.get("text") or "").strip()
    words = [w for seg in tr.get("segments", []) for w in seg.get("words", [])]
    r["metrics"]["heard"] = heard
    if not heard:
        rep.flag(it, "NO-TRANSCRIPT", "whisper heard nothing"); return
    want = it["expect"].get(sp.get("line_field", "line")) or it["expect"].get("line")
    if want:
        verdict, flags, missing = diff_line(want, heard, sp)
        r["metrics"]["dialogue"] = verdict
        for code, det in flags:
            rep.flag(it, code, det)
    if words:
        t0, t1 = float(words[0]["start"]), float(words[-1]["end"])
        span = max(0.1, t1 - t0)
        nw = len(norm_text(heard, sp).split())
        wps = nw / span
        r["metrics"].update(speech_start=round(t0, 2), speech_end=round(t1, 2), wps=round(wps, 2))
        if t0 > sp["late_start_s"]:
            rep.flag(it, "LATE-START", "first word at %.2fs" % t0)
        if pr["dur"] - t1 > sp["dead_tail_s"]:
            rep.flag(it, "DEAD-TAIL", "%.2fs after last word (post conform)" % (pr["dur"] - t1))
        if nw >= 4:
            if wps < sp["min_wps"]:
                rep.flag(it, "STRETCHED", "%.2f words/s" % wps)
            elif wps > sp["max_wps"]:
                rep.flag(it, "RUSHED", "%.2f words/s" % wps)


def check_dupes(items, prof, rep):
    dp = prof["dupes"]
    hashes = []
    for it in items:
        if it.get("missing"):
            continue
        if it["kind"] == "still":
            try:
                hashes.append((it, dhash(Image.open(it["file"]), dp["hash_bits"])))
            except Exception:
                pass
        elif dp.get("include_clip_frames"):
            tmp = it["file"] + ".qa_h.png"
            if frame_at(it["file"], 0.0, tmp, 96):
                hashes.append((it, dhash(Image.open(tmp), dp["hash_bits"]))); os.remove(tmp)
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            a, b = hashes[i], hashes[j]
            if a[0]["kind"] != b[0]["kind"]:
                continue
            if a[0]["kind"] == "clip" and a[0]["group"] == b[0]["group"]:
                continue  # sibling takes of one shot share a seed on purpose
            h = hamming(a[1], b[1])
            if h <= dp["max_hamming"]:
                rep.flag(a[0], "DUPLICATE", "≈ %s (hamming %d)" % (b[0]["id"], h))


# ----------------------------------------------------------------------------- identity (faces)
MODELS_DIR = os.path.join(HERE, "models")
_FACE = {}


def _face_models():
    if "det" not in _FACE:
        import cv2
        d = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")
        r = os.path.join(MODELS_DIR, "face_recognition_sface_2021dec.onnx")
        if not (os.path.exists(d) and os.path.exists(r)):
            _FACE["det"] = None
        else:
            _FACE["det"] = cv2.FaceDetectorYN.create(d, "", (320, 320), 0.6)
            _FACE["rec"] = cv2.FaceRecognizerSF.create(r, "")
    return _FACE.get("det"), _FACE.get("rec")


def face_embedding(img_bgr):
    """Largest face in the image -> SFace 128-d feature, or None."""
    import cv2
    det, rec = _face_models()
    if det is None or img_bgr is None:
        return None
    h, w = img_bgr.shape[:2]
    s = 640.0 / max(h, w)
    im = cv2.resize(img_bgr, (max(1, int(w * s)), max(1, int(h * s))))
    det.setInputSize((im.shape[1], im.shape[0]))
    _, faces = det.detect(im)
    if faces is None or len(faces) == 0:
        return None
    f = faces[int(np.argmax(faces[:, 2] * faces[:, 3]))]
    return rec.feature(rec.alignCrop(im, f))


def face_cos(a, b):
    import cv2
    return float(_FACE["rec"].match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


_EMB = {}       # item id -> (identity_ref, mean SFace embedding of its sampled frames)


def np_cos(a, b):
    a = np.asarray(a, dtype=float).ravel(); b = np.asarray(b, dtype=float).ravel()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / d) if d else 0.0


def cast_outliers(embs, warn):
    """embs: {id: (ref, emb)}. Per reference group of 3+ clips, the members whose cosine to the group
    centroid falls under `warn` while the group's median holds -> the take with the other face.
    (an earlier ad: 'not the same woman as at 0:18' - the client compared take to take, QA compared to a sheet.)"""
    out = {}
    groups = {}
    for i, (ref, e) in embs.items():
        groups.setdefault(ref, []).append((i, e))
    for ref, members in groups.items():
        if len(members) < 3:
            continue
        c = np.mean([e for _, e in members], axis=0)
        cs = {i: np_cos(c, e) for i, e in members}
        med = float(np.median(list(cs.values())))
        if med < warn:
            continue
        for i, v in cs.items():
            if v < warn:
                out[i] = (v, med, ref)
    return out


def check_cast(items, prof, rep):
    warn = prof["identity"]["cos_warn"]
    for i, (v, med, ref) in cast_outliers(_EMB, warn).items():
        it = next((x for x in items if x["id"] == i), None)
        if it is not None:
            rep.flag(it, "CAST-OUTLIER", "cos %.2f to the cast centroid (group median %.2f, %s)" % (v, med, os.path.basename(ref)))


# ----------------------------------------------------------------------------- voice (pitch)
_F0 = {}


def voice_outliers(f0_by_id, max_st):
    """{id: semitones off its group's median} for talking clips more than max_st away. A value is
    f0 or (group, f0); a group is one voice population (the _voicechange results vs the originals)."""
    import math
    groups = {}
    for i, v in f0_by_id.items():
        g, f = v if isinstance(v, tuple) else ("", v)
        if f:
            groups.setdefault(g, []).append((i, f))
    out = {}
    for g, members in groups.items():
        if len(members) < 3:
            continue
        med = float(np.median([f for _, f in members]))
        for i, f in members:
            st = 12 * math.log2(f / med)
            if abs(st) > max_st:
                out[i] = st
    return out


def check_voice(it, prof, rep, voice_cache):
    if it["kind"] != "clip" or it.get("missing") or not it["expect"].get("speaks"):
        return
    import voice_stage as VS
    r = rep.rec(it)
    try:
        x, sr = VS.pcm(it["file"])
        f0 = VS.median_f0(x, sr)
    except Exception:
        return
    if not f0:
        return
    r["metrics"]["f0_hz"] = round(f0, 1)
    _F0[it["id"]] = (os.path.dirname(os.path.abspath(it["file"])), f0)   # group = folder: swapped results vs originals
    vo = it["expect"].get("vo_master")
    if vo and os.path.isfile(vo):
        if vo not in voice_cache:
            xm, srm = VS.pcm(vo, seconds=60)
            voice_cache[vo] = VS.median_f0(xm, srm)
        if voice_cache[vo]:
            st = VS.semitones(f0, voice_cache[vo])
            r["metrics"]["vo_semitones"] = round(st, 2)
            if abs(st) > prof.get("voice", {}).get("max_semitones", 1.5):
                rep.flag(it, "VOICE-PITCH", "%+.1f semitones vs the VO master" % st)


def check_lipsync(it, prof, rep):
    if it["kind"] != "clip" or it.get("missing") or not it["expect"].get("speaks"):
        return
    import vision_layers as VL
    lc = prof.get("lipsync", {})
    series = VL.clip_jaw_series(it["file"], lc.get("sample_fps", 8))
    seen = [(t, j) for t, j in series if j is not None]
    r = rep.rec(it)
    if len(seen) < max(6, len(series) // 3):
        rep.flag(it, "NO-MOUTH", "face found on %d of %d sampled frames" % (len(seen), len(series))); return
    env = VL.audio_envelope(it["file"], [t for t, _ in seen])
    rr = VL.lipsync_r([j for _, j in seen], env)
    r["metrics"]["lipsync_r"] = None if rr is None else round(rr, 3)
    if rr is not None and rr < lc.get("min_r", 0.3):
        rep.flag(it, "LIPSYNC-LOW", "mouth/audio r=%.2f" % rr)


def check_objects(it, prof, rep):
    if it.get("missing"):
        return
    import vision_layers as VL
    oc = prof.get("objects", {})
    if it["kind"] == "still":
        counts = VL.count_objects(_read_bgr(it["file"]), oc.get("conf", 0.35))
    else:
        counts = VL.clip_object_counts(it["file"], oc.get("clip_samples", 3), oc.get("conf", 0.35))
    if counts is None:
        return
    r = rep.rec(it)
    r["metrics"]["objects"] = counts
    ex = it["expect"]
    if counts.get("person") and ex.get("people") is False:
        # YOLO reads hands and forearms as 'person'; a person on a no-people shot needs a FACE to count
        if it["kind"] == "still":
            face = face_embedding(_read_bgr(it["file"])) is not None
        else:
            tmp = it["file"] + ".qa_obj.png"
            pr = it.get("_probe") or probe(it["file"])
            face = bool(pr) and frame_at(it["file"], pr["dur"] / 2, tmp, 720) and face_embedding(_read_bgr(tmp)) is not None
            if os.path.exists(tmp):
                os.remove(tmp)
        if not face:
            counts = {k: v for k, v in counts.items() if k != "person"}
            r["metrics"]["objects_note"] = "person without a face (hands) - not counted"
    for code, detail in VL.object_flags(counts, {"people": ex.get("people"), "products": ex.get("products"),
                                                  "allow_phone": ex.get("allow_phone")}):
        rep.flag(it, code, detail)


def check_voice_group(items, prof, rep):
    for i, st in voice_outliers(_F0, prof.get("voice", {}).get("max_semitones", 1.5)).items():
        it = next((x for x in items if x["id"] == i), None)
        if it is not None:
            rep.flag(it, "VOICE-DRIFT", "%+.1f semitones off the other talking clips" % st)


def _read_bgr(path):
    import cv2
    data = np.fromfile(path, dtype=np.uint8)          # unicode-safe on Windows
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def check_identity(it, prof, rep, ref_cache):
    if it.get("missing"):
        return
    idc = prof["identity"]
    ref = it["expect"].get("identity_ref") or idc.get("ref")
    if not ref or not os.path.exists(ref):
        return
    det, _rec = _face_models()
    if det is None:
        rep.flag(it, "IDENTITY-SKIPPED", "face models missing under asset-qc-local/models"); return
    # A clip is judged against the still it was FIRED from when that still holds a face: the master
    # still is the lock the editor approved at Gate 2, the sheet closeup is one step upstream of it.
    # Measured on an earlier build (2026-09-26): the same clips read cos 0.5 vs the sheet closeup and 0.73-0.89
    # vs their master still.
    st = it["expect"].get("start_still") if it["kind"] == "clip" else None
    if st and os.path.exists(st) and os.path.abspath(st) != os.path.abspath(it["file"]):
        if st not in ref_cache:
            ref_cache[st] = face_embedding(_read_bgr(st))
        if ref_cache[st] is not None:
            ref = st
    if ref not in ref_cache:
        ref_cache[ref] = face_embedding(_read_bgr(ref))
    ref_emb = ref_cache[ref]
    r = rep.rec(it)
    if ref_emb is None:
        r["metrics"]["identity"] = "ref has no detectable face"; return
    if os.path.abspath(it["file"]) == os.path.abspath(ref):
        return
    scores = []
    if it["kind"] == "still":
        e = face_embedding(_read_bgr(it["file"]))
        if e is not None:
            scores.append(face_cos(ref_emb, e))
    else:
        pr = it.get("_probe") or probe(it["file"])
        if not pr:
            return
        n = idc["clip_samples"]
        embs = []
        for k in range(n):
            t = pr["dur"] * (k + 0.5) / n
            tmp = it["file"] + ".qa_id.png"
            if frame_at(it["file"], t, tmp, 720):
                e = face_embedding(_read_bgr(tmp)); os.remove(tmp)
                if e is not None:
                    scores.append(face_cos(ref_emb, e)); embs.append(np.asarray(e, dtype=np.float32).ravel())
        if embs:
            _EMB[it["id"]] = (ref, np.mean(embs, axis=0))
    if not scores:
        r["metrics"]["identity_cos"] = None
        if idc.get("require_face") or it["expect"].get("speaks"):
            rep.flag(it, "NO-FACE", "no detectable face vs %s" % os.path.basename(ref))
        return
    lo, mean = min(scores), sum(scores) / len(scores)
    r["metrics"].update(identity_cos=round(mean, 3), identity_cos_min=round(lo, 3), identity_ref=os.path.basename(ref))
    if mean < idc["cos_fail"]:
        rep.flag(it, "IDENTITY-MISMATCH", "cos %.2f vs %s" % (mean, os.path.basename(ref)))
    elif lo < idc["cos_fail"] or mean < idc["cos_warn"]:
        rep.flag(it, "IDENTITY-DRIFT", "cos mean %.2f min %.2f vs %s" % (mean, lo, os.path.basename(ref)))


# ----------------------------------------------------------------------------- text (OCR)
_OCR = {}


def ocr_strings(path, min_conf):
    if "engine" not in _OCR:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR["engine"] = RapidOCR()
        except Exception as e:
            _OCR["engine"] = None
            print("NOTE: rapidocr_onnxruntime unavailable (%s) - text layer skipped" % e)
    eng = _OCR["engine"]
    if eng is None:
        return None
    res, _ = eng(path)
    return [(r[1].strip(), float(r[2])) for r in (res or []) if float(r[2]) >= min_conf and r[1].strip()]


def check_text(it, prof, rep):
    if it.get("missing"):
        return
    tc = prof["text"]
    allow = [re.compile(p, re.I) for p in (tc.get("allow") or []) + (it["expect"].get("text_allow") or [])]
    found = []
    if it["kind"] == "still":
        s = ocr_strings(it["file"], tc["min_conf"])
        if s is None:
            return
        found = s
    else:
        pr = it.get("_probe") or probe(it["file"])
        if not pr:
            return
        n = tc["clip_samples"]
        for k in range(n):
            tmp = it["file"] + ".qa_ocr.png"
            if frame_at(it["file"], pr["dur"] * (k + 0.5) / n, tmp, 720):
                s = ocr_strings(tmp, tc["min_conf"]); os.remove(tmp)
                if s is None:
                    return
                found += s
    import difflib
    seen, uniq = set(), []
    for t, c in found:
        if t.lower() not in seen:
            seen.add(t.lower()); uniq.append((t, c))
    r = rep.rec(it)
    if uniq:
        r["metrics"]["text"] = ["%s (%.2f)" % (t, c) for t, c in uniq[:12]]
    allow_plain = [re.sub(r"[^a-z0-9 ]", "", p.pattern.lower()) for p in allow]

    def permitted(t):
        if any(p.search(t) for p in allow):
            return True
        # a real label read at 720p comes back garbled ("PR0DUCT LABEI"): fuzzy-match it to the allow list
        tt = re.sub(r"[^a-z0-9 ]", "", t.lower())
        return any(ap and difflib.SequenceMatcher(None, tt, ap).ratio() >= tc.get("fuzzy", 0.6) for ap in allow_plain)

    bad = [t for t, c in uniq
           if len(re.sub(r"[^A-Za-z0-9]", "", t)) >= tc.get("min_len", 3)     # drop 1-2 char / CJK / punctuation junk
           and re.search(r"[A-Za-z0-9]", t)
           and (it["kind"] == "still" or c >= tc.get("clip_min_conf", 0.8))     # compressed frames need a higher bar
           and not permitted(t)]
    if bad:
        code = "TEXT-FORBIDDEN" if tc.get("mode") == "fail" else "TEXT-FOUND"
        rep.flag(it, code, " | ".join(bad[:6]))


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir")
    src.add_argument("--manifest")
    ap.add_argument("--profile", default=None, help="broll | flow | path to json")
    ap.add_argument("--set", action="append", default=[], help="override profile key, e.g. --set speech.whisper_model=base")
    ap.add_argument("--layers", default="spec,framescan,startframe,audio,speech,dupes,identity,text,voice,lipsync,objects")
    ap.add_argument("--only", default="", help="comma list of ids/groups to check")
    ap.add_argument("--out", default=None, help="report dir (default <root>/_qa)")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the report json instead of the table")
    ap.add_argument("--exclude", default=None, help="folder mode: comma list of dir names to skip (default: %s)" % ",".join(DEFAULT_EXCLUDE))
    a = ap.parse_args()

    if a.dir:
        prof = load_profile(a.profile or "broll", a.set)
        items, root = items_from_dir(a.dir, a.exclude.split(",") if a.exclude is not None else None)
    else:
        prof = load_profile(a.profile or "flow", a.set)
        items, root = items_from_flow(a.manifest)

    layers = {x.strip() for x in a.layers.split(",") if x.strip()}
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    if only:
        items = [it for it in items if it["id"] in only or it["group"] in only]
    qa_dir = a.out or os.path.join(root, "_qa")
    os.makedirs(qa_dir, exist_ok=True)

    rep = Report()
    group_loud = defaultdict(list)
    model_holder = {}
    ref_cache = {}
    voice_cache = {}
    print("profile %s | %d items (%d clips, %d stills) | layers %s" % (
        prof["name"], len(items), sum(i["kind"] == "clip" for i in items), sum(i["kind"] == "still" for i in items),
        ",".join(sorted(layers))))
    for it in items:
        rep.rec(it)
        if "spec" in layers:
            check_spec(it, prof, rep)
        if it.get("missing"):
            continue
        if "framescan" in layers:
            check_framescan(it, prof, rep, qa_dir)
        if "startframe" in layers:
            check_startframe(it, prof, rep)
        if "audio" in layers:
            check_audio(it, prof, rep, group_loud)
        if "speech" in layers:
            check_speech(it, prof, rep, model_holder)
        if "identity" in layers:
            check_identity(it, prof, rep, ref_cache)
        if "voice" in layers:
            check_voice(it, prof, rep, voice_cache)
        if "lipsync" in layers:
            check_lipsync(it, prof, rep)
        if "objects" in layers:
            check_objects(it, prof, rep)
        if "text" in layers:
            check_text(it, prof, rep)
        r = rep.rows[it["id"]]
        print("  %-38s %s" % (it["id"][-38:], ", ".join(f["code"] for f in r["findings"]) or "ok"), flush=True)
    if "identity" in layers:
        check_cast(items, prof, rep)
    if "voice" in layers:
        check_voice_group(items, prof, rep)
    if "dupes" in layers:
        check_dupes(items, prof, rep)
    if "audio" in layers:
        for gname, lst in group_loud.items():
            if len(lst) > 1:
                vals = [v for _i, v in lst]
                if max(vals) - min(vals) > prof["audio_checks"]["loudness_spread_warn_lu"]:
                    for iid, v in lst:
                        rep.rows[iid]["findings"].append({"code": "LOUDNESS-SPREAD",
                                                          "detail": "%.1f LUFS, siblings span %.1f LU" % (v, max(vals) - min(vals))})

    # severity
    pol = prof["policy"]
    sev = {c: "FAIL" for c in pol["FAIL"]}
    sev.update({c: "WARN" for c in pol["WARN"]})
    if a.strict:
        for c in prof.get("strict_promote", []):
            sev[c] = "FAIL"
    n_fail = n_warn = 0
    for r in rep.rows.values():
        for f in r["findings"]:
            f["severity"] = sev.get(f["code"], "INFO")
            if f.get("accepted") and f["severity"] == "FAIL":
                f["severity"] = "WARN"
        r["verdict"] = ("FAIL" if any(f["severity"] == "FAIL" for f in r["findings"])
                        else "WARN" if any(f["severity"] == "WARN" for f in r["findings"]) else "PASS")
        n_fail += r["verdict"] == "FAIL"; n_warn += r["verdict"] == "WARN"

    report = {"profile": prof["name"], "root": root, "strict": a.strict, "layers": sorted(layers),
              "summary": {"items": len(rep.rows), "pass": len(rep.rows) - n_fail - n_warn, "warn": n_warn, "fail": n_fail},
              "rows": list(rep.rows.values())}
    rp = os.path.join(qa_dir, "qa_report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)

    if a.json:
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        print("\n" + "=" * 78)
        print("%-40s %-5s %s" % ("item", "verd", "findings"))
        for r in report["rows"]:
            if r["verdict"] == "PASS":
                continue
            fl = "; ".join("%s%s" % (f["code"], ("(" + f["detail"] + ")") if f["detail"] else "") for f in r["findings"])
            print("%-40s %-5s %s" % (r["id"][-40:], r["verdict"], fl[:300]))
            if r.get("strip"):
                print("%-40s       strip: %s" % ("", r["strip"]))
        print("=" * 78)
        s = report["summary"]
        print("items %d   pass %d   warn %d   fail %d   -> %s" % (s["items"], s["pass"], s["warn"], s["fail"], rp))
        print("Automated-clean != deliverable: read the strips, then the human watch-through in a real player.")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
