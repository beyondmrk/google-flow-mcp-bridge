"""Shared pre-spend lints + ref resolution for Flow runs (ported from super-ai-gen fire.py preflight()).

Pure functions: no I/O beyond ref existence checks, never generates anything.
"""
import json
import re
import string
from dataclasses import dataclass
from pathlib import Path

import castlock
import panels

WPS_LO, WPS_HI = 1.8, 3.6
DUR_GRID = {"omni_flash": (4, 6, 8, 10)}
DEFAULT_DURATION = 8
# Copied verbatim from super-ai-gen/fire.py.
STATIC_WORDS = ("holds still", "does not move", "camera is static", "static camera", "locked off",
                "locked-off", "motionless camera", "camera holds")
MOVE_WORDS = ("push", "pull", "dolly", "track", "pan", "tilt", "drift", "descend", "rise", "orbit",
              "arc", "glide", "travel", "move", "creep", "zoom", "crane", "sweep")
_STATIC_RE = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in STATIC_WORDS))
# Inflected forms count ("tracks", "pushing", "arcs"): the bare-word match read an earlier action
# that says "the camera tracks slowly down the row" as naming no movement (2026-10-02). The
# leading \b still keeps "expand" from reading as "pan".
_MOVE_RE = re.compile(r"\b(?:%s)(?:s|es|d|ed|ing)?\b" % "|".join(re.escape(w) for w in MOVE_WORDS))
_PUNCT = string.punctuation + "\u2018\u2019\u201c\u201d\u2014\u2013\u2026"
NO_TEXT_RE = re.compile(r"no\s+(captions?|on-screen\s+(sentence\s+)?text|text)", re.I)
IMG_EXTS = ("png", "jpg", "jpeg", "webp")


@dataclass(frozen=True)
class Finding:
    level: str  # "FAIL" | "WARN"
    code: str
    msg: str


def _i2v(shot):
    """(obj, parse_error): parse_error is True when i2v_prompt is present but not a JSON object."""
    p = shot.get("i2v_prompt")
    if p is None or p == "":
        return {}, False
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            return {}, True
    if not isinstance(p, dict):
        return {}, True
    return p, False


