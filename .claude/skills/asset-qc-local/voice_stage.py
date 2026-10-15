#!/usr/bin/env python3
"""voice_stage.py - voice_change on every talking clip, as a spine stage: submit once, poll, verify.

Lifted from earlier per-project voice_swap.py files (three ads, 2026-09-25)
so no project writes one again. What it keeps from them, and why:
  * never `--wait`: one probe hung 47 minutes before its voice_change_merge job even existed.
    Upload once, submit once (state file), then poll the job list for the merge job whose
    input_video is the clip's upload
  * the result lands BESIDE the original (`_voicechange/<clip>_<label>.mp4`), a redo as `_v02`
  * verify against the original: same duration, audio present, same words (Whisper, fuzzy so
    "hot spots"/"hotspots" passes and "ago"->"without" fails), first/last word within 0.10 s
  * NEW: pitch against the VO master. voice_change matched the timbre but kept the source
    clip's pitch on an earlier ad (about 2.2 semitones high) and nobody's check caught it

    python voice_stage.py --manifest <m.json> run [--only T1,T2] [--redo TAG] [--dry-run]
    python voice_stage.py --manifest <m.json> status

Manifest contract (project.voice_swap): {"voice_id": "<preset id>", "label": "grady"}.
Targets = the newest take of every speaking, non-local shot. `project.vo.master`, when present,
is the pitch reference. Rule after a CHECK: redo once (`--redo TAG`); if it repeats, the delivery
lays the TTS line (Elements/Audio/lines/<tag>.mp3) under the shot instead.
"""
import argparse, difflib, glob, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hf_call  # noqa: E402

DONE_BAD = ("failed", "nsfw", "canceled", "cancelled", "error")
WORDS_OK = 0.92          # difflib ratio on normalised, space-free text
PITCH_OK_SEMITONES = 1.5


# ---------------------------------------------------------------- pure helpers (tested)
def targets(m, root, only=None):
    """{tag: newest clip path} for every speaking, non-local shot (or the `only` tags)."""
    P = m["project"]
    out = {}
    for s in m["shots"]:
        if not s.get("dialogue") or s.get("local"):
            continue
        if only and s["tag"] not in only:
            continue
        stem = s.get("clipName") or "%s_%s" % (P.get("prefix", "clip"), s["tag"])
        takes = sorted(glob.glob(os.path.join(root, "Elements", "Clips", stem + "_v*.mp4")),
                       key=lambda p: int(re.search(r"_v(\d+)\.mp4$", p).group(1)))
        if takes:
            out[s["tag"]] = takes[-1]
    return out


def result_path(clip, label, redo=False):
    d = os.path.join(os.path.dirname(clip), "_voicechange")
    stem = os.path.splitext(os.path.basename(clip))[0]
    return os.path.join(d, "%s_%s%s.mp4" % (stem, label, "_v02" if redo else ""))


def norm_text(words):
    return re.sub(r"[^a-z0-9']", "", " ".join(words).lower())


def words_match(a, b):
    """True when two transcripts say the same thing (ASR spelling noise tolerated)."""
    return difflib.SequenceMatcher(None, norm_text(a), norm_text(b), autojunk=False).ratio() >= WORDS_OK


def semitones(f_a, f_b):
    import math
    return 12 * math.log2(f_a / f_b) if f_a and f_b else 0.0


def median_f0(samples, sr, lo=70, hi=400):
    """Median fundamental of the voiced frames, by autocorrelation (numpy only)."""
    import numpy as np
    x = np.asarray(samples, dtype=float)
    n = int(sr * 0.04); hop = n // 2
    if len(x) < n:
        return 0.0
    thresh = 0.3 * (x ** 2).mean()
    f0s = []
    for i in range(0, len(x) - n, hop):
        fr = x[i:i + n]
        if (fr ** 2).mean() < thresh:
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[n - 1:]
        lag_lo, lag_hi = int(sr / hi), int(sr / lo)
        if lag_hi >= len(ac):
            continue
        seg = ac[lag_lo:lag_hi]
        k = int(seg.argmax()) + lag_lo
        if ac[k] > 0.5 * ac[0]:
            f0s.append(sr / k)
    return float(np.median(f0s)) if f0s else 0.0


