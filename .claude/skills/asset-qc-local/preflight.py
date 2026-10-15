#!/usr/bin/env python3
"""START-OF-GEN gate for every AI-gen skill (flow-local here; Higgsfield-engine skills use the same rows).

The user's checklist, 2026-09-03 — nothing generates until every row is OK or the user has cleared the FLAG:

  1. Which engine/account is this run on — Higgsfield or Flow?
  2. If Higgsfield: WHOSE account is signed in, and does it match the client this work is for?
     A mismatch is a FLAG immediately (client credits on the wrong client's work).
  3. Which model(s) will fire — confirmed against the skill's allowed list.
  4. Local project + Drive project both have the canonical folders — created if missing —
     and the SCRIPT is inside (Creatives/).
  5. Elements/ holds exactly three folders: Stills/, Clips/ and Audio/. Anchors, refs, recycled,
     generated, HUD-baked, screen-lane — everything is a still, a clip or an audio track, and
     nothing else lives under Elements/. (Audio/ added 2026-09-08 at the user's call: VO tracks sitting
     in Creatives/ next to the PDFs confused the editor. Music and VO both belong here.)
  6. Every recurring character and place, and every product, carries a locked media ID
     (Gate 1 pass only - pass --refs. See REF_LOCKS.md). A CHARACTER lock needs its model
     sheet + cropped panels; a PRODUCT lock must come from the client's product_registry
     folder - products are never generated (the user, 2026-09-21).
  7. Was the inspo / reference WATCHED before anything started? (refwatch.py)
  8. Does the shot table still say what the SCRIPT says? (scriptalign.py; --align, Gate 1).

    python preflight.py --client flow --project "<root>/<PROJECT>" --script "<path to script>" --model nano_banana_2 --model google_omni [--drive-folder-id <id> | --drive-parent-id <id>] [--no-drive]

Exit 0 = all OK.  Exit 2 = at least one FLAG (print it to the user, do not fire).  Exit 3 = registry/config error.
Writes <project>/Creatives/_preflight.json so the run is auditable.
"""
import hf_call  # noqa: E402
import argparse, datetime, json, os, re, shutil, subprocess, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import refwatch  # noqa: E402
CANON = ["Creatives", "Elements", "Elements/Stills", "Elements/Clips", "Elements/Audio"]
# Windows is case-insensitive and Drive is not, so accept either casing rather than flagging one.
ELEMENTS_OK = ("Stills", "Clips", "Audio", "AUDIO")
SCRIPT_EXT = {".docx", ".pdf", ".md", ".txt", ".json", ".py", ".rtf"}
# Row 6. How many beats an entity must appear in before it needs a locked media ID.
# A product needs one from its first appearance - the wrong box is a dead ad.
LOCK_THRESHOLD = {"character": 2, "product": 1, "place": 2}
UNSET_IDS = {"", "TODO", "NONE", "-", "VERIFY_AT_USE"}
SHEET_PANELS = ("front", "profile", "three_quarter", "closeup")


def run(cmd, timeout=90):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 127, "%s: not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def jload(out):
    """rclone prefixes lsjson output with NOTICE/warning lines on stderr (the shared client_id
    retirement notice) and can emit a UTF-8 BOM. Parse from the first bracket, never from byte 0."""
    if not out:
        return []
    t = out.lstrip("﻿")
    i = min([x for x in (t.find("["), t.find("{")) if x >= 0], default=-1)
    if i < 0:
        return []
    try:
        return json.loads(t[i:])
    except Exception:
        return []


def rclone_bin():
    p = shutil.which("rclone")
    if p:
        return p
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    for root, _d, files in os.walk(base) if os.path.isdir(base) else []:
        if "rclone.exe" in files:
            return os.path.join(root, "rclone.exe")
    return "rclone"


class Gate:
    def __init__(self):
        self.rows = []

    def ok(self, item, detail=""):
        self.rows.append(("OK", item, detail))

    def flag(self, item, detail=""):
        self.rows.append(("FLAG", item, detail))

    def note(self, item, detail=""):
        self.rows.append(("NOTE", item, detail))

    @property
    def flagged(self):
        return [r for r in self.rows if r[0] == "FLAG"]


