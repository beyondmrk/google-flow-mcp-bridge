---
name: asset-qc-local
description: >-
  Shared QA engine and generation gates for AI-generated 9:16 ad assets — the automated QA step
  behind flow-local (`qa_engine.py` + profiles: spec, every-frame scan, start/end-frame SSIM,
  audio floor, Whisper speech diff, duplicates, face identity, OCR text) plus `preflight.py` /
  `postflight.py` — the START-OF-GEN (account-vs-client, model, Creatives/Elements layout
  local+Drive, script inside) and END-OF-GEN (strict QA vs the brief, brand, packaging, existing
  Drive folder) gates — and `clients.json`; plus the older spec-only `asset_qc.py`. Use at each
  phase check-in before showing assets to the user, or standalone to sanity-check any folder of
  9:16 stills/clips (duration/truncation, aspect, resolution, stray audio, corruption). It is
  NOT an aesthetic judge and NOT a video player; creative judgment is the human check-in.
  ALSO the SPINE: `PIPELINE.md` beside this file is the ONE generation loop every client skill
  runs (preflight → refs → align → locks → Gate 1 → stills → Gate 2 → motion → QA +
  postflight → delivery → editor-notes block); client skills hold only their values.
---

# asset-qc-local — deterministic spec-QC for generated assets

> 🔴 **The generation loop lives in `PIPELINE.md`.** Twelve steps, one copy. Client skills
> (`flow-local` in this kit) point here and carry only their client values. When a step below
> and `PIPELINE.md` disagree, `PIPELINE.md` wins.

It answers the questions a human eye is bad at and a player can't show at a glance: *is every
file actually on spec?* A machine without `ffprobe` cannot check clip durations at all (one
earlier setup reported a hardcoded 8.0s for every clip), so this needs ffprobe + Pillow and runs
the real check.

## What it checks

**Clips** (`.mp4/.mov/.webm/.mkv/.m4v`), via `ffprobe`:
- **Duration** — reported exactly; flagged `TRUNCATED?` if under `--min-duration` (a failed/cut-short gen). Optional `--max-duration` flags runaways. (This is a *generation-sanity* check on the raw file — the ≤2s **screen-time** rule is an edit-time trim, not a raw-file property.)
- **Aspect** — flagged `WRONG-ASPECT` if not 9:16 (0.5625 ± 0.02). A non-9:16 clip poisons the vertical edit.
- **Resolution** — reported (w×h).
- **Audio track** — flagged `HAS-AUDIO`. Delivery rule: all b-roll ships **muted**; any clip still carrying an audio track must be stripped.

**Stills** (`.png/.jpg/.jpeg/.webp`), via Pillow:
- **Integrity** — full decode (`Image.open().load()`); flagged `CORRUPT` if it fails (PIL `.verify()` misses these).
- **Aspect** — flagged `WRONG-ASPECT` if not 9:16.
- **Resolution** — flagged `LOW-RES` if the short side is below `--min-still` (default 1024 = ~1k; masters want 2k).

## How to run

```bash
# point it at the project (walks recursively over stills + clips)
python .claude/skills/asset-qc-local/asset_qc.py "<PROJECT>" --aspect 9:16 --min-still 1024

# machine-readable
python .claude/skills/asset-qc-local/asset_qc.py "<PROJECT>" --json
```

- The script finds `ffprobe` on PATH, falling back to the winget Packages dir on Windows.
- Run with `PYTHONIOENCODING=utf-8` if a path has odd characters.
- **Exit code 1 if anything is flagged**, 0 if all clean — so it gates cleanly (`... && proceed`).
- Tune per phase: b-roll stills `--min-still 1024`, master stills `--min-still 2048`. Clips: `--max-duration` off by default (raw gens are longer than screen time); set it only if you want to catch overlong raw files.

## Where it fits

In the pipeline, run it **after each phase download, before the check-in**: read its report, fix/re-fire anything flagged (wrong aspect, corrupt, stray audio), and only then present the clean set to the user. It composes with the other QA layers:

| Layer | Catches | Tool |
|---|---|---|
| **asset-qc-local** (this) | spec: duration, aspect, resolution, audio-track, corruption | ffprobe + Pillow |
| Frame-grid eye pass | visual drift across keyframes: morph, boil, static, swapped env | a frame-grid viewer |
| Human check-in | true playback, audio, creative/brand judgment | the user's own player |

It is deterministic and cheap — run it every phase; it never judges aesthetics, so it never blocks on taste, only on spec.

---

