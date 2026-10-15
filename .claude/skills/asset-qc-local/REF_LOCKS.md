# Entity locks — one reference per character, product and place

Every entity the script needs is generated **once**, approved once, imported to a stable media
ID, and referenced by that ID everywhere after. Three *kinds*; the *count per kind is driven by
the script* — a five-character script gets five character locks.

| Kind | Locks at | Why |
|---|---|---|
| CHARACTER | 2+ beats | a different-looking person per beat breaks the true-story spell |
| PRODUCT | **any** appearance | a product is never drawn from memory; the wrong box is a dead ad |
| PLACE | 2+ beats | an unlocked room drifts exactly the way an unlocked face does |

One-off characters and one-off locations stay free, so recycled footage can fill them.

**An approved anchor is not yet a lock.** A generation result is session-fragile. Upload or
import each approved anchor to the engine to mint a stable media ID, and reference only that ID
downstream — never a re-generation, never a "similar frame".

**Wardrobe:** the hero wears ONE look for the whole ad. Supporting cast may change per scene
block, and each block's outfit is stated explicitly, never left to the model.

**Reference images follow the SUBJECT of the shot, never the clip category.** Attaching a
product ref to every shot in a category once put our product's rings on a competitor's device.

## CHARACTER: the lock is a model sheet, not a hero still

One in-scene still shows the model a single angle. Every other angle it invents, and that
invention is where identity drift (profile and seated shots) and scale drift (an earlier client
rejected a batch because the product changed size between shots) come from. A character that
locks therefore carries:

| field | what it is |
|---|---|
| `anchor` | **the sheet is the anchor.** A hero still is optional context, NOT the reference the shots inherit — the headshot pass is retired |
| `sheet` | the approved MODEL SHEET - height chart, front, profile, three-quarter, seated, face closeup; one wardrobe, one light, one scale, one person |
| `panels` | the folder of per-angle crops (default `<sheet>_panels`). `front.png`, `profile.png`, `three_quarter.png`, `closeup.png` are REQUIRED |

    python charsheet.py --prompt "<who they are>" --name CHAR_X      # house sheet prompt, 16:9
    # the user approves the sheet -> import to a stable media id ->
    python charsheet.py --crop "<...>/CHAR_X_sheet.png" --out "<...>/CHAR_X"
    python charsheet.py --verify "<...>/CHAR_X"

The sheet fires 16:9 on purpose - it is a reference, not a deliverable, so the 9:16 default does
not apply to it. Panels ride along with the hero anchor on any shot whose angle they match, and
`--crop` also writes `_panels_preview.png` so the gate costs ONE read, not six.
`preflight.py --refs` FLAGs a character at or over its beat threshold with no sheet, a sheet
missing from disk, or panels that were never cut. One-off characters stay free.

**STRICT — the ANCHOR for a recurring character IS a character sheet, INSTEAD OF a headshot,
not as well as one.** Generate the sheet, crop it, and let the panels be what every shot
inherits. A hero still alone is not a lock and is never an acceptable substitute, however good
the still is. Enforced at two points, deliberately:

| when | what refuses | catches |
|---|---|---|
| declaration time | `reflocks.py` `character_check()` — **raises**, so `_reflocks.json` cannot even be written for a 2+ beat character with no `sheet` | a skill *speccing* flat anchors, before it fires them |
| Gate 1 | `preflight.py --refs` `sheet_rule()` — FLAGs a missing sheet, a sheet absent from disk, or panels never cut | a lock that was declared but never actually produced |

The declaration-time refusal exists because the Gate 1 one arrives too late: a skill that
planned hero stills has usually already generated them by the time `--refs` runs. That is not
hypothetical — on an earlier project nine flat anchors were specced and costed and only the user
caught it. `panels` is NOT refused at declaration time: both `charsheet.py --crop` and
`sheet_rule` default it to `<sheet>_panels`, and a declaration-time gate must never be stricter
than the gate it front-runs.

## PRODUCT: from the standing registry, never generated

A product that is always used lives in one folder the pipeline reads from, so nothing is
regenerated or re-derived from the client's website. The folder is per client and lives outside
any one project, so archiving a project never moves it:

    <root>/_REGISTRY/<client>/product/        <- clients.json: product_registry