# ----------------------------------------------------------------------------- 1 + 2: engine / account
def check_account(client, g):
    eng = client["engine"]
    g.ok("1. engine", "%s (%s)" % (eng, client["skill"]))
    if eng == "higgsfield-cli":
        hf = shutil.which("higgsfield") or shutil.which("higgsfield.cmd")
        if not hf:
            for cand in (os.path.expandvars(r"%APPDATA%\npm\higgsfield.cmd"), os.path.expandvars(r"%LOCALAPPDATA%\npm\higgsfield.cmd")):
                if os.path.exists(cand):
                    hf = cand; break
        rc, out = run([hf or "higgsfield", "account", "status"])
        if rc != 0 and "not found" in out:
            g.flag("2. account", "higgsfield CLI not on PATH - cannot verify who is signed in"); return
        status, email = hf_call.account_status(rc, out)
        if status == "unreachable":
            g.flag("2. account", "API unreachable (%s) - retry in a minute; NOT an account mismatch" % email); return
        email = email if status == "ok" else None
        # `account status` prints only the email/plan/credits - it never names a
        # workspace, so the selected workspace has to come from `workspace list`,
        # whose selected row is the one carrying the check mark.
        _, ws_out = run([hf or "higgsfield", "workspace", "list"])
        sel = ""
        for ln in ws_out.splitlines():
            if "✓" in ln or ln.rstrip().endswith("*"):
                sel = ln
                break
        ws_ok = client.get("workspace_id", "") and client["workspace_id"] in sel
        ws_name_ok = client.get("workspace_name", "") and client["workspace_name"].lower() in sel.lower()
        if email is None:
            g.flag("2. account", "no signed-in account found in `higgsfield account status`:\n%s" % out.strip()[:400]); return
        if email != client["account"].lower():
            g.flag("2. account", "signed in as %s but %s work must run on %s - STOP, the user signs into the right account themselves"
                   % (email, client["skill"], client["account"])); return
        g.ok("2. account", "%s matches client %s" % (email, client["skill"]))
        if client.get("workspace_id") and not (ws_ok or ws_name_ok):
            g.flag("2. workspace", "workspace %s (%s) not selected - run `higgsfield workspace set %s`, then re-check"
                   % (client["workspace_name"], client["workspace_id"], client["workspace_id"]))
        else:
            g.ok("2. workspace", client.get("workspace_name", "-"))
    elif eng == "higgsfield-mcp":
        g.note("2. account", "Higgsfield MCP = CLIENT tool (%s). Before the first spend call `list_workspaces` and confirm the "
               "active workspace is '%s' - never Private, never the user's own projects. Record the answer in the run notes."
               % (client.get("workspace_name", "client"), client.get("workspace_name", "client")))
    elif eng == "flow":
        g.note("2. account", "%s. Confirm the Edge avatar is the user's Ultra login and run SETTINGS-ASSERT before the first prompt; "
               "the Flow project name must equal the local project folder name." % client["account"])
    else:
        g.flag("2. account", "unknown engine %r in clients.json" % eng)


# ----------------------------------------------------------------------------- 3: models
def check_models(client, models, g):
    if not models:
        g.flag("3. model", "no --model given - state the still and motion model before firing"); return
    allowed = [m.lower() for m in client.get("models_allowed", [])]
    bad = [m for m in models if m.lower() not in allowed]
    if bad:
        g.flag("3. model", "%s not in the %s allowed list %s - %s" % (", ".join(bad), client["skill"], allowed, client.get("models_note", "")))
    else:
        g.ok("3. model", ", ".join(models))