# qa_engine.py — the shared QA engine

`asset_qc.py` above is the spec-only check and still works. **`qa_engine.py` is the superset** and
is what the client skills call: one set of checks, one report shape, and the per-client
differences (audio require vs forbid, still floor, which text field is "what should be heard",
brand notation) live in **profiles**, not in divergent scripts. A check added here lands in every
client skill at once. Shipped profiles: `flow` (flow-local manifests) and `broll` (folder mode).

## Run

```bash
# folder mode (b-roll; stills + clips stem-paired, _qa/_rejects skipped)
python <root>/.claude/skills/asset-qc-local/qa_engine.py --dir "<root>/<PROJECT>" --profile broll

# flow-local manifest (Creatives/<vertical>_<slug>.json)
python <root>/.claude/skills/asset-qc-local/qa_engine.py --manifest "<...>/Creatives/home_new_jersey.json" --profile flow
```

The engine also loads a `--shotlist <py>` for clients that keep their shot table in Python.

Options: `--layers spec,framescan,startframe,audio,speech,dupes` (default all), `--only C04a,C06`
(ids or shot groups), `--out <dir>` (default `<root>/_qa`), `--strict` (promotes the profile's
`strict_promote` codes to FAIL), `--set key=value` to override any profile key
(`--set speech.whisper_model=base`, `--set still_min_short_side=2048`), `--json`.
**Exit 1 when any finding is FAIL.** Report: `_qa/qa_report.json` (per item: findings with
severity, metrics, verdict) plus `_qa/<clip>_flagged.png` strips.

## Layers

| Layer | Catches | How |
|---|---|---|
| **spec** | aspect, resolution, fps, duration vs expected, truncation, audio present/absent per the profile's mode (`require` / `forbid` / `by_shot`), still corruption, missing/orphan clips | ffprobe + PIL |
| **framescan** | **every frame decoded** (not keyframe-sampled): whole-clip STATIC, FREEZE runs (FREEZE-MAJOR = FAIL past a profile share), SPIKE (jump cut), FLICKER, BLACK, FIRST-FRAME-GLITCH | ffmpeg rawvideo → numpy per-frame |Δ| + first→last displacement |
| **startframe** | START-FRAME-MISMATCH / DRIFT (clip frame 0 vs seed still; thresholds per engine via startframe.by_model), END-FRAME-DRIFT (last frame vs declared end still) | SSIM at 108×192 gray |
| **audio** | ALL-SILENT, LEAD-SILENCE, TAIL-SILENCE, NOISY floor, LOW-SNR, sibling LOUDNESS-SPREAD | 100 ms RMS windows + EBU R128 |
| **speech** | word-for-word diff vs the line (DROPPED-OPENER / TAIL, STUTTER, CLAIM-DROP, **NUMERIC-MISMATCH**, TRUNCATED-SPEECH, DRIFT), STRETCHED / RUSHED words-per-second, LATE-START, DEAD-TAIL | Whisper with word timestamps; both sides number-normalised ("twelve hundred fourteen dollars" == "$1,214") |
| **dupes** | DUPLICATE stills / clip first frames across different shots | 256-bit dHash, Hamming ≤ 6 |

## Calibration facts (measured on real runs, keep them)

- **STATIC needs four conditions**: per-frame mean < 1.2 AND changing-px < 1 % AND first→last
  displacement < 6 AND first/last SSIM > 0.85. A slow push-in on a locked UGC frame reads
  0.65–1.2 per-frame (looks static) but 20–35 first→last — that is motion. A batch of 13 b-rolls
  that failed on per-frame alone all turned out to be pushes. Re-confirmed on real statics,
  which read 3.4 and 2.5 first->last against 26-77 on moving clips; STATIC caught both.
- **FREEZE-MAJOR.** A same-frame run covering at least `framescan.freeze_fail_share` of the clip
  is FAIL, not the WARN a plain FREEZE is. Off by default (None); a profile may set it (0.2 has
  been used). Why: one clip froze 1.17 s at 2.83 s of a 4.0 s clip (29%, the last third dead)
  while its first->last displacement read a healthy 53, so STATIC could not catch it and FREEZE
  alone shipped as a WARN. One defect example and no measured legitimate long hold - if it starts
  failing clips an editor accepts, suspect the threshold, not the clip.
