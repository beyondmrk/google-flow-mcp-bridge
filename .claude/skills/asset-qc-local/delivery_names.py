#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delivered-clip naming and the client Drive layout - shared by EVERY generation skill.

The delivery rule, 2026-09-14 (after two earlier client deliveries):

  <Drive project folder>/
    Creatives/          script/brief, edit notes PDF, one cut-sheet PDF per hook
    Elements/
      Stills/           _anchors/, _refs/, the generated stills
      Clips/            ONE FLAT FOLDER, videos only, names sort in CUT ORDER
      Audio/            VO / music tracks (optional)

  Clip names (generated and free/recycled footage are named alike - never shot ids, never SRC_):
    HA_1.mp4 HA_2.mp4   hook openers: H + hook letter, underscore, order inside the hook
    HB_1.mp4 ...
    S01_01.mp4          body: S + script section (2 digits), underscore, position in the section
    S03_02_v1.mp4       optional take suffix when a slot ships more than one take
  A creative with no hook variants uses S names only. Alphabetical order = the edit: a hook's
  openers, then S01_01 onward. The cut sheet per hook maps each file back to its production id.

The old layouts are withdrawn: numbered 000_* copies, "<Brief> - Hook N" sub-folders inside Clips
(parallel copies there made duplicate same-name folders on Drive), and shot-id file names.

API
  plan(items)        items: dicts with "key", and either "hook" (letter) or "sec" (int);
                     optional "slot" (items sharing a slot share a position) and "take" (int).
                     Returns {key: file name}, positions counted in the order given.
  check_names(names) -> list of problems (bad pattern, gaps in a hook or section sequence)
CLI
  python delivery_names.py --drive-folder-id <id>     list Drive and verify layout + names.
                                                      Prints VERIFY: PASS / FAIL, exit 1 on FAIL.
"""
import argparse
import json
import re
import subprocess
import sys

NAME_RE = re.compile(r"^(?:H(?P<hook>[A-Z])_(?P<hn>\d+)|S(?P<sec>\d{2})_(?P<pos>\d{2}))(?:_v(?P<take>\d+))?\.mp4$")
ELEMENTS = {"Stills", "Clips", "Audio"}
REJECT_MARKS = ("_rejected", "_rejects", "_bakeoff", "_qa/", "_parts/")


def plan(items):
    names, hook_n, sec_n, slots = {}, {}, {}, {}
    for it in items:
        slot = it.get("slot", it["key"])
        if slot not in slots:
            if it.get("hook"):
                h = it["hook"].upper()
                hook_n[h] = hook_n.get(h, 0) + 1
                slots[slot] = "H%s_%d" % (h, hook_n[h])
            else:
                sec = int(it["sec"])
                if not 0 < sec < 100:
                    raise ValueError("section %r out of range for %s" % (it["sec"], it["key"]))
                sec_n[sec] = sec_n.get(sec, 0) + 1
                if sec_n[sec] > 99:
                    raise ValueError("section %d has more than 99 clips" % sec)
                slots[slot] = "S%02d_%02d" % (sec, sec_n[sec])
        take = it.get("take")
        names[it["key"]] = slots[slot] + ("_v%d" % int(take) if take else "") + ".mp4"
    dupes = sorted({n for n in names.values() if list(names.values()).count(n) > 1})
    if dupes:
        raise ValueError("duplicate delivered names: %s" % dupes)
    return names


def check_names(names):
    bad, hooks, secs = [], {}, {}
    for n in names:
        m = NAME_RE.match(n)
        if not m:
            bad.append("BAD NAME %s (want HA_1.mp4 / S01_01.mp4 / S01_01_v1.mp4)" % n)
            continue
        if m.group("hook"):
            hooks.setdefault(m.group("hook"), set()).add(int(m.group("hn")))
        else:
            secs.setdefault(int(m.group("sec")), set()).add(int(m.group("pos")))
    for label, groups in (("hook H", hooks), ("section S", secs)):
        for g, nums in sorted(groups.items()):
            gaps = sorted(set(range(1, max(nums) + 1)) - nums)
            if gaps:
                bad.append("GAP in %s%s: missing positions %s" % (label, g if isinstance(g, str) else "%02d" % g, gaps))
    return bad


def verify_drive(folder_id):
    r = subprocess.run(["rclone", "lsjson", "gdrive:", "-R", "--drive-root-folder-id", folder_id],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return ["RCLONE " + r.stderr.strip()[-300:]], {}
    items = json.loads(r.stdout)
    bad = []
    top = {x["Path"] for x in items if "/" not in x["Path"] and x["IsDir"]}
    bad += ["MISSING FOLDER " + d for d in ("Creatives", "Elements") if d not in top]
    el = {x["Path"].split("/")[1] for x in items if x["Path"].startswith("Elements/") and x["Path"].count("/") == 1}
    bad += ["NOT ALLOWED UNDER Elements/: " + d for d in sorted(el - ELEMENTS)]
    clips = [x for x in items if x["Path"].startswith("Elements/Clips/")]
    bad += ["FOLDER IN Elements/Clips: " + x["Path"] for x in clips if x["IsDir"]]
    files = [x for x in clips if not x["IsDir"]]
    bad += ["NOT FLAT: " + x["Path"] for x in files if x["Path"].count("/") > 2]
    bad += ["NOT A VIDEO: " + x["Path"] for x in files if not x["Name"].lower().endswith(".mp4")]
    names = [x["Name"] for x in files if x["Path"].count("/") == 2]
    bad += check_names(names)
    bad += ["REJECT ON DRIVE: " + x["Path"] for x in items if any(k in x["Path"] + "/" for k in REJECT_MARKS)]
    stats = {"clips": len(names), "hook_openers": sorted(n for n in names if n.startswith("H")),
             "stills": sum(1 for x in items if x["Path"].startswith("Elements/Stills/") and not x["IsDir"]),
             "audio": sum(1 for x in items if x["Path"].startswith("Elements/Audio/") and not x["IsDir"])}
    return bad, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--drive-folder-id", required=True)
    a = ap.parse_args()
    bad, stats = verify_drive(a.drive_folder_id)
    print("clips %s | stills %s | audio %s | hook openers %s" % (
        stats.get("clips"), stats.get("stills"), stats.get("audio"), ", ".join(stats.get("hook_openers", []))))
    print("VERIFY:", "PASS" if not bad else "FAIL (%d)" % len(bad))
    for b in bad[:60]:
        print("  ", b)
    sys.exit(0 if not bad else 1)


if __name__ == "__main__":
    main()
