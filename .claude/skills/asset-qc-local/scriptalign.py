#!/usr/bin/env python3
"""Does the shot table still say what the script says? (the user, 2026-09-21)

Run at Gate 1, BEFORE motion spends anything. The manifest is built by hand from the
script and drifts the moment a line is tightened, split, re-ordered or quietly dropped - and
nothing downstream catches it, because Whisper only proves a clip said what the MANIFEST asked,
never what the SCRIPT asked. This compares the two, sentence by sentence, in order.

    python scriptalign.py --client flow --manifest "<Creatives/<vertical>_<slug>.json>" --script "<Creatives/script.md>"
    python scriptalign.py --client flow --dir "<project>"                               --script "<...>"

Findings: DROPPED (script line no shot covers), INVENTED (shot copy not in the script),
REORDERED (covered, but out of script order), PARAPHRASED (covered, not in the script words).
Exit 1 on any DROPPED / INVENTED / REORDERED, 0 when only PARAPHRASED (a tightened line is a
judgement call - it is reported, and the editor decides). Writes <root>/Creatives/_align.json.
"""
import argparse, difflib, json, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qa_engine  # noqa: E402

NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
       "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
LABEL = re.compile(r"^\s*((vo|v\.o|narrator|voice ?over|on ?screen|text|sfx|super|cta|th|br|"
                   r"s\d{1,2}|shot \d+|clip \d+|line \d+|\d{1,2})\s*[:.\-—)]\s*)+", re.I)
SKIP = re.compile(r"^\s*([#>|*\-=_`]|\[|\!)")
CLOSE = 0.62          # close enough to call a paraphrase rather than a different line


def norm(s):
    s = (s or "").lower().replace("’", "'").replace("—", " ")
    s = re.sub(r"[\"'(){}\[\]]", " ", s)
    s = re.sub(r"[$,]", "", s)
    words = [NUM.get(w, w) for w in re.split(r"[^a-z0-9%.]+", s) if w]
    return " ".join(w.rstrip(".") for w in words).strip()


def sentences(text):
    out = []
    for raw in text.splitlines():
        line = LABEL.sub("", raw.strip())
        if not line or SKIP.match(raw) or len(line.split()) < 3:
            continue
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if len(p.split()) >= 3]
        # A line made only of short sentences ("Seven months. Nothing.") is kept whole, the same
        # way load_shots keeps it - dropping it here made the verbatim shot read INVENTED.
        out += parts or [line]
    return out


def load_shots(a):
    if a.dir:
        items, root = qa_engine.items_from_dir(a.dir)
    else:
        items, root = qa_engine.items_from_flow(a.manifest)
    lines = []
    for it in items:
        if it.get("kind") != "clip":
            continue
        spoken = (it.get("expect") or {}).get("line")
        if spoken and len(str(spoken).split()) >= 3:
            # A shot line is split into sentences exactly as the script is, so a two-sentence
            # shot does not match one script sentence and leave its sibling flagged DROPPED.
            for part in sentences(str(spoken)) or [str(spoken)]:
                lines.append((it["id"], part))
    # Local VO narration shots (the cut map under b-roll runs) are not clips, so the QA loader skips
    # them since d9e37c3 - read their lines straight from the manifest or every script line is DROPPED.
    if getattr(a, "manifest", None) and not a.dir:
        with open(a.manifest, encoding="utf-8") as f:
            for s in json.load(f).get("shots", []):
                if s.get("local") and s.get("kind") == "VO" and len(str(s.get("dialogue") or "").split()) >= 3:
                    lines += [(s["tag"], part) for part in sentences(s["dialogue"]) or [s["dialogue"]]]
    return lines, root


def best(target, pool):
    """(index, ratio) of the closest entry in pool [(index, (id, text))]."""
    best_i, best_r = -1, 0.0
    for i, (_, txt) in pool:
        r = difflib.SequenceMatcher(None, target, norm(txt)).ratio()
        if r > best_r:
            best_i, best_r = i, r
    return best_i, best_r


def align(script_lines, shot_lines):
    findings, pairs = [], []
    used = set()
    for si, s in enumerate(script_lines):
        ns = norm(s)
        i, r = best(ns, [(i, v) for i, v in enumerate(shot_lines) if i not in used])
        if r >= CLOSE:
            used.add(i)
            pairs.append((si, i, r))
            if norm(shot_lines[i][1]) != ns:          # aligned, but not in the script's words
                findings.append(("PARAPHRASED", shot_lines[i][0],
                                 "script: %s | shot: %s" % (s.strip(), shot_lines[i][1].strip())))
        else:
            findings.append(("DROPPED", "-", s.strip()))
    for i, (sid, txt) in enumerate(shot_lines):
        if i not in used:
            findings.append(("INVENTED", sid, txt.strip()))
    order = [i for _, i, _ in pairs]
    for a_, b_ in zip(order, order[1:]):
        if b_ < a_:
            findings.append(("REORDERED", shot_lines[b_][0],
                             "%s sits after %s in the shot table but before it in the script"
                             % (shot_lines[b_][0], shot_lines[a_][0])))
    return findings, pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest"); src.add_argument("--dir")
    ap.add_argument("--script", required=True, help="the script / brief the shots were built from")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    with open(a.script, encoding="utf-8", errors="replace") as f:
        script_lines = sentences(f.read())
    shot_lines, root = load_shots(a)
    findings, pairs = align(script_lines, shot_lines)

    hard = [f for f in findings if f[0] != "PARAPHRASED"]
    out = {"script": os.path.abspath(a.script), "script_lines": len(script_lines),
           "shot_lines": len(shot_lines), "aligned": len(pairs),
           "findings": [{"code": c, "id": i, "detail": d} for c, i, d in findings]}
    os.makedirs(os.path.join(root, "Creatives"), exist_ok=True)
    with open(os.path.join(root, "Creatives", "_align.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)

    if a.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        print("SCRIPT ALIGNMENT  ·  %d script line(s) vs %d shot line(s)  ·  %d aligned"
              % (len(script_lines), len(shot_lines), len(pairs)))
        print("-" * 78)
        for c, i, d in findings:
            print("%-12s %-14s %s" % (c, i, d[:100]))
        if not findings:
            print("clean - every script line is covered, in order, in the words the script used")
        print("-" * 78)
        print("written: %s" % os.path.join(root, "Creatives", "_align.json"))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