# ----------------------------------------------------------------------------- 4 + 5: local layout + script
def check_local(project, script, g, fix=True):
    created = []
    for sub in CANON:
        p = os.path.join(project, *sub.split("/"))
        if not os.path.isdir(p):
            if fix:
                os.makedirs(p, exist_ok=True); created.append(sub)
            else:
                g.flag("4. local layout", "missing %s" % sub)
    if fix or not any(r[0] == "FLAG" and r[1] == "4. local layout" for r in g.rows):
        g.ok("4. local layout", "%s  (created: %s)" % (project, ", ".join(created) or "nothing, all present"))
    creatives = os.path.join(project, "Creatives")
    have = ([f for f in os.listdir(creatives) if os.path.splitext(f)[1].lower() in SCRIPT_EXT and not f.startswith("_")]
            if os.path.isdir(creatives) else [])
    if script:
        if not os.path.exists(script):
            g.flag("4. script", "given script does not exist: %s" % script)
        else:
            dst = os.path.join(creatives, os.path.basename(script))
            if os.path.abspath(script) != os.path.abspath(dst) and (not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(script)):
                shutil.copy2(script, dst)
            g.ok("4. script", "in Creatives/: %s" % os.path.basename(dst))
    elif have:
        g.ok("4. script", "Creatives/ already holds: %s" % ", ".join(have[:5]))
    else:
        g.flag("4. script", "no script/brief in %s - pass --script <file> (the script always travels with the project)" % creatives)
    # 5. Elements holds exactly Stills + Clips + Audio
    el = os.path.join(project, "Elements")
    extra = sorted(d for d in os.listdir(el) if d not in ELEMENTS_OK) if os.path.isdir(el) else []
    if extra:
        legacy = [d for d in extra if d in ("Graphics", "Footage", "Prompts")]
        g.flag("5. Elements/ = Stills + Clips + Audio only", "extra entries under Elements/: %s%s" % (
            ", ".join(extra), "  (legacy layout - migrate with `python preflight.py --migrate`)" if legacy else ""))
    else:
        g.ok("5. Elements/ = Stills + Clips + Audio only", "clean")
    stray = sorted(d for d in os.listdir(project)
                   if os.path.isdir(os.path.join(project, d)) and d not in ("Creatives", "Elements") and not d.startswith("_") and not d.startswith("."))
    if stray:
        g.note("5. project root", "other folders at root (allowed, but nothing generated may land there): %s" % ", ".join(stray))


def migrate(project, g):
    """Legacy -> canonical: Elements/Graphics + Footage/* + Prompts, 0N_* folders -> Creatives / Elements/Stills / Elements/Clips."""
    moves = []
    el = os.path.join(project, "Elements")
    stills = os.path.join(el, "Stills"); clips = os.path.join(el, "Clips"); creatives = os.path.join(project, "Creatives")
    for d in (stills, clips, creatives):
        os.makedirs(d, exist_ok=True)
    def mv(src, dst_dir, keep_sub=None):
        if not os.path.isdir(src):
            return
        dst = os.path.join(dst_dir, keep_sub) if keep_sub else dst_dir
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            s, t = os.path.join(src, name), os.path.join(dst, name)
            if os.path.exists(t):
                g.note("migrate", "skip (exists): %s" % t); continue
            shutil.move(s, t); moves.append("%s -> %s" % (os.path.relpath(s, project), os.path.relpath(t, project)))
        try:
            os.rmdir(src)
        except OSError:
            pass
    mv(os.path.join(el, "Graphics"), stills)
    mv(os.path.join(el, "Prompts"), creatives)
    foot = os.path.join(el, "Footage")
    if os.path.isdir(foot):
        for sub in os.listdir(foot):
            p = os.path.join(foot, sub)
            if sub.lower() == "reference":
                mv(p, stills, "_refs")
            elif sub.lower() in ("hud", "screen"):
                mv(p, clips, sub)
            else:
                mv(p, clips)
        try:
            os.rmdir(foot)
        except OSError:
            pass
    for legacy, dst, sub in (("02_REFERENCE_STILLS", stills, None), ("03_MOTION_CLIPS", clips, None),
                             ("BOOK_REFS", stills, "_refs"), ("HERO_SHOTS", stills, "_anchors"),
                             ("01_ANCHORS", stills, "_anchors"), ("PRODUCT_REFS", stills, "_refs"),
                             ("00_BRIEF", creatives, None), ("06_DOCS", creatives, None)):
        mv(os.path.join(project, legacy), dst, sub)
    for legacy in ("02_STILLS", "03_CLIPS"):
        p = os.path.join(project, legacy)
        if os.path.isdir(p):
            for sub in os.listdir(p):
                mv(os.path.join(p, sub), stills if legacy == "02_STILLS" else clips, sub)
            try:
                os.rmdir(p)
            except OSError:
                pass
    g.ok("migrate", "%d moves" % len(moves))
    for m in moves[:40]:
        print("   " + m)


