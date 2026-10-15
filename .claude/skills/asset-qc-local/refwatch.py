#!/usr/bin/env python3
"""The inspo / reference is WATCHED before anything starts (the user, 2026-09-21).

A reference link in a brief is a spec, not decoration - the 2026-09-18 song ad burned
304 credits in the wrong treatment because the reference was never opened. A video reference is
not "opened", it is WATCHED: run it through claude-real-video (`crv <file|url> --grid`) or play
it, then write down the beats it actually uses. This records that and proves it happened.

    python refwatch.py --scan   --script "<brief.md>"                  # what refs the brief names
    python refwatch.py --record "<project>" --ref "<url|path>" --kind video \\
                       --notes "beats: 0-2s hook on the face, 2-6s ... ; what we take: ..."
    python refwatch.py --check  "<project>" --script "<brief.md>"      # what preflight calls

Exit 1 when a reference the brief names has no watched record with real notes.
Record lives in <project>/Creatives/_references.json.
"""
import argparse, datetime, json, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
VID = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]+\.(?:mp4|mov|m4v|webm|mkv)|[^\s\"'<>|]+\.(?:mp4|mov|m4v|webm|mkv)", re.I)
VIDEO_HOSTS = ("youtube.", "youtu.be", "vimeo.", "tiktok.", "facebook.com/watch", "fb.watch",
               "instagram.com/reel", "drive.google.com/file", "frame.io", "loom.com")
NOTES_FLOOR = 40          # a real note, not "watched"


DRIVE_ID = re.compile(r"(?:drive|docs)\.google\.com/(?:file/d/|folders/|document/d/|open\?id=)"
                      r"([A-Za-z0-9_-]{10,})", re.I)


def ref_key(ref):
    """Identity of a reference for matching a record against the brief.

    A Drive link is identified by its FILE ID, not by the URL string: the Drive UI appends
    ?usp=drive_link / ?usp=sharing, so the brief's copy and a hand-cleaned record are the
    same reference spelled two ways. Everything else drops the query and fragment and
    lowercases. Without this the gate flagged a reference that HAD been watched
    (2026-09-21, an earlier build).
    """
    r = (ref or "").strip()
    m = DRIVE_ID.search(r)
    if m:
        return "drive:" + m.group(1)
    r = r.split("#", 1)[0].split("?", 1)[0]
    return r.rstrip("/").lower()


def refs_in(text):
    out = []
    for m in URL.findall(text):
        r = m.rstrip(".,);")
        kind = "video" if any(h in r.lower() for h in VIDEO_HOSTS) else "link"
        out.append({"ref": r, "kind": kind})
    for m in VID.findall(text):
        out.append({"ref": m.strip(), "kind": "video"})
    seen, uniq = set(), []
    for r in out:
        if r["ref"] not in seen:
            seen.add(r["ref"]); uniq.append(r)
    return uniq


def path_of(project):
    return os.path.join(project, "Creatives", "_references.json")


def load(project):
    p = path_of(project)
    if not os.path.isfile(p):
        return {"references": []}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def record(project, ref, kind, notes):
    if len((notes or "").strip()) < NOTES_FLOOR:
        print("notes are too thin (%d chars) - write what the reference actually DOES: its beats, "
              "its cut rhythm, what we take and what we leave. A reference nobody can describe was "
              "not watched." % len((notes or "").strip()))
        return 1
    d = load(project)
    d["references"] = [r for r in d["references"] if r.get("ref") != ref]
    d["references"].append({"ref": ref, "kind": kind, "watched": True, "notes": notes.strip(),
                            "when": datetime.datetime.now().isoformat(timespec="seconds")})
    os.makedirs(os.path.dirname(path_of(project)), exist_ok=True)
    with open(path_of(project), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    print("recorded: %s (%s)" % (ref, kind))
    return 0


def check(project, script):
    """-> (ok, message). Used by preflight row 7."""
    with open(script, encoding="utf-8", errors="replace") as f:
        found = refs_in(f.read())
    if not found:
        return True, "the brief names no reference - nothing to watch"
    have = {ref_key(r.get("ref")): r for r in load(project).get("references", [])}
    bad = []
    for r in found:
        rec = have.get(ref_key(r["ref"]))
        if not rec or not rec.get("watched"):
            bad.append("%s %s - NOT WATCHED" % (r["kind"].upper(), r["ref"]))
        elif len((rec.get("notes") or "").strip()) < NOTES_FLOOR:
            bad.append("%s %s - watched but the notes say nothing" % (r["kind"].upper(), r["ref"]))
    if bad:
        return False, ("watch the reference BEFORE the shotlist, then refwatch.py --record:\n"
                       + "\n".join(bad))
    return True, "%d reference(s) watched and noted" % len(found)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--record", metavar="PROJECT")
    ap.add_argument("--check", metavar="PROJECT")
    ap.add_argument("--script")
    ap.add_argument("--ref"); ap.add_argument("--kind", default="video", choices=("video", "link"))
    ap.add_argument("--notes", default="")
    a = ap.parse_args()

    if a.scan:
        if not a.script:
            ap.error("--scan needs --script")
        with open(a.script, encoding="utf-8", errors="replace") as f:
            found = refs_in(f.read())
        for r in found:
            print("%-6s %s" % (r["kind"], r["ref"]))
        print("%d reference(s). Watch every video one before the shotlist." % len(found))
        return 0
    if a.record:
        if not a.ref:
            ap.error("--record needs --ref")
        return record(a.record, a.ref, a.kind, a.notes)
    if a.check:
        if not a.script:
            ap.error("--check needs --script")
        ok, msg = check(a.check, a.script)
        print(("ok   " if ok else "FLAG ") + msg)
        return 0 if ok else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
