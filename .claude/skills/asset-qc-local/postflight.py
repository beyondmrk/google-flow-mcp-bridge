#!/usr/bin/env python3
"""END-OF-GEN gate for every AI-gen skill. The user's checklist, 2026-09-03:

  1. Strict QA — and does the batch match WHAT WAS ASKED? Anything that doesn't is a FLAG.
  2. Are the brand guidelines followed?
  3. Is this the correct packaging?
  4. Is there already a Drive folder for this project to upload into?
  5. Did you actually LOOK? (QC regime, 2026-09-21 - the eye pass is recorded, not assumed)

This script runs the machine half (qa_engine with the client's profile + a Drive lookup) and then
prints the BRIEF-COMPLIANCE TABLE the model has to fill in by hand — one row per shot: what the
script/manifest asked for, what the clip delivered (Whisper heard / identity / text / frame QA),
and a MATCH / FLAG call. Brand + packaging rows are read from clients.json and must be answered
explicitly; "not checked" is a FLAG.

    python postflight.py --client flow --manifest "<Creatives/<vertical>_<slug>.json>" [--drive-folder-id <id>]
    python postflight.py --client flow --dir "<root>/<PROJECT>"

Exit = qa_engine's exit (1 when any FAIL), or 2 when Drive is unresolved or no eye pass was
recorded. Writes
<root>/Creatives/_postflight.json (QA summary + the table skeleton + brand/packaging rows).
"""
import argparse, datetime, json, os, shutil, subprocess, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qa_engine  # noqa: E402


def rclone_bin():
    p = shutil.which("rclone")
    if p:
        return p
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    for root, _d, files in os.walk(base) if os.path.isdir(base) else []:
        if "rclone.exe" in files:
            return os.path.join(root, "rclone.exe")
    return "rclone"


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir"); src.add_argument("--manifest")
    ap.add_argument("--drive-folder-id", default=None)
    ap.add_argument("--drive-parent-id", default=None)
    # Strict is the DEFAULT on a client run (2026-09-26): four client postflights ran
    # lenient and passed with WARN on 98% of items, so garbled label text and off-spec resolution
    # shipped inside the noise. --lenient is the explicit opt-out and is recorded in the marker.
    ap.add_argument("--strict", dest="strict", action="store_true", default=True,
                    help="default: promote the profile's strict_promote codes to FAIL")
    ap.add_argument("--lenient", dest="strict", action="store_false",
                    help="opt out of strict (recorded in _postflight.json; never for a delivery)")
    ap.add_argument("--layers", default=None, help="passed to qa_engine (default: all)")
    ap.add_argument("--only", default="")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="profile override passed to qa_engine, e.g. --set clip_res=[480,854] for a "
                         "480p seedance build")
    ap.add_argument("--no-drive", action="store_true")
    ap.add_argument("--eyes", action="append", default=[], metavar="WHAT",
                    help="what I actually LOOKED at for this batch - contact sheets, crv grids, "
                         "QA strips, packaging frames (repeatable). Inside a client run the eye "
                         "pass is mandatory: nothing recorded = gate not passed (exit 2). "
                         "See SKILL.md 'QC regime'.")
    return ap