A product entity's `anchor` MUST resolve inside that folder, and `source` is `registry` or
`supplied` - never `generated`. `reflocks.py` refuses to emit a generated or outside-the-registry
product at shotlist time; `preflight.py --refs` FLAGs it at Gate 1; `postflight.py` still compares
the delivered frames against `packaging_ref` by eye. When packaging changes the user drops the
new file in the registry and the old one moves to `_superseded/`.

### The product sheet — the symmetric half of the character sheet

A character locks to a model sheet because one angle means the model invents the rest. A
product has the identical failure: a product locked to a single three-quarter render produced a
"wrong product" round on an earlier client, from geometry guessed at angles that render never
showed.

So a PRODUCT carries `sheet` and `panels`, built by **`productsheet.py`**:

    python productsheet.py --build spec.json --out "<registry>/PROD_X_sheet.png"
    python productsheet.py --crop "<registry>/PROD_X_sheet.png" --out "<registry>/PROD_X_panels"
    python productsheet.py --verify "<registry>/PROD_X_panels"     # exit 1 on a missing angle

Required panels: **face · three_quarter · scale** — generic on purpose, because a plug-in
device, a tablet tube and a box all have those. Useful extras: `plugged`, `box`, `top_down`,
`profile`, `back`, `in_hand` (`scale` / `in_hand` are the answer to size drift).

**The one difference from a character sheet: a product sheet is never GENERATED.** Characters
are minted; products are photographed. Every panel is cropped from supplied stills or pulled
as a frame from the client's own real footage, and `--build` **refuses** a source under a
generated stills/clips/anchors path — the rule above has no loophole. Frames are chosen by
sharpness (Laplacian variance), a machine check, not an eye pass, and `--build` writes a
`_sources.json` beside the sheet recording every panel's origin and timestamp.

Where a footage panel and the official render disagree, **the render wins**: at footage
angles a coloured edge can bloom and read as a halo that is not on the product.

**STRICT — the ANCHOR for a product IS a product sheet, and it is RECYCLED.** The sheet lives in
the client's registry, outside any project, so the second creative for that client reuses the
first one's sheet and never rebuilds it. If the client has no sheet yet, build one there once;
from then on every project recycles it.

`reflocks.py` **raises** at shotlist time for a product with no sheet, or a sheet/panels outside
the registry; `preflight.py --refs` FLAGs the same plus a sheet missing on disk or panels never
cut. `flow_fire.py` enforces it again at fire time through `gen_lints.py` (CHAR_NO_SHEET / PRODUCT_NO_SHEET /
PRODUCT_GENERATED / PRODUCT_OUTSIDE_REGISTRY).

**Consequence, stated plainly:** a client whose registry has no sheet FLAGs until one is built.
That is the point — it is the difference between a product locked to one angle and a product the
model can actually see.

## Declaring them: `Creatives/_reflocks.json`

The skill writes this at shotlist time, mechanically — nothing here is hand-maintained.
`flow-local` derives it from the manifest `*_ref` slots; a client skill with a shotlist derives
it from the shots' `refs[]` fields.

    {
      "project": "<project folder name>",
      "client": "flow",
      "entities": [
        {"id": "CHAR_ANDRE", "kind": "character", "beats": ["S01","S04"],
         "anchor": "Elements/Stills/_anchors/andre.png",
         "locked_media_id": "<media id>", "source": "registry"}
      ]
    }

`kind` is `character | product | place`. `beats` lists the shot tags the entity appears in.
`anchor` is relative to the project root or absolute. `source` is `generated | registry |
supplied`; `registry` and `supplied` cost nothing and are reported as REUSED at the gate.

## The gate

`preflight.py` runs at start-of-gen, **before** Gate 0 — no anchor can be locked yet, so the
lock check does not run there. It runs as a **second preflight pass at Gate 1**, once anchors
are approved and imported:

    start-of-gen   preflight.py --client … --project …          (rows 1-5)
    anchors        → Gate 0 → upload/import → ids minted
    Gate 1         preflight.py --client … --project … --refs   (adds row 6)

Omitting `--refs` skips the check. A genuinely entity-free run — an abstract b-roll pack with
no recurring person, product or room — simply never gets the `--refs` pass, and
`_preflight.json` records that it was not gated on locks.

Check for an existing lock from an earlier project **before** any anchor is generated. A repeat
entity turns Gate 0 into a reconfirmation instead of a fresh approval round, which is free on
both meters.