# ----------------------------------------------------------------------------- 4: Drive layout
def check_drive(client, project, script, folder_id, parent_id, g, fix=True):
    rc_ = rclone_bin()
    name = os.path.basename(os.path.normpath(project))
    if not folder_id and not parent_id:
        parent_id = client.get("drive_root_id") or ""
    if not folder_id and parent_id:
        rc, out = run([rc_, "lsjson", "gdrive:", "--drive-root-folder-id", parent_id, "--dirs-only"], 120)
        if rc != 0:
            g.flag("4. drive", "cannot list parent %s: %s" % (parent_id, out.strip()[:200])); return None
        try:
            dirs = jload(out)
        except Exception:
            dirs = []
        hit = next((d for d in dirs if d["Name"].strip().lower() == name.strip().lower()), None)
        if hit:
            folder_id = hit["ID"]; g.ok("4. drive project", "existing folder found: %s (%s)" % (hit["Name"], folder_id))
        elif fix:
            rc, out = run([rc_, "mkdir", "gdrive:" + name, "--drive-root-folder-id", parent_id], 120)
            rc, out = run([rc_, "lsjson", "gdrive:", "--drive-root-folder-id", parent_id, "--dirs-only"], 120)
            dirs = jload(out) if rc == 0 else []
            hit = next((d for d in dirs if d["Name"].strip().lower() == name.strip().lower()), None)
            if not hit:
                g.flag("4. drive project", "could not create %r under %s" % (name, parent_id)); return None
            folder_id = hit["ID"]; g.ok("4. drive project", "created: %s (%s)" % (name, folder_id))
        else:
            g.flag("4. drive project", "no folder named %r under %s" % (name, parent_id)); return None
    if not folder_id:
        g.flag("4. drive", "no --drive-folder-id / --drive-parent-id and no drive_root_id for %s - where does this deliver?" % client["skill"]); return None
    rc, out = run([rc_, "lsjson", "gdrive:", "--drive-root-folder-id", folder_id, "-R", "--dirs-only"], 180)
    if rc != 0:
        g.flag("4. drive", "cannot list %s: %s" % (folder_id, out.strip()[:200])); return folder_id
    have = {d["Path"].replace("\\", "/") for d in jload(out)}
    created = []
    for sub in CANON:
        if sub not in have:
            if fix:
                run([rc_, "mkdir", "gdrive:" + sub, "--drive-root-folder-id", folder_id], 120); created.append(sub)
            else:
                g.flag("4. drive layout", "missing %s" % sub)
    g.ok("4. drive layout", "folder %s  (created: %s)" % (folder_id, ", ".join(created) or "nothing, all present"))
    extra = sorted(p for p in have if p.startswith("Elements/") and p.count("/") == 1
                   and p.split("/")[-1] not in ELEMENTS_OK)
    if extra:
        g.flag("5. drive Elements/ = Stills + Clips + Audio only", ", ".join(extra))
    # script on Drive
    rc, out = run([rc_, "lsjson", "gdrive:Creatives", "--drive-root-folder-id", folder_id], 120)
    files = [f["Name"] for f in jload(out)] if rc == 0 else []
    scripts = [f for f in files if os.path.splitext(f)[1].lower() in SCRIPT_EXT and not f.startswith("_")]
    local_script = script or next((os.path.join(project, "Creatives", f) for f in os.listdir(os.path.join(project, "Creatives"))
                                   if os.path.splitext(f)[1].lower() in SCRIPT_EXT and not f.startswith("_")), None)
    if scripts:
        g.ok("4. drive script", "Creatives/ holds: %s" % ", ".join(scripts[:5]))
    elif local_script and fix:
        rc, out = run([rc_, "copy", local_script, "gdrive:Creatives", "--drive-root-folder-id", folder_id], 300)
        if rc == 0:
            g.ok("4. drive script", "uploaded %s to Creatives/" % os.path.basename(local_script))
        else:
            g.flag("4. drive script", "upload failed: %s" % out.strip()[:200])
    else:
        g.flag("4. drive script", "no script in Drive Creatives/ and none locally to upload")
    return folder_id