def _squash(v):
    if isinstance(v, str):
        return " ".join(v.split())
    if isinstance(v, dict):
        return {k: _squash(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_squash(x) for x in v]
    return v


def _norm(v):
    """Canonical text of a voice_lock (dict / JSON string / plain string) for drift comparison."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return " ".join(v.split())
    if isinstance(v, (dict, list)):
        v = _squash(v)
        return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return " ".join(str(v or "").split())


def duration_of(shot):
    """(int seconds or None, raw). None = unparseable / non-integer."""
    raw = shot.get("duration")
    if raw is None or raw == "":
        return DEFAULT_DURATION, raw
    try:
        if isinstance(raw, bool):
            return None, raw
        if isinstance(raw, (int, float)):
            return (int(raw) if float(raw).is_integer() else None), raw
        return int(str(raw).strip()), raw
    except (ValueError, OverflowError):
        return None, raw


_duration = duration_of  # backwards-compatible alias


def _dialogue(shot, i2v):
    return (shot.get("spoken") or shot.get("dialogue") or i2v.get("dialogue") or "").strip()


def lint_shot(shot, project, stage):
    out = []
    tag = shot.get("tag", "?")
    i2v, parse_err = _i2v(shot)
    dialogue = _dialogue(shot, i2v)
    words = dialogue.split()

    if stage == "stills":
        if not NO_TEXT_RE.search(shot.get("still_prompt") or ""):
            out.append(Finding("WARN", "NO_TEXT_LOCK", "%s still prompt has no no-text/no-caption instruction" % tag))
    elif stage == "clips":
        if parse_err:
            out.append(Finding("FAIL", "I2V_PARSE", "%s i2v_prompt is not a JSON object" % tag))
        dur, raw = duration_of(shot)
        grid = DUR_GRID["omni_flash"]
        if dur is None or dur not in grid:
            out.append(Finding("FAIL", "DUR_GRID", "%s dur=%r but omni_flash accepts only %s"
                               % (tag, raw, "/".join(map(str, grid)))))
        if words and dur:
            wps = len(words) / float(dur)
            if not (WPS_LO <= wps <= WPS_HI):
                out.append(Finding("WARN", "WPS", "%s %.2f w/s (%d words in %ss) - outside %.1f-%.1f"
                                   % (tag, wps, len(words), dur, WPS_LO, WPS_HI)))
        if words:
            # The clip fires from i2v_prompt, so the lock must live INSIDE it.
            vl = i2v.get("voice_lock")
            if not _norm(vl):
                out.append(Finding("FAIL", "NO_VOICE_LOCK", "%s carries dialogue but i2v_prompt has no voice_lock" % tag))
            elif project.get("voice_lock") and _norm(vl) != _norm(project["voice_lock"]):
                out.append(Finding("FAIL", "VOICE_LOCK_DRIFT",
                                   "%s i2v voice_lock differs from project.voice_lock" % tag))
        act = str(i2v.get("action") or shot.get("action") or "").lower()
        # STATIC reads action only: camera_movement carries "locked-off" in the shipped template.
        if _STATIC_RE.search(act) and not _MOVE_RE.search(act):
            out.append(Finding("FAIL", "STATIC", "%s action holds the camera still and names no movement" % tag))

    for w in words:
        c = w.strip(_PUNCT)
        if len(c) > 1 and c.isalpha() and c.isupper():
            out.append(Finding("WARN", "ALL_CAPS", "%s ALL-CAPS word %r will be spelled out letter by letter" % (tag, c)))

    if project.get("char_ref") or shot.get("refs"):
        try:
            resolve_refs(shot, project, registry_root_for(project))
        except ValueError as e:
            out.append(Finding("FAIL", "REF_MISSING", "%s %s" % (tag, e)))
    return out


CLIENTS = Path(__file__).resolve().parent / "clients.json"
FALLBACK_REGISTRY = str(Path(__file__).resolve().parents[3] / "_REGISTRY" / "flow" / "product")


def registry_root_for(project, client="flow"):
    """ONE source of truth for the product registry: project["product_registry"], else clients.json[client]."""
    reg = project.get("product_registry")
    if reg:
        return Path(reg)
    try:
        return Path(json.loads(CLIENTS.read_text(encoding="utf-8"))[client]["product_registry"])
    except (OSError, ValueError, KeyError):
        return Path(FALLBACK_REGISTRY)


def resolve_refs(shot, project, registry_root):
    """char_ref (panel-expanded) first, then each named product ref from the registry."""
    registry_root = Path(registry_root)
    got = []
    char = project.get("char_ref")
    if char:
        angle = shot.get("angle")
        files = panels.expand(char, angle=angle)
        if not files:
            raise ValueError(panels.missing_angle_message("char_ref", char, angle))
        got += [Path(p) for p in files]
    for name in shot.get("refs") or []:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("ref %r is not a plain registry name" % (name,))
        for ext in IMG_EXTS:
            p = registry_root / ("%s.%s" % (name, ext))
            if p.is_file():
                got.append(p)
                break
        else:
            raise ValueError("ref %r not in registry %s" % (name, registry_root))
    return got


def lint_cast(project, shots, stills):
    errs, warns = castlock.lint(project.get("entities"), shots, stills, project.get("on_camera_kinds"))
    return ([Finding("FAIL", "CAST", e) for e in errs] + [Finding("WARN", "CAST", w) for w in warns])


def lint_sheets(project, all_shots, registry_root):
    """ANCHORS ARE SHEETS (REF_LOCKS.md), enforced at fire time: a recurring character fires only
    off a cropped model sheet, a product only off a cropped product sheet in the registry - never a
    flat hero still, never a generated render. Same panels and thresholds as preflight row 6."""
    from preflight import LOCK_THRESHOLD, PRODUCT_PANELS, SHEET_PANELS
    from productsheet import refuse_if_generated

    def missing(flat, need):
        d = panels.panels_dir_for(flat)
        return list(need) if not d else [n for n in need if not Path(d, n + ".png").is_file()]

    out = []
    char = project.get("char_ref")
    if char and len(all_shots) >= LOCK_THRESHOLD["character"]:
        gone = missing(char, SHEET_PANELS)
        if gone:
            out.append(Finding("FAIL", "CHAR_NO_SHEET",
                               "char_ref %s is not a cropped model sheet (missing %s) - mint it with "
                               "charsheet.py --prompt, approve it, then charsheet.py --crop"
                               % (Path(char).name, ", ".join(gone))))
    registry_root = Path(registry_root)
    products = []
    if project.get("product_ref"):
        p = Path(project["product_ref"])
        if registry_root.resolve() not in p.resolve().parents:
            out.append(Finding("FAIL", "PRODUCT_OUTSIDE_REGISTRY",
                               "product_ref %s is outside the product registry %s - copy the approved "
                               "product sheet there" % (p, registry_root)))
        products.append(p)
    for name in sorted({n for s in all_shots for n in (s.get("refs") or []) if isinstance(n, str)}):
        hit = next((registry_root / ("%s.%s" % (name, e)) for e in IMG_EXTS
                    if (registry_root / ("%s.%s" % (name, e))).is_file()), None)
        if hit:  # an unresolved name already fails REF_MISSING in resolve_refs
            products.append(hit)
    for p in products:
        try:
            refuse_if_generated(str(p))
        except ValueError as e:
            out.append(Finding("FAIL", "PRODUCT_GENERATED", str(e)))
            continue
        gone = missing(p, PRODUCT_PANELS)
        if gone:
            out.append(Finding("FAIL", "PRODUCT_NO_SHEET",
                               "product %s is not a cropped product sheet (missing %s) - recycle or "
                               "build it in the registry with productsheet.py --build / --crop"
                               % (p.name, ", ".join(gone))))
    return out