def main():
    a = build_parser().parse_args()
    if not a.strict:
        print("LENIENT: strict QA is OFF for this run - WARN codes will not block. Not a delivery gate."); print()

    with open(os.path.join(HERE, "clients.json"), encoding="utf-8") as f:
        client = json.load(f)[a.client]
    profile = client["qa_profile"]

    # locate the project root up front: the brand check runs BEFORE the QA engine spends time
    if a.dir:
        _, root0 = qa_engine.items_from_dir(a.dir)
    else:
        _, root0 = qa_engine.items_from_flow(a.manifest)
    # The brand is declared, never inferred: --client must match what the start-of-gen gate
    # wrote into Creatives/_preflight.json (the project's client marker). A mismatch = exit 2.
    for cand in (os.path.join(root0, "Creatives", "_preflight.json"), os.path.join(root0, "_preflight.json"),
                 os.path.join(os.path.dirname(root0), "Creatives", "_preflight.json")):
        if os.path.isfile(cand):
            try:
                marked = (json.load(open(cand, encoding="utf-8")).get("client") or "").strip().lower()
            except Exception:
                marked = ""
            if marked and marked != a.client.strip().lower():
                print("CLIENT MISMATCH: --client %s but %s says %s - refusing. Fix whichever is wrong."
                      % (a.client, cand, marked)); sys.exit(2)
            break

    # ---- 1. strict QA (machine half)
    cmd = [sys.executable, os.path.join(HERE, "qa_engine.py"), "--profile", profile]
    if a.dir:
        cmd += ["--dir", a.dir]
    else:
        cmd += ["--manifest", a.manifest]
    if a.strict:
        cmd.append("--strict")
    if a.layers:
        cmd += ["--layers", a.layers]
    if a.only:
        cmd += ["--only", a.only]
    for kv in a.set:
        cmd += ["--set", kv]
    print("running:", " ".join(cmd)); print()
    qa_rc = subprocess.call(cmd)

    # locate the report the engine wrote
    if a.dir:
        items, root = qa_engine.items_from_dir(a.dir)
    else:
        items, root = qa_engine.items_from_flow(a.manifest)
    rp = os.path.join(root, "_qa", "qa_report.json")
    report = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {"rows": [], "summary": {}}
    rows = {r["id"]: r for r in report["rows"]}

    # ---- brief-compliance table skeleton
    print("\n" + "=" * 100)
    print("1. BRIEF COMPLIANCE — fill every row: MATCH or FLAG(reason). Asked = script/manifest; Delivered = what QA saw.")
    print("=" * 100)
    print("%-26s %-6s %-42s %-42s %s" % ("shot / take", "verd", "asked", "delivered", "call"))
    table = []
    for it in items:
        if it["kind"] != "clip":
            continue
        r = rows.get(it["id"], {})
        m = r.get("metrics", {})
        asked = (it["expect"].get("line") or it["expect"].get("shot_kind") or "-")
        if it.get("missing"):
            delivered = "NOT GENERATED"
        else:
            bits = []
            if "heard" in m:
                bits.append("heard: " + m["heard"][:60])
            if "identity_cos" in m and m["identity_cos"] is not None:
                bits.append("id %.2f" % m["identity_cos"])
            if m.get("text"):
                bits.append("text: " + "; ".join(t.split(" (")[0] for t in m["text"][:3]))
            fl = [f["code"] for f in r.get("findings", [])]
            if fl:
                bits.append("QA: " + ",".join(fl[:5]))
            delivered = " | ".join(bits) or "clean"
        call = "FLAG(missing)" if it.get("missing") else ("FLAG(QA FAIL)" if r.get("verdict") == "FAIL" else "")
        table.append({"id": it["id"], "group": it["group"], "verdict": r.get("verdict"), "asked": asked, "delivered": delivered, "call": call})
        print("%-26s %-6s %-42s %-42s %s" % (it["id"][-26:], r.get("verdict", "-"), str(asked)[:42], delivered[:42], call))
    print("(every blank 'call' is yours to fill after the eye pass: MATCH, or FLAG with the reason)")

    # ---- 2 + 3. brand + packaging
    print("\n" + "=" * 100)
    print("2. BRAND GUIDELINES  —  read %s and answer each rule for THIS batch" % client.get("brand_doc", "-"))
    for rule in client.get("brand_rules", []):
        print("   [ ] " + rule)
    print("\n3. PACKAGING  —  %s" % client.get("packaging_rule", "-"))
    pr = client.get("packaging_ref")
    if pr and os.path.exists(pr):
        refs = sorted(os.listdir(pr))[:8] if os.path.isdir(pr) else [os.path.basename(pr)]
        print("   reference on disk: %s  ->  %s" % (pr, ", ".join(refs)))
    elif pr:
        print("   reference path missing on disk: %s  (FLAG until found)" % pr)
    print("   [ ] every product/packaging frame compared against the reference by eye (Read) — not assumed")

    # ---- 4. Drive
    drive = {"folder_id": a.drive_folder_id, "status": "skipped"}
    if not a.no_drive:
        rc_ = rclone_bin()
        name = os.path.basename(os.path.normpath(root))
        fid = a.drive_folder_id
        parent = a.drive_parent_id or client.get("drive_root_id")
        if not fid and parent:
            r = subprocess.run([rc_, "lsjson", "gdrive:", "--drive-root-folder-id", parent, "--dirs-only"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                dirs = json.loads(r.stdout or "[]")
            except Exception:
                dirs = []
            hit = next((d for d in dirs if d["Name"].strip().lower() == name.strip().lower()), None)
            fid = hit["ID"] if hit else None
        print("\n" + "=" * 100)
        if fid:
            r = subprocess.run([rc_, "lsjson", "gdrive:", "--drive-root-folder-id", fid, "-R"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                ls = json.loads(r.stdout or "[]")
            except Exception:
                ls = []
            n_clips = sum(1 for x in ls if x["Path"].startswith("Elements/Clips/") and not x["IsDir"])
            n_stills = sum(1 for x in ls if x["Path"].startswith("Elements/Stills/") and not x["IsDir"])
            print("4. DRIVE  —  existing project folder %s (%s): %d clips, %d stills already there. Upload goes HERE, not a new folder."
                  % (fid, name, n_clips, n_stills))
            drive = {"folder_id": fid, "status": "found", "clips": n_clips, "stills": n_stills}
        else:
            print("4. DRIVE  —  no existing folder for %r (parent %s). FLAG: ask the user which Drive folder, or run preflight.py to create it."
                  % (name, parent or "-"))
            drive = {"folder_id": None, "status": "unresolved"}

    # ---- 5. eye pass (QC regime: strict QA inside a client run, 2026-09-21)
    eyes = [{"what": e, "exists": os.path.exists(e)} for e in a.eyes]
    eyes_ok = any(e["exists"] for e in eyes)
    print("\n" + "=" * 100)
    if eyes_ok:
        print("5. EYE PASS  —  recorded:")
        for e in eyes:
            print("   %s %s" % ("[x]" if e["exists"] else "[ ] not on disk", e["what"]))
    else:
        print("5. EYE PASS  —  NOT RECORDED.  Automated-clean is not deliverable: read every flagged "
              "strip, a contact sheet per still batch and a grid per clip take, plus the packaging "
              "and locked-entity frames, then re-run with --eyes <path> (repeatable).")

    out = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "client": a.client, "root": root,
           "qa_exit": qa_rc, "strict": a.strict, "qa_summary": report.get("summary"), "table": table,
           "brand_rules": client.get("brand_rules", []), "packaging_rule": client.get("packaging_rule"), "drive": drive,
           "eye_pass": eyes}
    os.makedirs(os.path.join(root, "Creatives"), exist_ok=True)
    with open(os.path.join(root, "Creatives", "_postflight.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print("\nwritten: %s" % os.path.join(root, "Creatives", "_postflight.json"))
    print("END-OF-GEN GATE: QA %s · brand/packaging answered by hand · eye pass %s · Drive %s"
          % ("FAIL present" if qa_rc else "clean", "recorded" if eyes_ok else "NOT RECORDED", drive["status"]))
    sys.exit(qa_rc or (2 if (drive["status"] == "unresolved" or not eyes_ok) else 0))


if __name__ == "__main__":
    main()
