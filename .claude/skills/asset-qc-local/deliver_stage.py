#!/usr/bin/env python3
"""deliver_stage.py - delivery as a spine stage: pick, name in cut order, cut sheet, gated upload.

Replaces the per-project deliver.py / stage_delivery.py / stage_rev1.py files (2026-09-25/26).
Layout and names come from delivery_names.py (the 2026-09-14 rule); what this adds:

  * one source per shot, in this order: Creatives/_picks.json  >  the VERIFIED voice-swap result
    (voice_stage.py, _vc_state.json)  >  the newest take. A talking clip with no verified swap
    ships its own take and the cut sheet says "lay the TTS line under it"
  * stills: picks first, else the newest take; `project.delivery.never_ship` globs never ship
    (an earlier ad: v01 reference recreates still carried the reference ad's actors)
  * the cut sheet PDF: file -> shot -> the line over it -> note, one cut about every 2 s
  * upload is GATED on Creatives/_postflight.json: qa_exit 0, strict, and newer than the newest
    staged clip - never chained with `;` (Hands Down: a file went up before the verdict was in)
  * a revision ships as <slot>_vN.mp4 BESIDE the originals with its own cut sheet

    python deliver_stage.py --manifest <m.json> map   [--rev N --only T1,T2]
    python deliver_stage.py --manifest <m.json> stage [--rev N --only T1,T2]   # Creatives/_delivery[_revN]/
    python deliver_stage.py --manifest <m.json> sheet [--rev N]
    python deliver_stage.py --manifest <m.json> upload [--rev N]               # gated, then VERIFY

Manifest: shots carry `section` (int, default 1) or `hook` (letter); `project.delivery` =
{"drive_folder_id": "...", "title": "...", "notes": "...", "never_ship": ["*_v01.png"]}.
"""
import argparse, fnmatch, glob, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import delivery_names as DN  # noqa: E402


# ---------------------------------------------------------------- pure helpers (tested)
def take_no(path):
    m = re.search(r"_v(\d+)\.\w+$", path)
    return int(m.group(1)) if m else 1


def newest(paths):
    return sorted(paths, key=take_no)[-1] if paths else None


def pick_clip(tag, clips, picks, swaps):
    """(path, note). picks[tag] may be a file name or a take int; swaps = {tag: [(path, verify)]}."""
    p = picks.get(tag)
    if p:
        if isinstance(p, int):
            hit = [c for c in clips if take_no(c) == p]
        else:
            hit = [c for c in clips + [s for s, _ in swaps.get(tag, [])] if os.path.basename(c) == p]
        if hit:
            return hit[0], "pick"
    ok = [s for s, v in swaps.get(tag, []) if v == "PASS"]
    if ok:
        return sorted(ok)[-1], "voice swap verified"
    if swaps.get(tag):
        return newest(clips), "voice swap NOT verified - lay the TTS line (Elements/Audio/lines/%s.mp3) under it" % tag
    return newest(clips), ""


def plan_names(shots, rev=None, only=None):
    """delivery_names.plan over the manifest's shots; a revision keeps every slot and suffixes _vN."""
    items = []
    for s in shots:
        if s.get("local"):
            continue
        it = {"key": s["tag"]}
        if s.get("hook"):
            it["hook"] = s["hook"]
        else:
            it["sec"] = int(s.get("section") or 1)
        if rev and (not only or s["tag"] in only):
            it["take"] = rev
        items.append(it)
    names = DN.plan(items)
    if only:
        names = {k: v for k, v in names.items() if k in only}
    return names


def never_ship(path, patterns):
    return any(fnmatch.fnmatch(os.path.basename(path), pat) for pat in patterns or [])


