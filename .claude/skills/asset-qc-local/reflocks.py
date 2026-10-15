#!/usr/bin/env python3
"""Emit Creatives/_reflocks.json - the entity lock table preflight.py --refs checks.

The entity list is authored once per project (who recurs is a creative decision); `beats`
is derived from the shot list, because that is the half that goes stale by hand.

    python reflocks.py --manifest "<project>/Creatives/<manifest>.json" --client flow

See REF_LOCKS.md for the doctrine and the schema.
"""
import argparse, json, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CARRY = ("id", "kind", "anchor", "locked_media_id", "source", "sheet", "panels")



def product_check(e, registry):
    """Refuse at declaration time what is already knowable: a generated product, or a product
    anchored outside the client's standing registry folder. See REF_LOCKS.md."""
    eid = e.get("id", "?")
    if (e.get("source") or "").strip().lower() == "generated":
        raise ValueError("%s is a PRODUCT with source=generated - products are never generated, "
                         "they come from the client's product registry" % eid)
    if not registry:
        return
    anchor = (e.get("anchor") or "").strip()
    if not anchor:
        raise ValueError("%s is a PRODUCT with no anchor - point it at a file in %s" % (eid, registry))
    ap = os.path.abspath(anchor)
    root = os.path.normcase(os.path.abspath(registry)) + os.sep
    if not os.path.normcase(ap).startswith(root):
        raise ValueError("%s anchors outside the product registry: %s - copy the approved "
                         "packaging into %s and declare that path" % (eid, anchor, registry))
    # A product MAY also carry a sheet + panels (productsheet.py) - the symmetric answer to
    # the character sheet, for the angles one render never shows. Optional, because existing
    # clients predate it; but if declared it lives in the registry like everything else, and
    # it is ASSEMBLED from supplied material, never generated. See REF_LOCKS.md.
    # STRICT since 2026-09-21: a product's anchor is its SHEET, exactly as a character's is.
    # RECYCLE FIRST - the registry sheet is shared across every project for that client, so a
    # second creative never rebuilds it. If the client has none yet, build one with
    # productsheet.py from supplied photography; it is never generated.
    sheet = (e.get("sheet") or "").strip()
    if not sheet:
        raise ValueError(
            "%s is a PRODUCT with NO product sheet - the anchor for a product IS a product "
            "sheet (face, three_quarter, scale), recycled from %s when one already exists, "
            "or built there with productsheet.py --build from supplied photography when it "
            "does not. Products are never generated. See REF_LOCKS.md." % (eid, registry))
    for field in ("sheet", "panels"):
        val = (e.get(field) or "").strip()
        if not val:
            continue
        vp = os.path.normcase(os.path.abspath(val))
        if not vp.startswith(root):
            raise ValueError("%s declares a product %s outside the registry: %s - a product "
                             "sheet is registry material like its anchor" % (eid, field, val))


CHARACTER_LOCK_THRESHOLD = 2   # beats; matches preflight.py LOCK_THRESHOLD["character"]


def character_check(e, beats):
    """A CHARACTER lock is a MODEL SHEET plus its cropped panels, NEVER a lone hero still
    (the user, 2026-09-21: "Yes. Strictly."). One in-scene still shows the model a single angle;
    every other angle it invents, and that invention is where identity drift and scale drift
    come from. Refuse at DECLARATION time - preflight --refs also FLAGs this at Gate 1, but by
    then a skill has often already specced and fired flat anchors. See REF_LOCKS.md.

    One-off characters (under the beat threshold) stay free and are not checked."""
    eid = e.get("id", "?")
    if len(beats) < CHARACTER_LOCK_THRESHOLD:
        return
    sheet = (e.get("sheet") or "").strip()
    if not sheet:
        raise ValueError(
            "%s is a CHARACTER in %d beats with NO model sheet - the ANCHOR for a recurring "
            "character IS the model sheet + cropped panels, INSTEAD OF a headshot, not as well "
            "as one (the user, 2026-09-21). Mint it with "
            "charsheet.py --prompt \"<who they are>\" --name %s, get the user's approval, cut the "
            "panels with --crop, then declare 'sheet' and 'panels' on the lock." % (eid, len(beats), eid))
    # panels: NOT refused here. charsheet.py --crop and preflight.py sheet_rule both default
    # them to "<sheet>_panels", and a declaration-time gate must not be stricter than the
    # Gate 1 gate it front-runs. preflight --refs checks the cut panels exist on disk.


def emit(entities, shots, project, client, registry=None):
    """Build the lock table. Raises KeyError if a shot names an entity nobody declared."""
    declared = {e["id"]: e for e in entities}
    beats = {eid: [] for eid in declared}
    for s in shots:
        for eid in s.get("entities", []) or []:
            if eid not in declared:
                raise KeyError("shot %s names undeclared entity %r - declare it in "
                               "project.entities or fix the typo" % (s.get("tag", "?"), eid))
            beats[eid].append(s.get("tag", "?"))
    out = []
    for eid, e in declared.items():
        kind = (e.get("kind") or "").strip().lower()
        if kind == "product":
            product_check(e, registry)
        elif kind == "character":
            character_check(e, beats[eid])
        row = {k: e.get(k) for k in CARRY}
        row["beats"] = beats[eid]
        out.append(row)
    return {"project": project, "client": client, "entities": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="the project manifest holding project.entities and shots")
    ap.add_argument("--client", required=True, help="key in clients.json")
    ap.add_argument("--out", default=None, help="default: <project root>/Creatives/_reflocks.json")
    a = ap.parse_args()

    with open(a.manifest, encoding="utf-8") as f:
        m = json.load(f)
    root = m.get("project", {}).get("root") or os.path.dirname(os.path.dirname(os.path.abspath(a.manifest)))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "clients.json"),
              encoding="utf-8") as f:
        registry = (json.load(f)[a.client].get("product_registry") or "") or None
    tbl = emit(m.get("project", {}).get("entities", []), m.get("shots", []),
               os.path.basename(root.rstrip("\\/")), a.client, registry)
    out = a.out or os.path.join(root, "Creatives", "_reflocks.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tbl, f, indent=1, ensure_ascii=False)
    print("wrote %s - %d entity(ies)" % (out, len(tbl["entities"])))
    for e in tbl["entities"]:
        print("  %-8s %-16s %d beat(s)  %s" % (e["kind"], e["id"], len(e["beats"]),
                                               e["locked_media_id"] or "NO LOCK"))


if __name__ == "__main__":
    main()