# ----------------------------------------------------------------------------- 6: ref locks

def product_rule(e, eid, anchor, project, client):
    """Products come from the client's standing registry folder - never generated, never
    re-derived from the client's website (the user, 2026-09-21). See REF_LOCKS.md."""
    if (e.get("source") or "").strip().lower() == "generated":
        return ("PRODUCT %s is source=generated - a product is never generated. Take the approved "
                "packaging from the client's product registry and lock that file." % eid)
    if client is None:
        return None                      # no client dict (unit tests) - registry unresolvable
    reg = (client.get("product_registry") or "").strip()
    if not reg:
        return ("PRODUCT %s: clients.json has no product_registry for this client - make the "
                "standing folder and point at it before locking a product." % eid)
    if not os.path.isdir(reg):
        return "PRODUCT %s: product_registry is not on disk: %s" % (eid, reg)
    if not anchor:
        return "PRODUCT %s has no anchor - point it at a file in %s" % (eid, reg)
    ap = os.path.abspath(anchor if os.path.isabs(anchor) else os.path.join(project, anchor))
    root = os.path.normcase(os.path.abspath(reg)) + os.sep
    if not os.path.normcase(ap).startswith(root):
        return ("PRODUCT %s anchors outside the registry: %s - copy the approved packaging into "
                "%s and lock that path." % (eid, ap, reg))
    return product_sheet_rule(e, eid, reg)


PRODUCT_PANELS = ("face", "three_quarter", "scale")


def product_sheet_rule(e, eid, reg):
    """STRICT since 2026-09-21 (the user): a product's anchor IS a product sheet, the same way a
    character's is - RECYCLED from the client's registry when one exists, built there with
    productsheet.py when it does not. Never generated: every panel is supplied photography or
    a frame of the client's own footage."""
    sheet = (e.get("sheet") or "").strip()
    if not sheet:
        return ("PRODUCT %s has no product sheet - the anchor for a product IS a sheet (%s). "
                "Recycle the one in %s if it exists, otherwise build it there with "
                "productsheet.py --build. Products are never generated."
                % (eid, ", ".join(PRODUCT_PANELS), reg))
    sp = sheet if os.path.isabs(sheet) else os.path.join(reg, sheet)
    if not os.path.isfile(sp):
        return "PRODUCT %s: product sheet missing on disk: %s" % (eid, sheet)
    panels = (e.get("panels") or "").strip() or os.path.splitext(sp)[0] + "_panels"
    pp = panels if os.path.isabs(panels) else os.path.join(reg, panels)
    missing = [n for n in PRODUCT_PANELS if not os.path.isfile(os.path.join(pp, n + ".png"))]
    if missing:
        return ("PRODUCT %s: sheet not cropped - %s missing from %s (productsheet.py --crop)"
                % (eid, ", ".join(missing), pp))
    return None


def sheet_rule(e, eid, project):
    """A character lock is a MODEL SHEET plus its cropped panels, not a hero still - otherwise
    the model invents every angle it was never shown (the user, 2026-09-21: strictly)."""
    sheet = (e.get("sheet") or "").strip()
    if not sheet:
        return ("CHARACTER %s has no model sheet - mint one (charsheet.py --prompt), get the user's "
                "approval, crop it (--crop), and put 'sheet' + 'panels' on the lock." % eid)
    sp = sheet if os.path.isabs(sheet) else os.path.join(project, sheet)
    if not os.path.isfile(sp):
        return "CHARACTER %s: model sheet missing on disk: %s" % (eid, sheet)
    panels = (e.get("panels") or "").strip() or os.path.splitext(sp)[0] + "_panels"
    pp = panels if os.path.isabs(panels) else os.path.join(project, panels)
    missing = [n for n in SHEET_PANELS if not os.path.isfile(os.path.join(pp, n + ".png"))]
    if missing:
        return ("CHARACTER %s: sheet not cropped - %s missing from %s (charsheet.py --crop)"
                % (eid, ", ".join(missing), pp))
    return None



