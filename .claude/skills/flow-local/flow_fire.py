"""flow_fire.py - manifest-driven fire script for Google Flow.

Lints every selected line, writes the lint stamp (Creatives/_flow_lint.json), then fires in WAVES
through the flow MCP bridge Service: up to --wave N lines are fired queued (each returns once Flow
ACCEPTED it, charged at that point), then Service.settle() is polled and each landed line downloaded,
and the next wave starts only when this one is terminal (or its settle timeout ran out - the rest are
reported STILL_RENDERING and stay pending for flow_settle). --wave 1 = one blocking fire per line.
Never takes a RunLock itself (Service does per call).

  uv run --project <root>/mcp/flow python <root>/.claude/skills/flow-local/flow_fire.py   (<root> = the folder holding .claude/)
      --project <dir> --stage stills|clips [--only L01,L02] [--dry-run] [--budget N] [--count 1|2]
      [--wave 1-6] [--clip-waves-verified]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "asset-qc-local"))
sys.path.insert(0, str(HERE.parents[2] / "mcp" / "flow" / "src"))

import gen_lints  # noqa: E402
from gen_lints import Finding  # noqa: E402
from flow_mcp import stamp  # noqa: E402
from flow_mcp.guards import FlowError  # noqa: E402

CLIENTS = HERE.parent / "asset-qc-local" / "clients.json"
CLIP_CREDITS = 12
STOP_CODES = {"OUT_OF_CREDITS", "CAPTCHA", "LOGIN_REQUIRED", "RUN_LOCKED", "BROWSER_DOWN"}
# A FlowError with one of these codes, raised BEFORE the click, charged nothing: the pre-charged
# 12 x count is refunded. Service tags any FlowError raised after the click with after_click=True
# (never refunded). BROWSER_DOWN is never refunded: it can come after begin_fire / the click.
PRE_CLICK_CODES = {"LINT_REQUIRED", "ALREADY_COMMITTED", "RUN_LOCKED", "SETTINGS_MISMATCH",
                   "REF_NOT_ATTACHED", "PATH_REFUSED", "OUT_OF_CREDITS", "CAPTCHA", "LOGIN_REQUIRED",
                   "UI_CHANGED", "UNCONFIRMED", "SAME_PROMPT_PENDING", "IN_FLIGHT",
                   "LOST_PENDING_RELEASE"}  # UNCONFIRMED = pending-line refusal
MAX_STILL_REFS = 3
MAX_STILL_OUTPUTS = 4
DEFAULT_WAVE, MAX_WAVE = 4, 6  # stills; clips default to 1 (clip waves are not live-verified)
SETTLE_TIMEOUT_S = 600  # per wave: in-flight lines not landed by then are reported STILL_RENDERING
SETTLE_POLL_S = 5


def _sleep(s):
    time.sleep(s)


def _refundable(e):
    return e.code in PRE_CLICK_CODES and not getattr(e, "after_click", False)


def _alts(shot):
    """Still outputs for a line = shot.alts, clamped 1..4 (a missing or bad value -> 1)."""
    try:
        n = int(shot.get("alts", 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(MAX_STILL_OUTPUTS, n))


def _clip_prompt(shot):
    """EXACTLY the text passed to fire_clip (and hashed into the stamp)."""
    p = shot.get("i2v_prompt")
    if isinstance(p, str):
        return p
    if p is None:
        return ""
    return json.dumps(p, ensure_ascii=False)


def _status(findings):
    if any(f.level == "FAIL" for f in findings):
        return "FAIL"
    return "WARN" if any(f.level == "WARN" for f in findings) else "PASS"


def _die(msg):
    print("flow_fire: " + msg, file=sys.stderr)
    return 2


def _load_run(proj_dir):
    try:
        return json.loads((Path(proj_dir) / "_flow_run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_PID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _bound(run):
    """True when the run state is bound to a Flow project. flow_fire is an attach-only peer of the MCP
    server on the ONE shared Flow tab: Service moves that tab to the bound project under the tab lock."""
    pid = run.get("projectId")
    return isinstance(pid, str) and bool(_PID_RE.fullmatch(pid))


def _already(run, tag, stage):
    """ALREADY_DONE / PENDING when the run state says this line was already fired (else None)."""
    kind = "clip" if stage == "clips" else "still"
    L = (run.get("lines") or {}).get(tag) or {}
    if kind == "clip":
        if (L.get("clip") or {}).get("versions"):
            return "ALREADY_DONE"
        for at in (run.get("attempts") or {}).values():
            if at.get("line") == tag and at.get("stage") == "clip" and at.get("status") == "lost":
                return "LOST"  # spend unknown: only the user releases it (see SKILL.md), or --reroll
            if at.get("line") == tag and at.get("stage") == "clip" and at.get("status") in (
                    "pending", "unconfirmed", "wrong_model", "model_unknown", "no_media_url"):
                return "PENDING"
    else:
        if any(c.get("slot") == "canonical" for c in (L.get("still") or {}).get("candidates", [])):
            return "ALREADY_DONE"
    return None


def lint_lines(project, shots, stage, registry, all_shots=None):
    """Return {tag: (findings, resolved_refs)}. all_shots = the whole manifest (masters live there, not in --only)."""
    out = {}
    for shot in shots:
        tag = shot["tag"]
        fs = list(gen_lints.lint_shot(shot, project, stage))
        refs = []
        try:
            refs = gen_lints.resolve_refs(shot, project, registry)
        except ValueError as e:
            fs.append(Finding("FAIL", "REF_MISSING", "%s %s" % (tag, e)))
        out[tag] = (fs, refs)
    cast = gen_lints.lint_cast(project, shots, all_shots or shots)
    cast += gen_lints.lint_sheets(project, all_shots or shots, registry)  # anchors are sheets
    for tag in out:
        out[tag][0].extend(cast)
    return out


def main(argv=None, service_factory=None):
    ap = argparse.ArgumentParser(description="Fire Flow generations from a manifest")
    ap.add_argument("--project", required=True)
    ap.add_argument("--stage", required=True, choices=["stills", "clips"])
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--count", type=int, default=1, choices=[1, 2])
    ap.add_argument("--reroll", action="store_true",
                    help="fire again even if the line already has a committed take / candidate")
    ap.add_argument("--wave", type=int, default=None,
                    help="lines in flight at once (1-%d; 1 = fire and wait per line). Default: stills %d, "
                         "clips 1" % (MAX_WAVE, DEFAULT_WAVE))
    ap.add_argument("--clip-waves-verified", action="store_true",
                    help="allow --wave > 1 for clips - only once clip waves are verified live")
    a = ap.parse_args(argv)
    if a.wave is None:
        a.wave = DEFAULT_WAVE if a.stage == "stills" else 1
    if not 1 <= a.wave <= MAX_WAVE:
        ap.error("--wave must be 1..%d" % MAX_WAVE)
    if a.stage == "clips" and a.wave > 1 and not a.clip_waves_verified:
        ap.error("--wave > 1 for clips needs --clip-waves-verified: clip-side waves are not verified live "
                 "(the first paid clip run is --wave 1, --count 1)")
    proj_dir = Path(a.project)
    try:
        mpath = stamp.manifest_for(proj_dir)
    except FlowError as e:
        return _die(str(e))
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return _die("cannot read manifest %s: %s" % (mpath, e))
    project = manifest.get("project")
    shots = manifest.get("shots")
    if not isinstance(project, dict) or not project.get("char_ref"):
        return _die("manifest project.char_ref is missing")
    if not isinstance(shots, list) or not shots:
        return _die("manifest has no shots list")
    if any(not isinstance(s, dict) or not s.get("tag") for s in shots):
        return _die("every shot needs a tag")
    tags = [s["tag"] for s in shots]
    dupes = sorted({t for t in tags if tags.count(t) > 1})
    if dupes:
        return _die("duplicate shot tag(s) in manifest: %s" % ", ".join(dupes))
    by_tag = {s["tag"]: s for s in shots}
    if a.only:
        want = [t.strip() for t in a.only.split(",") if t.strip()]
        twice = sorted({t for t in want if want.count(t) > 1})
        if twice:
            return _die("--only lists tag(s) twice: %s" % ", ".join(twice))
        bad = [t for t in want if t not in by_tag]
        if bad:
            return _die("unknown tag(s): %s" % ", ".join(bad))
        sel = [by_tag[t] for t in want]
    else:
        sel = list(shots)

    registry = gen_lints.registry_root_for(project)
    linted = lint_lines(project, sel, a.stage, registry, all_shots=shots)

    # stamp: merge existing lines only when the existing stamp matches this exact manifest
    lines = {}
    try:
        old = json.loads(stamp.stamp_path(proj_dir).read_text(encoding="utf-8"))
        if old.get("manifest_sha256") == stamp._sha(mpath) and isinstance(old.get("lines"), dict):
            lines.update(old["lines"])
    except (OSError, ValueError):
        pass
    for shot in sel:
        tag = shot["tag"]
        fs, refs = linted[tag]
        if a.stage == "stills" and len(refs) > MAX_STILL_REFS:
            fs.append(Finding("WARN", "REFS_TRUNCATED", "%s resolved %d refs, stills take the first %d"
                              % (tag, len(refs), MAX_STILL_REFS)))
        entry = {"status": _status(fs),
                 "findings": [{"level": f.level, "code": f.code, "msg": f.msg} for f in fs]}
        if a.stage == "clips":
            entry["prompt_sha256"] = stamp.prompt_sha(_clip_prompt(shot))
        lines[tag] = entry
    stamp.write(proj_dir, mpath, lines)

    print("Lint (%s):" % a.stage)
    for shot in sel:
        tag = shot["tag"]
        print("  %s  %s" % (tag, lines[tag]["status"]))
        for f in linted[tag][0]:
            if f.code != "CAST":
                print("      %s %s: %s" % (f.level, f.code, f.msg))
    cast_seen = set()
    for tag in linted:
        for f in linted[tag][0]:
            if f.code == "CAST" and f.msg not in cast_seen:
                cast_seen.add(f.msg)
                print("  cast %s: %s" % (f.level, f.msg))

    if a.dry_run:
        if a.stage == "clips":
            n = sum(1 for s in sel if lines[s["tag"]]["status"] != "FAIL")
            print("Dry run: nothing fired. Credits: %d (%d line(s) x %d cr x count %d)"
                  % (CLIP_CREDITS * a.count * n, n, CLIP_CREDITS, a.count))
        else:
            print("Dry run: nothing fired. Credits: 0 (stills are free)")
        return 0

    run = _load_run(proj_dir)
    if not _bound(run):
        return _die("_flow_run.json has no Flow projectId - run flow_open_project first")
    if service_factory is None:
        from flow_mcp.service import Service
        service_factory = Service.build
    svc = service_factory()
    slug = project.get("slug") or "flow"
    rows = []  # [tag, stage, status, media, file, credits] - a QUEUED row is updated in place
    R = {"ok": True, "stop": False, "deferred": []}  # deferred: (row, ids) downloads to retry
    spent = 0
    cap = a.budget if a.budget is not None else float("inf")
    queue = a.wave > 1
    # attempt -> row. waiting: this wave's clicks (QUEUED and UNCONFIRMED), the wave waits on them.
    # watch: rows a wave left unfinished - never waited on, but any later settle still lands them.
    waiting, watch = {}, {}
    in_wave = 0  # clicks in the current wave
    # Waves: every upload happens BEFORE the first fire - a new upload reloads the page, and any
    # navigation while fires are in flight kills them (live 2026-09-30).
    up_err = _upload_all(svc, proj_dir, sel, run, a, lines, linted, R) if queue else {}
    for shot in sel:
        tag = shot["tag"]
        if queue and in_wave >= a.wave:
            _settle_wave(svc, proj_dir, waiting, watch, R, rows)
            in_wave = 0
        if R["stop"]:
            rows.append([tag, a.stage, "NOT_RUN", "", "", 0])
            R["ok"] = False
            continue
        if not a.reroll:
            skip = _already(run, tag, a.stage)
            if skip:
                rows.append([tag, a.stage, skip, "", "", 0])
                continue
        if tag in up_err:
            rows.append([tag, a.stage, up_err[tag], "", "", 0])
            R["ok"] = False
            continue
        # credits: a clip that reached the fire_clip call is charged 12*count whatever came back
        # (never trust a non-numeric credits value); only a FlowError with a pre-click code and no
        # after_click tag (Service marked it not_fired, or refused before begin_fire) is refunded.
        # Charged BEFORE the call so an unexpected exception after it still lands in the spend report.
        credits = 0
        try:
            if a.stage == "stills":
                if lines[tag]["status"] == "FAIL":
                    print("  WARNING %s lint FAIL - firing still anyway" % tag)
                use = linted[tag][1][:MAX_STILL_REFS]
                for p in use if not queue else ():
                    svc.upload(p, proj_dir)
                res = svc.fire_still(proj_dir, slug, tag, shot.get("still_prompt", ""),
                                     refs=list(use), outputs=_alts(shot), queue=queue, reroll=a.reroll)
                file_ = shot.get("outName", "")
            else:
                if lines[tag]["status"] == "FAIL":
                    rows.append([tag, "clip", "LINT_FAIL", "", "", 0])
                    R["ok"] = False
                    continue
                if spent + CLIP_CREDITS * a.count > cap:
                    rows.append([tag, "clip", "BUDGET", "", "", 0])
                    R["ok"] = False
                    R["stop"] = True
                    continue
                out_name = shot.get("outName", "")
                sf = proj_dir / "Elements" / "Stills" / out_name
                if not out_name or not sf.is_file():
                    rows.append([tag, "clip", "NO_START_FRAME", "", out_name, 0])
                    R["ok"] = False
                    continue
                if not queue:
                    svc.upload(sf, proj_dir)
                dur = gen_lints.duration_of(shot)[0] or gen_lints.DEFAULT_DURATION
                credits = CLIP_CREDITS * a.count
                spent += credits
                try:
                    res = svc.fire_clip(proj_dir, shot.get("clipName") or ("%s_%s" % (slug, tag)),
                                        tag, _clip_prompt(shot), sf, dur, a.count, reroll=a.reroll,
                                        queue=queue)
                except FlowError as e:
                    if _refundable(e):
                        spent -= credits  # refused before the click: nothing charged
                        credits = 0
                    raise
                file_ = shot.get("clipName", "")
            status = res.get("status")
            ids = res.get("media_ids") or []
            row = [tag, a.stage, str(status), ",".join(ids), file_, credits]
            rows.append(row)
            if status == "QUEUED":
                waiting[res.get("attempt")] = row
                in_wave += 1
            elif status == "UNCONFIRMED" and queue and res.get("attempt"):
                # no sweep in a wave: settle() collects queued UNCONFIRMED attempts too, into this row
                waiting[res["attempt"]] = row
                # no Flow-side evidence of the job: stop firing (never a blind wave), settle what flies
                print("  %s UNCONFIRMED (no Flow-side acceptance) - stopping the run, not re-fired" % tag)
                R["stop"] = True
                in_wave += 1
            elif status == "UNCONFIRMED":
                sw = svc.sweep(proj_dir)
                print("  %s UNCONFIRMED - swept once, not re-fired: %s" % (tag, sw))
                R["ok"] = False
            else:
                _finish(svc, proj_dir, row, res, R)
        except FlowError as e:
            rows.append([tag, a.stage, e.code, "", "", credits])
            R["ok"] = False
            if e.code in STOP_CODES:
                R["stop"] = True
        except Exception as e:  # noqa: BLE001 - a surprise must never swallow the spend report
            rows.append([tag, a.stage, "ERROR %s: %s" % (type(e).__name__, e), "", "", credits])
            R["ok"] = False
            R["stop"] = True  # remaining lines NOT_RUN
    if waiting:  # the last wave (also after a stop: paid, in-flight lines are still collected)
        _settle_wave(svc, proj_dir, waiting, watch, R, rows)
    _retry_deferred(svc, proj_dir, R)
    for row, ids in R["deferred"]:  # a download that needs the detail view while fires are in flight
        row[2] = "DOWNLOAD_DEFERRED"
        R["ok"] = False
    if watch:  # left unfinished by their wave and never landed by a later settle: not committed
        R["ok"] = False

    print("\ntag | stage | status | media | file | credits")
    for r in rows:
        print(" | ".join(str(x) for x in r))
    print("credits spent: %s" % spent)
    return 0 if R["ok"] else 1


def _finish(svc, proj_dir, row, res, R):
    """Land one fire/settle result on its row: committed -> download; anything else is reported."""
    status = res.get("status")
    ids = res.get("media_ids") or []
    row[2], row[3] = str(status), ",".join(ids)
    if status == "LOST":
        print("  %s LOST - Flow never listed it; spend unknown (a clip stays charged here), not re-fired"
              % row[0])
    if status != "committed":
        R["ok"] = False
        if status in STOP_CODES:
            R["stop"] = True
        return
    try:
        dl = svc.download(proj_dir, ids)
    except FlowError as e:
        row[2] = "DOWNLOAD_" + e.code
        R["ok"] = False
        if e.code in STOP_CODES:
            R["stop"] = True
        return
    except Exception as e:  # noqa: BLE001 - the spend report must still print
        row[2] = "ERROR %s: %s" % (type(e).__name__, e)
        R["ok"] = False
        R["stop"] = True
        return
    _landed(row, ids, dl, R)


def _landed(row, ids, dl, R):
    """Apply a download result to its row: deferred ids are retried after the wave, failed ones
    (no signed URL obtainable) are reported DOWNLOAD_NO_MEDIA_URL."""
    if dl.get("failed"):
        row[2] = "DOWNLOAD_NO_MEDIA_URL"
        R["ok"] = False
    if dl.get("deferred"):
        R["deferred"].append((row, list(dl["deferred"])))
    row[4] = ", ".join(str(w) for w in dl.get("written", [])) or row[4]


def _retry_deferred(svc, proj_dir, R):
    """Downloads that needed the detail view while fires were in flight: try again now (the service
    still defers them if navigation is not safe yet)."""
    todo, R["deferred"] = R["deferred"], []
    for row, ids in todo:
        try:
            _landed(row, ids, svc.download(proj_dir, ids), R)
        except FlowError as e:
            row[2] = "DOWNLOAD_" + e.code
            R["ok"] = False
        except Exception as e:  # noqa: BLE001 - the spend report must still print
            row[2] = "ERROR %s: %s" % (type(e).__name__, e)
            R["ok"] = False


def _upload_all(svc, proj_dir, sel, run, a, lines, linted, R):
    """Wave mode: ONE reload of the bound project, then every ref / start frame of the lines that may
    fire - all before the first fire. Returns {tag: error code} for lines with an upload refused
    (they are not fired, nothing charged)."""
    want = {}  # path -> tags
    for shot in sel:
        tag = shot["tag"]
        if not a.reroll and _already(run, tag, a.stage):
            continue
        if a.stage == "stills":
            paths = linted[tag][1][:MAX_STILL_REFS]
        else:
            sf = proj_dir / "Elements" / "Stills" / shot.get("outName", "")
            if lines[tag]["status"] == "FAIL" or not shot.get("outName") or not sf.is_file():
                continue  # the fire loop reports LINT_FAIL / NO_START_FRAME
            paths = [sf]
        for x in paths:
            want.setdefault(str(x), []).append(tag)
    if not want:
        return {}
    err = {}
    try:
        out = svc.upload_many([Path(x) for x in want], proj_dir)
    except FlowError as e:
        if e.code in STOP_CODES:
            R["stop"] = True
        return {t: "UPLOAD_" + e.code for tags in want.values() for t in tags}
    for x, code in (out.get("errors") or {}).items():
        for t in want.get(x, []):
            err[t] = "UPLOAD_" + code
    return err



def _settle_wave(svc, proj_dir, waiting, watch, R, rows):
    """Poll Service.settle() until every line fired in this wave (QUEUED or UNCONFIRMED) is terminal or
    SETTLE_TIMEOUT_S ran out. Never fires. Results for rows an earlier wave left (watch) are landed too.
    What has not landed stays pending: its row (QUEUED -> STILL_RENDERING, UNCONFIRMED kept) moves to
    watch so a later wave's settle can still commit + download it; else flow_settle collects it."""
    end = time.monotonic() + SETTLE_TIMEOUT_S
    while waiting:
        try:
            res = svc.settle(proj_dir)
        except FlowError as e:
            print("  settle refused %s: %s - in-flight lines stay pending (run flow_settle)" % (e.code, e))
            R["ok"] = False
            if e.code in STOP_CODES:
                R["stop"] = True
            break
        except Exception as e:  # noqa: BLE001
            rows.append(["-", "settle", "ERROR %s: %s" % (type(e).__name__, e), "", "", 0])
            R["ok"] = False
            R["stop"] = True
            break
        for aid, r in (res.get("results") or {}).items():
            row = waiting.pop(aid, None) or watch.pop(aid, None)
            if row is not None:
                _finish(svc, proj_dir, row, r, R)
        if not waiting:
            _retry_deferred(svc, proj_dir, R)
        if not waiting or time.monotonic() >= end:
            break
        _sleep(SETTLE_POLL_S)
    for aid, row in waiting.items():
        if row[2] == "QUEUED":
            row[2] = "STILL_RENDERING"
        watch[aid] = row
    waiting.clear()


if __name__ == "__main__":
    sys.exit(main())