- **START-FRAME thresholds are per engine.** The default 0.5 / 0.8 pair assumes an engine that
  COPIES the seed frame (clean clips 0.95-0.99). `startframe.by_model` overrides the pair for one
  engine. An engine that RE-RENDERS from its start image (seedance_2_5 was measured) scored
  0.266-0.777 on 13 correct clips, which under 0.5 / 0.8 was 5 false FAILs and 8 false WARNs; it
  was given fail 0.2 / warn 0.25, while veo3_1_lite on the same builds scored 0.93-0.96 and kept
  the global pair. A floor with no measured wrong-frame clip behind it only catches a grossly
  wrong frame.
- **SPIKE = a real jump cut.** One flagged clip changed framing and pose between adjacent frames
  at 2.96 s → 3.00 s. Read the strip before rerolling; a spike inside the first 3 frames is the
  FIRST-FRAME-GLITCH case (editor trims it).
- **Whisper writes numbers as digits.** The engine collapses number words to digits on both
  sides, so `spoken` text and Whisper output compare equal; a *real* numeric mismatch ("$369"
  → "$300") still fails. Per-profile `digits` maps are optional extras, not required.
- Frame-grid keyframe sheets remain useful for the *eye* pass; this engine replaces them for the
  static / freeze / jump-cut question because it sees every frame.

## What the engine cannot decide

Whether a performance is good, pacing, hook strength, b-roll relevance, voice timbre, whether
a frozen-beat subject moved, an unintended talking head in b-roll, a product changing size
between shots. **Automated-clean ≠ deliverable: read the strips, then the user's own player is
the final gate.**

---

# QC regime - which law applies

Two rules pull against each other: "no automatic QC" (show the gen, no verdict, never read
pixels unless asked - image reads are expensive permanent context) and a client's demand that
every clip is strictly QA'd before it is delivered. The split is by RUN TYPE, not by mood.

**A client run is a project that went through `preflight.py`** - a `clients.json` client,
`Creatives/_preflight.json` on disk. `flow-local` runs are client runs. Everything else is not.

## Spine stages beside the QA