def check_align(a, project, g):
    """Row 8 - the shot table still says what the script says. See scriptalign.py."""
    if not a.script or not os.path.isfile(a.script):
        g.flag("8. script alignment", "--align needs --script (the script the shots were built from)")
        return
    import argparse as _ap
    import scriptalign
    ns = _ap.Namespace(client=a.client, dir=None, manifest=None)
    if os.path.isdir(a.align):
        ns.dir = a.align
    else:
        ns.manifest = a.align
    try:
        with open(a.script, encoding="utf-8", errors="replace") as f:
            script_lines = scriptalign.sentences(f.read())
        shots, _ = scriptalign.load_shots(ns)
    except Exception as e:
        g.flag("8. script alignment", "could not read the shot table: %s" % e)
        return
    findings, pairs = scriptalign.align(script_lines, shots)
    hard = [f for f in findings if f[0] != "PARAPHRASED"]
    soft = len(findings) - len(hard)
    if hard:
        g.flag("8. script alignment", "\n".join("%-11s %-12s %s" % (c, i, d[:70]) for c, i, d in hard[:12]))
    else:
        g.ok("8. script alignment", "%d/%d script line(s) covered, in order%s"
             % (len(pairs), len(script_lines),
                " (%d paraphrased - reported, your call)" % soft if soft else ""))