def verdict(d_src, d_new, has_audio, w_src, w_new, f_src, f_new, l_src, l_new, st_off=None):
    """(PASS|CHECK, reasons)."""
    why = []
    if abs(d_src - d_new) >= 0.1:
        why.append("duration %.2f/%.2f" % (d_src, d_new))
    if not has_audio:
        why.append("no audio stream")
    if not words_match(w_src, w_new):
        why.append("words differ")
    if abs(f_src - f_new) >= 0.1 or abs(l_src - l_new) >= 0.1:
        why.append("timing drift first %.2f/%.2f last %.2f/%.2f" % (f_src, f_new, l_src, l_new))
    if st_off is not None and abs(st_off) > PITCH_OK_SEMITONES:
        why.append("pitch %+.1f semitones vs the VO master" % st_off)
    return ("PASS" if not why else "CHECK"), why


# ---------------------------------------------------------------- I/O
def hf_bin():
    import shutil
    for c in (shutil.which("higgsfield"), shutil.which("higgsfield.cmd"), os.path.expandvars(r"%APPDATA%\npm\higgsfield.cmd")):
        if c and os.path.exists(c):
            return c
    return "higgsfield"


def hf(args, timeout=300):
    r = subprocess.run([hf_bin()] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout or ""


def jitems(raw):
    try:
        return json.loads(raw[raw.index("["):])
    except Exception:
        return []


def dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def has_audio(p):
    return bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip())


def pcm(p, sr=16000, seconds=None):
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", p] + (["-t", str(seconds)] if seconds else []) + \
          ["-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(float) / 32768.0, sr


_MODEL = {}


def transcribe(p):
    if "m" not in _MODEL:
        import whisper
        _MODEL["m"] = whisper.load_model("small")
    r = _MODEL["m"].transcribe(p, language="en", word_timestamps=True)
    w = [x for s in r["segments"] for x in s.get("words", [])]
    return [x["word"].strip() for x in w], (w[0]["start"] if w else -1), (w[-1]["end"] if w else -1)


def load(mpath):
    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)
    root = m["project"].get("root") or os.path.dirname(os.path.dirname(os.path.abspath(mpath)))
    vs = m["project"].get("voice_swap") or {}
    return m, root, vs