- `castlock.py` - one master still + one voice per on-camera character; every fire script's motion preflight refuses without it. PIPELINE.md section 4.
- QA layers: `voice` (median f0 per talking clip: `VOICE-PITCH` vs `project.vo.master`, `VOICE-DRIFT` vs the other talking clips in the same folder, 2.5 st), `CAST-OUTLIER` (a clip whose face sits off its cast group's centroid while the group holds), and a fuzzy speech diff (`fuzzy_word` 85: "flavoured" hears "flavored", "hotspots" hears "hot spots", "without" never hears "ago"). All three promote to FAIL under strict.
- `lipsync` + `objects` layers (`vision_layers.py`): MediaPipe jawOpen vs the audio envelope on every speaking clip (`LIPSYNC-LOW` under r 0.2, `NO-MOUTH`; original lip-synced clips read 0.36-0.42, voice-changed results 0.22-0.25 because the envelope changes, not the lips); YOLOv8n counts on stills + 3 clip frames: `PHONE-IN-FRAME` (unless `allow_phone`), `PRODUCT-COUNT` over the shot's product entities, `PERSON-IN-FRAME` on a `people: false` shot only when a face confirms it (hands read as person). Models in `models/` (face_landmarker.task, yolov8n.pt).
- `hf_call.py` - the Higgsfield failure path a Higgsfield-engine fire script shares: lookup before re-fire, backoff on 5xx, nsfw never re-fired, verified downloads, 503 on account status = unreachable.

## Inside a client run - strict QA, and look

- `qa_engine.py` runs on the client's profile. Exit 1 blocks delivery.
- **The eye pass is mandatory and recorded** before anything reaches the user or Drive: every
  FAIL/WARN strip, every product/packaging frame, every locked-entity frame, plus one contact
  sheet per still batch and one frame grid per clip take. Recorded via
  `postflight.py --eyes <path of a sheet/grid file you read>` (repeatable; must exist on disk, prose = exit 2) into `Creatives/_postflight.json`.
  **Nothing recorded = gate not passed, exit 2.**
- **Composite first.** Contact sheets and grids, never per-file reads of a whole batch - one
  read covering twelve stills, not twelve reads. An image read is ~34k tokens of permanent
  context; that cost is why the no-auto-QC rule exists, and compositing is how this keeps the
  client promise without paying it twelve times.

## Outside a client run - no automatic QC

Show it, no verdict, move on. Don't read pixels unless the user asks. Frame-grid and
visual/watch passes are invoked, never offered at the end of a gen.

## Both regimes

- **Showing the user a result as it lands is not QC and is never gated on it** - link or file,
  no verdict, the instant it exists. The strict pass sits at the DELIVERY boundary (Drive,
  editor notes), not in front of the user's eyes.
- Deterministic text checks (mp4 counts, exit codes, ffprobe, paths) are always on and always
  free. "Done" means one of them passed.
- A FAIL the user recorded always blocks. No-auto-QC never unblocks it.

---

# The two gates every AI-gen skill runs

Both scripts live here so every pipeline skill shares one implementation. **Nothing fires
before the START gate is clear; nothing is delivered before the END gate is answered.**

## START-OF-GEN — `preflight.py`

```bash
python <root>/.claude/skills/asset-qc-local/preflight.py --client <client> --project "<project root>" --script "<script/brief file>" --model <still model> --model <motion model> [--drive-folder-id <id> | --drive-parent-id <id>] [--no-drive] [--migrate]
```

`<client>` is a key in `clients.json`; `flow` is the one shipped entry. Another client = another
`clients.json` entry + a profile in `profiles/`.

| # | The question | What the script does |
|---|---|---|
| 1 | Which engine — Flow or Higgsfield? | reads `clients.json` → engine for this client |
| 2 | Is the signed-in account the client's? | Higgsfield CLI engine: runs `higgsfield account status`, compares the signed-in email + workspace to the client's. **Mismatch = FLAG, exit 2, do not fire.** Flow and MCP engines get an explicit manual assertion row instead |
| 3 | Which model? | `--model` values checked against the client's `models_allowed`; anything else FLAGs until the user confirms |
| 4 | Creatives + Elements exist locally AND on Drive, script inside? | creates `Creatives/`, `Elements/Stills/`, `Elements/Clips/` locally and (via rclone, by folder id) on Drive; copies the `--script` into `Creatives/` and uploads it if Drive lacks one. Finds an existing Drive project folder by name under `--drive-parent-id` / the client's `drive_root_id` before ever creating one |
| 5 | Elements holds ONLY Stills + Clips | anything else under `Elements/` (Graphics, Footage, Prompts…) is a FLAG; `--migrate` moves a legacy layout into place |
| 6 | Is every recurring entity locked? | `--refs` at Gate 1: a CHARACTER needs its model sheet + cropped panels, a PRODUCT must come from the client's `product_registry`. See REF_LOCKS.md |
| 7 | Was the inspo / reference WATCHED before anything started? | scans `--script` for links and clip paths; each one needs a watched record with real notes in `Creatives/_references.json` (`refwatch.py --record`). A reference nobody can describe was not watched |
| 8 | Does the shot table still say what the SCRIPT says? | `--align <manifest\|shotlist\|dir>` at Gate 1 (`scriptalign.py`): DROPPED / INVENTED / REORDERED FLAG, PARAPHRASED is reported and left to the editor |

## The reference is the spec; the script is the contract

**Watch it first.** A reference in a brief is a spec, not decoration, and a video reference is not
"opened" - it is WATCHED (a frame grid, or played) BEFORE the shotlist exists. Then
`refwatch.py --record` takes what it actually does: its beats, its cut rhythm, what we take and
what we leave. An earlier run burned 304 credits in the wrong treatment because that never
happened. Row 7 refuses a brief whose references nobody watched, and refuses a note too thin to
describe one.

**Then keep the shots honest to the script.** `scriptalign.py` compares the script sentence by
sentence against the shot table's spoken lines, in order. Whisper only ever proves a clip said
what the MANIFEST asked - this is the only check that the manifest still says what the SCRIPT
said. Alignment means the script's own words: a rewrite that reads better is still reported, so
the editor decides whether it ships.

Writes `Creatives/_preflight.json`. **Exit 2 = FLAG → show the user the flagged rows and stop.**

## END-OF-GEN — `postflight.py`

```bash
python <root>/.claude/skills/asset-qc-local/postflight.py --client <client> (--manifest <json> | --shotlist <py> | --dir <root>) [--drive-folder-id <id>] [--lenient] --eyes "<Creatives/.../contact_sheet_01.png>" [--eyes "<_qa/grids/C03_v1.png>" ...]   # FILE PATHS that exist; prose = exit 2
```

| # | The question | What the script does / what you must do |
|---|---|---|
| 1 | Strict QA — does it match what was asked? | runs `qa_engine.py` with the client's profile, then prints a **brief-compliance table** (shot → asked → delivered → call). Fill every `call` with MATCH or FLAG(reason) after the eye pass; a blank is not an answer |
| 2 | Brand guidelines followed? | prints the client's `brand_rules` from `clients.json` and the `brand_doc` path — answer each rule for THIS batch, in the report to the user |
| 3 | Correct packaging? | prints `packaging_rule` + the reference folder; compare every product/packaging frame against it by eye. **Garbled label text from the `text` layer = the model re-typeset the packaging = FLAG** |
| 4 | Existing Drive folder to upload into? | looks the project folder up by name under the client root / `--drive-parent-id`; reports the id and what is already there. Unresolved = exit 2, ask the user before creating anything |
| 5 | Did you actually LOOK? | records the eye pass from `--eyes` (contact sheets, frame grids, QA strips, packaging frames). **Nothing recorded = gate not passed, exit 2** - automated-clean is not deliverable. See *QC regime* above |

Writes `Creatives/_postflight.json`.

## Canonical project layout (both local and Drive)

```
<PROJECT>/
  Creatives/          the script/brief, the manifest or shotlist, dialogue md, editor notes,
                      _preflight.json / _postflight.json, QA report copies
  Elements/
    Stills/           EVERY still — anchors (_anchors/), refs (_refs/), generated, recycled
    Clips/            EVERY clip — generated, recycled, HUD/ and Screen/ lanes, _rejects/
    Audio/            VO / music tracks
```

Nothing else under `Elements/`. Legacy folders (`Elements/Graphics`, `Elements/Footage/<model>`,
`Elements/Prompts`, `02_REFERENCE_STILLS`, `03_MOTION_CLIPS`, `01_ANCHORS`, `02_STILLS/S1`…) are still
READ by `qa_engine.py` so old projects resume, but every NEW project starts canonical and
`preflight.py --migrate` converts an old one.

## Delivered layout and clip names — every generation skill

What lands on the client Drive project folder is fixed, whatever skill made it. Local working
folders may keep lanes, `_rejects/` and internal names; the DELIVERY may not.

```
<Drive project folder>/
  Creatives/         brief/script · edit notes PDF · one cut-sheet PDF per hook
  Elements/Stills/   _anchors/ · _refs/ · generated stills
  Elements/Clips/    ONE FLAT FOLDER, videos only, names sort in cut order
  Elements/Audio/    VO / music (optional)
```

| Clip | Name | Example |
|---|---|---|
| Hook opener | `H` + hook letter + `_` + order in the hook | `HA_1.mp4`, `HA_2.mp4`, `HB_1.mp4` |
| Body (generated OR free/recycled footage) | `S` + 2-digit section + `_` + position in the section | `S01_01.mp4`, `S11_18.mp4` |
| More than one take of a slot | add `_v<k>` | `S03_02_v1.mp4`, `S03_02_v2.mp4` |

Alphabetical order IS the edit: a hook's openers, then `S01_01` onward. Shot ids and `SRC_`
never appear in delivered names; the cut sheet maps each file back to its production id. No
hook sub-folders and no numbered `000_` copies — parallel `rclone copyto` into hook folders made
duplicate same-name folders on Drive, and editors found that layout confusing.

**Enforcement — `delivery_names.py`:** `plan(items)` returns the delivered name for every clip
(pass items in cut order: `{key, hook}` or `{key, sec}`, optional `slot` + `take`), refusing
duplicates. After upload, `python delivery_names.py --drive-folder-id <id>` lists Drive and
FAILs on a folder inside `Elements/Clips`, a non-video, a bad name, a gap in a hook or section
sequence, anything under `Elements/` other than Stills/Clips/Audio, or reject/bake-off/QA
folders. Upload Clips with `rclone sync --track-renames` from the flat stage so stale names and
old folders go to Drive trash and renames happen server-side.

## Two more engine layers

| Layer | Catches | How |
|---|---|---|
| **identity** | `IDENTITY-MISMATCH` (FAIL) / `IDENTITY-DRIFT` (WARN) / `NO-FACE` (WARN, speaking shots) — the person in a still or clip is not the locked anchor | OpenCV YuNet + SFace (`models/`), cosine vs the anchor: flow `project.char_ref`, broll `--set identity.ref=<path>`. Measured: same person 0.77–0.93, different people 0.03–0.17; one clip that scored 0.26 really did cut to another man |
| **text** | `TEXT-FOUND` (WARN; `--strict` → FAIL) — burned-in captions, badges, hallucinated labels | RapidOCR on stills + 4 sampled frames; the profile's `allow` regexes plus fuzzy matching (≥0.6) so a real label misread at 720p passes while a caption or invented text is queued. A still is judged at OCR conf ≥0.6, a clip frame at ≥0.8 |