def check_refs(project, refs_path, g, client=None):
    """Row 6 - every recurring entity carries a locked media ID.

    Only runs when --refs is passed. preflight fires at start-of-gen, BEFORE Gate 0, when
    no anchor can exist yet; the skills call preflight a second time at Gate 1 with --refs
    once the anchors are approved and imported. See REF_LOCKS.md.
    """
    path = refs_path or os.path.join(project, "Creatives", "_reflocks.json")
    if not os.path.isfile(path):
        g.flag("6. ref locks", "no lock table at %s - the skill writes it at shotlist time" % path)
        return
    try:
        with open(path, encoding="utf-8") as f:
            tbl = json.load(f)
    except Exception as e:
        g.flag("6. ref locks", "%s is not readable JSON: %s" % (path, e))
        return
    ents = tbl.get("entities") or []
    if not ents:
        g.flag("6. ref locks", "%s declares no entities - an ad with no character, product or "
                               "place is a FLAG, not a pass" % path)
        return
    bad, reused, locked = [], [], []
    unset = {u.upper() for u in UNSET_IDS}
    for e in ents:
        eid = e.get("id") or "<unnamed>"
        kind = (e.get("kind") or "").strip().lower()
        if kind not in LOCK_THRESHOLD:
            bad.append("%s has unknown kind %r - expected character, product or place" % (eid, e.get("kind")))
            continue
        beats = e.get("beats") or []
        if len(beats) < LOCK_THRESHOLD[kind]:
            continue  # one-off character / one-off place stays free
        mid = (e.get("locked_media_id") or "").strip()
        if mid.upper() in unset:
            bad.append("%s %s in %d beat(s), no locked media id" % (kind.upper(), eid, len(beats)))
            continue
        anchor = e.get("anchor") or ""
        if anchor:
            ap = anchor if os.path.isabs(anchor) else os.path.join(project, anchor)
            if not os.path.isfile(ap):
                bad.append("%s %s locked to %s but its anchor is missing: %s"
                           % (kind.upper(), eid, mid, anchor))
                continue
        if kind == "product":
            msg = product_rule(e, eid, anchor, project, client)
            if msg:
                bad.append(msg); continue
        if kind == "character":
            msg = sheet_rule(e, eid, project)
            if msg:
                bad.append(msg); continue
        (reused if e.get("source") in ("registry", "supplied") else locked).append(eid)
    if bad:
        g.flag("6. ref locks", "\n".join(bad))
    else:
        g.ok("6. ref locks", "%d entity(ies) locked" % (len(locked) + len(reused)))
    if reused:
        g.note("6. ref locks reuse", "REUSED (0 cr): %s" % ", ".join(reused))


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", required=True, help="key in clients.json (this kit ships: flow)")
    ap.add_argument("--project", required=True, help="local project root (created if missing)")
    ap.add_argument("--script", default=None, help="the ad script / brief - copied into Creatives/ and uploaded")
    ap.add_argument("--model", action="append", default=[], help="model that will fire (repeat: still + motion)")
    ap.add_argument("--drive-folder-id", default=None, help="existing Drive project folder id")
    ap.add_argument("--drive-parent-id", default=None, help="Drive parent to look in / create the project folder under")
    ap.add_argument("--no-drive", action="store_true", help="skip the Drive half (local-only handover)")
    ap.add_argument("--no-fix", action="store_true", help="report only, create nothing")
    ap.add_argument("--align", default=None, metavar="MANIFEST|DIR",
                    help="Gate 1 pass: check the shot table still says what --script says")
    ap.add_argument("--migrate", action="store_true", help="move a legacy layout into Creatives / Elements/Stills / Elements/Clips first")
    ap.add_argument("--refs", nargs="?", const="", default=None,
                    help="Gate 1 pass: check Creatives/_reflocks.json - every recurring character "
                         "and place, and every product, carries a locked media ID (see REF_LOCKS.md). "
                         "Omit to skip row 6; that is the waiver.")
    a = ap.parse_args()

    with open(os.path.join(HERE, "clients.json"), encoding="utf-8") as f:
        reg = json.load(f)
    client = reg.get(a.client)
    if not client:
        print("unknown client %r - known: %s" % (a.client, [k for k in reg if not k.startswith("_")])); sys.exit(3)
    # HF_CLIENT: route every Higgsfield call to this client's login (optional login router)
    if client.get("engine") == "higgsfield-cli":
        hf_call.pin_hf_client(a.client)
    project = os.path.abspath(a.project)
    os.makedirs(project, exist_ok=True)
    g = Gate()
    if a.migrate:
        migrate(project, g)
    check_account(client, g)
    check_models(client, a.model, g)
    check_local(project, a.script, g, fix=not a.no_fix)
    fid = None
    if not a.no_drive:
        fid = check_drive(client, project, a.script, a.drive_folder_id, a.drive_parent_id, g, fix=not a.no_fix)
    else:
        g.note("4. drive", "skipped (--no-drive)")

    if a.refs is not None:
        check_refs(project, a.refs, g, client)
    else:
        g.note("6. ref locks", "not checked - no --refs (this is the start-of-gen pass; "
                               "run again with --refs at Gate 1)")


    if a.script and os.path.isfile(a.script):
        ok, msg = refwatch.check(project, a.script)
        (g.ok if ok else g.flag)("7. references watched", msg)
    else:
        g.note("7. references watched", "no --script given - nothing scanned for references")

    if a.align:
        check_align(a, project, g)
    else:
        g.note("8. script alignment", "not checked - no --align (Gate 1 pass: pass the "
                                      "manifest / shotlist the shots live in)")
    print("\nSTART-OF-GEN GATE  ·  client %s  ·  %s" % (a.client, project))
    print("-" * 78)
    for status, item, detail in g.rows:
        mark = {"OK": "ok  ", "FLAG": "FLAG", "NOTE": "note"}[status]
        print("%s  %-40s %s" % (mark, item, detail.replace("\n", "\n" + " " * 47)))
    print("-" * 78)
    rec = {"when": datetime.datetime.now().isoformat(timespec="seconds"), "client": a.client, "project": project,
           "models": a.model, "drive_folder_id": fid, "rows": [{"status": s, "item": i, "detail": d} for s, i, d in g.rows]}
    audit_dir = os.path.join(project, "Creatives") if os.path.isdir(os.path.join(project, "Creatives")) else project
    with open(os.path.join(audit_dir, "_preflight.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    if g.flagged:
        print("%d FLAG(s) - do NOT fire. Show the user the flagged rows and wait for their call." % len(g.flagged)); sys.exit(2)
    print("all clear - the gate is open. Drive folder id: %s" % (fid or "-"))


if __name__ == "__main__":
    main()