def _client_marker(mpath):
    """The client key from Creatives/_preflight.json under the manifest's project root, or None."""
    try:
        with open(mpath, encoding="utf-8") as f:
            proj = json.load(f).get("project") or {}
        root = proj.get("root") or os.path.dirname(os.path.dirname(os.path.abspath(mpath)))
        with open(os.path.join(root, "Creatives", "_preflight.json"), encoding="utf-8") as f:
            return (json.load(f).get("client") or "").strip().lower() or None
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def cmd_run(a):
    m, root, vs = load(a.manifest)
    if not vs.get("voice_id"):
        sys.exit("project.voice_swap.voice_id missing")
    label = vs.get("label") or "swap"
    only = set(a.only.split(",")) if a.only else None
    if a.redo:
        only = {a.redo}
    tg = targets(m, root, only)
    if not tg:
        sys.exit("no talking clips on disk to swap")
    out_dir = os.path.join(root, "Elements", "Clips", "_voicechange")
    os.makedirs(out_dir, exist_ok=True)
    sp = os.path.join(out_dir, "_vc_state.json")
    state = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}

    def save():
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)

    def res(tag):
        return result_path(tg[tag], label, redo=bool(a.redo))

    key = lambda tag: tag + ("_redo" if a.redo else "")  # noqa: E731
    todo = [t for t in tg if not os.path.exists(res(t))]
    print("voice swap -> %s: %d clip(s) %s" % (label, len(todo), ", ".join(todo[:8]) + ("..." if len(todo) > 8 else "")))
    if a.dry_run:
        print("DRY RUN - nothing uploaded or submitted."); return
    # 1) upload once
    for t in todo:
        if state.get(key(t), {}).get("upload"):
            continue
        up = None
        for i in range(4):
            raw = hf(["upload", "create", tg[t], "--json"])
            try:
                up = json.loads(raw[raw.index("{"):]); break
            except Exception:
                print(t, "upload retry", i + 1); time.sleep(20)
        if not up:
            print(t, "UPLOAD FAILED - skipped"); continue
        state[key(t)] = {"upload": up["id"], "clip": os.path.basename(tg[t])}; save()
    # 2) submit once, detached - never --wait
    for t in todo:
        st = state.get(key(t))
        if not st or st.get("submitted"):
            continue
        obj = json.dumps({"id": st["upload"], "type": "video_input"})
        subprocess.Popen([hf_bin(), "generate", "create", "voice_change", "--input_video", obj, "--voice_id", vs["voice_id"],
                          "--voice_type", "preset", "--json"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        st["submitted"] = time.time(); save(); print(t, "submitted")
    # 3) poll
    deadline = time.time() + 150 * 60
    while time.time() < deadline:
        pending = [t for t in todo if key(t) in state and not os.path.exists(res(t)) and not state[key(t)].get("failed")]
        if not pending:
            break
        jobs = jitems(hf(["generate", "list", "--video", "--size", "100", "--json"], 180))
        for t in pending:
            st = state[key(t)]
            j = None
            if st.get("job"):
                try:
                    j = json.loads(hf(["generate", "get", st["job"], "--json"], 120))
                except Exception:
                    j = None
            if j is None:
                mm = [x for x in jobs if x.get("job_type") == "voice_change_merge"
                      and ((x.get("params") or {}).get("input_video") or {}).get("id") == st["upload"]]
                if not mm:
                    continue
                j = mm[0]; st["job"] = j["id"]; save(); print(t, "job", j["id"], j.get("status"))
            s = str(j.get("status", "")).lower()
            if s == "completed" and j.get("result_url"):
                ok, err = hf_call.download(j["result_url"], res(t))
                print(t, "downloaded" if ok else "DOWNLOAD FAILED " + err)
            elif s in DONE_BAD:
                st["failed"] = s; save(); print(t, "FAILED", s)
        time.sleep(60)
    # 4) verify
    ref_f0 = None
    vo = (m["project"].get("vo") or {}).get("master")
    if vo and os.path.isfile(os.path.join(root, vo)):
        x, sr = pcm(os.path.join(root, vo), seconds=60); ref_f0 = median_f0(x, sr)
    passed = 0
    for t in tg:
        new = res(t)
        if not os.path.exists(new):
            print(t, "NO RESULT", state.get(key(t), {}).get("failed") or "timed out"); continue
        src = tg[t]
        w0, f0, l0 = transcribe(src); w1, f1, l1 = transcribe(new)
        st_off = None
        if ref_f0:
            x, sr = pcm(new); st_off = semitones(median_f0(x, sr), ref_f0)
        v, why = verdict(dur(src), dur(new), has_audio(new), w0, w1, f0, f1, l0, l1, st_off)
        passed += v == "PASS"
        state.setdefault(key(t), {})["verify"] = v; state[key(t)]["why"] = why; save()
        print("%s VERIFY %s %s" % (t, v, "; ".join(why)))
        if "words differ" in why:
            print("   orig:", " ".join(w0)); print("   new: ", " ".join(w1))
    print("VOICE SWAP DONE: %d/%d verified%s" % (passed, len(tg), "" if passed == len(tg) else
          " - redo a CHECK once (--redo TAG); if it repeats, the delivery lays the TTS line under the shot"))


def cmd_status(a):
    m, root, vs = load(a.manifest)
    label = vs.get("label") or "swap"
    tg = targets(m, root)
    sp = os.path.join(root, "Elements", "Clips", "_voicechange", "_vc_state.json")
    state = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
    for t, c in tg.items():
        r = result_path(c, label)
        print("%-10s %-8s %s" % (t, state.get(t, {}).get("verify") or ("landed" if os.path.exists(r) else "--"), os.path.basename(r)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("cmd", choices=("run", "status"))
    ap.add_argument("--only", default="")
    ap.add_argument("--redo", default=None, help="a second voice_change of one clip, landing as _v02 beside the first")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--client", default=None, help="client key, when Creatives/_preflight.json has none (login router)")
    a = ap.parse_args(argv)
    # HF_CLIENT: route every Higgsfield call to this client's login (optional login router)
    client = _client_marker(a.manifest) or (a.client or "").strip().lower() or None
    if client:
        hf_call.pin_hf_client(client)
    {"run": cmd_run, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