def upload_allowed(marker, newest_clip_mtime):
    """(ok, reason) from Creatives/_postflight.json."""
    if not marker:
        return False, "no Creatives/_postflight.json - run postflight first"
    if marker.get("qa_exit") != 0:
        return False, "postflight qa_exit=%s - fix and re-run" % marker.get("qa_exit")
    if marker.get("strict") is not True:
        return False, "postflight ran lenient (or before strict was recorded) - re-run postflight"
    try:
        when = time.mktime(time.strptime(marker.get("when", "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return False, "postflight marker has no readable timestamp"
    if newest_clip_mtime and when < newest_clip_mtime:
        return False, "a staged clip is newer than the last postflight - re-run postflight"
    return True, "postflight %s strict, QA exit 0" % marker.get("when")


# ---------------------------------------------------------------- I/O
def load(mpath):
    with open(mpath, encoding="utf-8") as f:
        m = json.load(f)
    root = m["project"].get("root") or os.path.dirname(os.path.dirname(os.path.abspath(mpath)))
    return m, root


def sources(m, root, rev=None, only=None):
    P = m["project"]
    prefix = P.get("prefix", "clip")
    clips_dir = os.path.join(root, "Elements", "Clips")
    picks = {}
    pp = os.path.join(root, "Creatives", "_picks.json")
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as f:
            picks = json.load(f)
    swaps = {}
    sp = os.path.join(clips_dir, "_voicechange", "_vc_state.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            st = json.load(f)
        label = (P.get("voice_swap") or {}).get("label") or "swap"
        for key, rec in st.items():
            tag = key.replace("_redo", "")
            for path in glob.glob(os.path.join(clips_dir, "_voicechange", "%s_%s_v*_%s*.mp4" % (prefix, tag, label))):
                swaps.setdefault(tag, []).append((path, rec.get("verify")))
    names = plan_names(m["shots"], rev, only)
    rows, missing = [], []
    for s in m["shots"]:
        if s["tag"] not in names:
            continue
        stem = s.get("clipName") or "%s_%s" % (prefix, s["tag"])
        clips = sorted(glob.glob(os.path.join(clips_dir, stem + "_v*.mp4")), key=take_no)
        src, note = pick_clip(s["tag"], clips, picks, swaps)
        if not src:
            missing.append(s["tag"]); continue
        rows.append({"name": names[s["tag"]], "tag": s["tag"], "kind": s.get("kind", ""), "dur": s.get("duration"),
                     "src": src, "section": s.get("section") or s.get("hook") or 1, "line": s.get("dialogue") or "",
                     "note": "; ".join(x for x in (note, s.get("note") or "") if x)})
    return rows, missing, picks


def stage_dir(root, rev=None):
    return os.path.join(root, "Creatives", "_delivery" + ("_rev%d" % rev if rev else ""))


def cmd_map(a):
    m, root = load(a.manifest)
    rows, missing, _ = sources(m, root, a.rev, set(a.only.split(",")) if a.only else None)
    for r in rows:
        print("%-14s %-8s %-3s %s  %s" % (r["name"], r["tag"], r["kind"], os.path.basename(r["src"]), r["note"]))
    if missing:
        print("MISSING clips for:", ", ".join(missing)); sys.exit(1)


def cmd_stage(a):
    m, root = load(a.manifest)
    only = set(a.only.split(",")) if a.only else None
    rows, missing, picks = sources(m, root, a.rev, only)
    if missing:
        sys.exit("MISSING clips for: %s" % ", ".join(missing))
    D = m["project"].get("delivery") or {}
    out = stage_dir(root, a.rev)
    for sub in ("Creatives", "Elements/Clips", "Elements/Stills", "Elements/Audio"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    for r in rows:
        shutil.copyfile(r["src"], os.path.join(out, "Elements", "Clips", r["name"]))
    n_st = 0
    if not a.rev:
        stills_dir = os.path.join(root, "Elements", "Stills")
        by_stem = {}
        for p in glob.glob(os.path.join(stills_dir, "*.png")):
            if never_ship(p, D.get("never_ship")):
                continue
            by_stem.setdefault(re.sub(r"_v\d+$", "", os.path.splitext(os.path.basename(p))[0]), []).append(p)
        for stem, ps in by_stem.items():
            pk = picks.get(stem)
            src = next((p for p in ps if os.path.basename(p) == pk), None) if isinstance(pk, str) else \
                  next((p for p in ps if take_no(p) == pk), None) if isinstance(pk, int) else None
            src = src or newest(ps)
            shutil.copyfile(src, os.path.join(out, "Elements", "Stills", stem + ".png")); n_st += 1
        anchors = os.path.join(stills_dir, "_anchors")
        if os.path.isdir(anchors):
            shutil.copytree(anchors, os.path.join(out, "Elements", "Stills", "_anchors"), dirs_exist_ok=True)
        vo = (m["project"].get("vo") or {}).get("master")
        if vo and os.path.isfile(os.path.join(root, vo)):
            shutil.copyfile(os.path.join(root, vo), os.path.join(out, "Elements", "Audio", os.path.basename(vo)))
        lines = os.path.join(root, "Elements", "Audio", "lines")
        if os.path.isdir(lines):
            shutil.copytree(lines, os.path.join(out, "Elements", "Audio", "lines"), dirs_exist_ok=True)
    for pat in ("*.txt", "*.pdf", "*.docx"):
        for p in glob.glob(os.path.join(root, "Creatives", pat)):
            if "_delivery" not in p:
                shutil.copyfile(p, os.path.join(out, "Creatives", os.path.basename(p)))
    bad = DN.check_names([r["name"] for r in rows]) if not a.rev else []
    print("staged %d clip(s), %d still(s) -> %s%s" % (len(rows), n_st, out, "  NAMES: " + "; ".join(bad) if bad else ""))
    if bad:
        sys.exit(1)


def cmd_sheet(a):
    m, root = load(a.manifest)
    only = set(a.only.split(",")) if a.only else None
    rows, missing, _ = sources(m, root, a.rev, only)
    D = m["project"].get("delivery") or {}
    title = D.get("title") or m["project"].get("title") or m["project"].get("slug", "delivery")
    out = os.path.join(stage_dir(root, a.rev), "Creatives", "%s - %scut sheet.pdf" % (title, ("Rev %d " % a.rev) if a.rev else ""))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
    st = getSampleStyleSheet(); small = st["BodyText"].clone("small", fontSize=7.5, leading=9)
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    notes = D.get("notes") or ""
    if a.rev:
        notes = ("REVISION %d: the _v%d files replace the same slots. " % (a.rev, a.rev)) + notes
    notes += " Cut about every 2 s like the reference; never hold a shot across the next line." if "every 2" not in notes else ""
    body = [Paragraph("%s - %scut sheet" % (title, ("Rev %d " % a.rev) if a.rev else ""), st["Title"]), Paragraph(notes, small), Spacer(1, 8)]
    data = [["Clip", "Section", "Shot", "Kind", "Dur", "Line over it", "Note"]]
    for r in rows:
        data.append([r["name"], str(r["section"]), r["tag"], r["kind"], "%ss" % r["dur"] if r["dur"] else "",
                     Paragraph(r["line"] or "cutaway", small), Paragraph(r["note"], small)])
    t = Table(data, colWidths=[64, 44, 56, 30, 30, 320, 240], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3b2d")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.grey)]))
    body.append(t); doc.build(body)
    print("cut sheet:", out)


def cmd_upload(a):
    m, root = load(a.manifest)
    D = m["project"].get("delivery") or {}
    drive = D.get("drive_folder_id") or m["project"].get("drive_folder_id")
    if not drive:
        sys.exit("no drive_folder_id in project.delivery / project")
    out = stage_dir(root, a.rev)
    clips = glob.glob(os.path.join(out, "Elements", "Clips", "*.mp4"))
    if not clips:
        sys.exit("nothing staged at %s - run `stage` first" % out)
    mp = os.path.join(root, "Creatives", "_postflight.json")
    marker = json.load(open(mp, encoding="utf-8")) if os.path.exists(mp) else None
    ok, why = upload_allowed(marker, max(os.path.getmtime(c) for c in clips))
    print("gate:", why)
    if not ok:
        sys.exit(2)
    if a.dry_run:
        print("DRY RUN - nothing uploaded."); return
    r = subprocess.run(["rclone", "copy", out, "gdrive:", "--drive-root-folder-id", drive, "--exclude", "_delivery*/**"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit("rclone failed: " + (r.stderr or "")[-300:])
    bad, stats = DN.verify_drive(drive)
    print(json.dumps(stats))
    print("VERIFY: %s" % ("PASS" if not bad else "FAIL - " + "; ".join(bad[:6])))
    sys.exit(0 if not bad else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("cmd", choices=("map", "stage", "sheet", "upload"))
    ap.add_argument("--rev", type=int, default=None)
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    {"map": cmd_map, "stage": cmd_stage, "sheet": cmd_sheet, "upload": cmd_upload}[a.cmd](a)


if __name__ == "__main__":
    main()
