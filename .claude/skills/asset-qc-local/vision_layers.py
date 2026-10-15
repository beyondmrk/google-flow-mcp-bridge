#!/usr/bin/env python3
"""vision_layers.py - the two QA layers that need the downloaded models (2026-09-26, the user's yes):

  lipsync  MediaPipe FaceLandmarker (models/face_landmarker.task): the jawOpen blendshape per sampled
           frame against the audio envelope. A talking clip whose mouth does not follow its own audio
           (voice_change slur, a dubbed take, a frozen mouth) reads as LIPSYNC-LOW; no face on a
           speaking clip reads NO-MOUTH.
  objects  YOLOv8n (models/yolov8n.pt, ultralytics): counts of cell phone / bottle / person on stills
           and sampled clip frames. PHONE-IN-FRAME (the Wan "iPhone" trap, the TH_A desk phone),
           PRODUCT-COUNT (Hands Down S01 drew two bottles), PERSON-IN-FRAME on a `people: false` shot.

Pure decision helpers are at the top and tested; the model calls sit below and load lazily.
"""
import os, subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
WATCH = {"cell phone": "PHONE-IN-FRAME", "bottle": "PRODUCT-COUNT", "person": "PERSON-IN-FRAME"}
MIN_CONF = {"person": 0.6}     # hands-only shots read as 'person' at 0.35 (Hands Down S01_v04: 3 persons, no people)


# ---------------------------------------------------------------- pure helpers (tested)
def smooth(x, k=3):
    x = np.asarray(x, dtype=float)
    if len(x) < k:
        return x
    ker = np.ones(k) / k
    return np.convolve(x, ker, mode="same")


def lipsync_r(jaw, env):
    """Pearson r between the mouth-open series and the audio envelope, both smoothed; None when
    either has no variation (a silent clip, a closed mouth throughout)."""
    if np.std(np.asarray(jaw, dtype=float)) < 1e-3 or np.std(np.asarray(env, dtype=float)) < 1e-6:
        return None                              # a closed mouth throughout, or silence
    a, b = smooth(jaw), smooth(env)
    n = min(len(a), len(b))
    if n < 6:
        return None
    a, b = a[:n], b[:n]
    return float(np.corrcoef(a, b)[0, 1])


def object_flags(counts, expect):
    """counts: {label: max count seen}; expect: {"people": bool|None, "products": int|None,
    "allow_phone": bool}. -> [(code, detail)]"""
    out = []
    if counts.get("cell phone", 0) and not expect.get("allow_phone"):
        out.append(("PHONE-IN-FRAME", "%d phone(s) detected" % counts["cell phone"]))
    want = expect.get("products")
    if want is not None and counts.get("bottle", 0) > want:
        out.append(("PRODUCT-COUNT", "%d bottles, want %d" % (counts["bottle"], want)))
    if expect.get("people") is False and counts.get("person", 0):
        out.append(("PERSON-IN-FRAME", "%d person(s) on a no-people shot" % counts["person"]))
    return out


# ---------------------------------------------------------------- models (lazy)
_M = {}


def landmarker():
    if "lm" not in _M:
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import vision, BaseOptions
            opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=os.path.join(MODELS, "face_landmarker.task")),
                                                output_face_blendshapes=True, num_faces=1)
            _M["lm"] = vision.FaceLandmarker.create_from_options(opts)
            _M["mp"] = mp
        except Exception as e:
            print("NOTE: face landmarker unavailable (%s) - lipsync layer skipped" % e)
            _M["lm"] = None
    return _M["lm"]


def yolo():
    if "yolo" not in _M:
        try:
            from ultralytics import YOLO
            _M["yolo"] = YOLO(os.path.join(MODELS, "yolov8n.pt"))
        except Exception as e:
            print("NOTE: yolov8n unavailable (%s) - objects layer skipped" % e)
            _M["yolo"] = None
    return _M["yolo"]


def jaw_open(bgr):
    """jawOpen blendshape (0..1) for the largest face, or None."""
    import cv2
    lm = landmarker()
    if lm is None:
        return None
    mp = _M["mp"]
    h, w = bgr.shape[:2]
    s = 720.0 / max(h, w)
    im = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))))
    r = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(im, cv2.COLOR_BGR2RGB)))
    if not r.face_blendshapes:
        return None
    return next((b.score for b in r.face_blendshapes[0] if b.category_name == "jawOpen"), None)


def clip_jaw_series(path, fps=8.0):
    """[(t, jawOpen|None)] sampled at `fps` through the clip."""
    import cv2
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    step = max(1, int(round(src_fps / fps)))
    out, i = [], 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            out.append((i / src_fps, jaw_open(fr)))
        i += 1
    cap.release()
    return out


def audio_envelope(path, times, win=0.06):
    """RMS of the clip's audio around each sample time (same grid as the jaw series)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(float) / 32768.0
    sr = 16000
    env = []
    for t in times:
        a, b = int(max(0, (t - win / 2) * sr)), int(min(len(x), (t + win / 2) * sr))
        env.append(float(np.sqrt((x[a:b] ** 2).mean())) if b > a else 0.0)
    return env


def count_objects(bgr, conf=0.35):
    m = yolo()
    if m is None:
        return None
    r = m.predict(bgr, verbose=False, conf=conf)[0]
    counts = {}
    for c, sc in zip(r.boxes.cls, r.boxes.conf):
        name = m.names[int(c)]
        if name in WATCH and float(sc) >= MIN_CONF.get(name, conf):
            counts[name] = counts.get(name, 0) + 1
    return counts


def clip_object_counts(path, samples=3, conf=0.35):
    """max count per label over `samples` frames spread through the clip."""
    import cv2
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    counts = {}
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 0.5) / samples))
        ok, fr = cap.read()
        if not ok:
            continue
        c = count_objects(fr, conf) or {}
        for name, v in c.items():
            counts[name] = max(counts.get(name, 0), v)
    cap.release()
    return counts
