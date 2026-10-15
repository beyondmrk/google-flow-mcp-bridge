---
name: flow-local
description: >-
  Turn a prebuilt `shots[]` manifest (one entry per script line) into one 9:16 talking clip per
  line, generated entirely inside Google Flow on the user's own Google Ultra account through the
  `flow` MCP bridge (a dedicated Edge window) — free Nano Banana 2 stills, paid Google Omni Flash
  motion (12 credits per clip), two approval gates. Use whenever the user says "fire this in
  Flow", "flow-local", "run this through Flow" / "use my Ultra", hands over a script or manifest
  and wants it generated on their own Ultra allowance, or asks to resume/repair a half-finished
  Flow run via `_flow_run.json`. NOT for writing the script, NOT for other generation services,
  and it never reads or writes outside the bridge's `work_root`.
---

# flow-local — Google Flow / Omni generation (local)

> 🔴 **This skill runs the spine: `<root>/.claude/skills/asset-qc-local/PIPELINE.md`.** Read it first. Everything below is Flow's VALUES and craft on top of it; the loop itself is not repeated here. Flow's own in-app "Preflight" section is Flow-specific and runs AFTER spine step 1.

`<root>` = the folder holding `.claude/` (the clone root).

## Values for the spine

| Slot | Flow value |
|---|---|
| client | `flow` (preflight: `--model nano_banana_2 --model google_omni`) |
| account | Dedicated Edge window launched by the `flow` MCP (own profile `<root>/mcp/flow/.edge-profile`), the user's Ultra login — first run they sign in by hand; `LOGIN_REQUIRED` / `CAPTCHA` → stop, ask the user. Flow project name = folder name |
| stills | Nano Banana 2, Image mode, **0 credits**, 9:16 enforced, lands 768×1376 PNG |
| motion | Google Omni Flash only, **12 credits per clip**, count ≤ 2 (`abra_r2v_*` Ingredients, `abra_i2v_*` Frames); Veo Lite rejected by the bridge until its label is verified |
| QA | `qa_engine.py --manifest "<Creatives/<name>.json>" --profile flow`, then Layers 1-3 (*QA* below) |
| postflight | `--manifest "<Creatives/<name>.json>"`, `--drive-folder-id <id>` or `--no-drive` |
| project path | `<root>/<project folder>` (inside the bridge's `work_root`) |
| manifest | `Creatives/<name>.json` |
| char_ref | model sheet + cropped panels in a LOCAL folder; pick the panel matching the shot angle |
| delivery | confirm cut order with the user → `deliver_stage.py --manifest <m> map → stage → sheet → upload` (gated on `_postflight.json` qa_exit 0) → `VERIFY: PASS` |

**Gate mapping.** Manifest review + dry-run math = spine step 5. Flow's **Gate 1** (still
approval) = spine step 7. Flow's **Gate 2** (clip approval, re-rolls cost credits) is a Flow
addition between spine steps 8 and 9.

One line: **free stills, paid motion, two gates.**

## Hard rules — read these first

**The bridge only touches paths inside its `work_root`.** `mcp/flow/config.toml` sets `work_root`
(every path handed to a tool must live under it) and `denied_roots` (paths always refused, e.g. a
shared network drive). Anything else returns `PATH_REFUSED`. Character refs must already exist in
a **local** folder under `work_root` (e.g. `<root>/refs/`) that the user populates themselves — a
deliberate act of theirs, never something this skill does. A `char_ref` on a network drive or
outside `work_root` = stop and ask the user for a local copy. During a run this skill touches only
`<root>/...` and the Flow tab in the dedicated Edge window.

**Never press `Ctrl+A` in the Flow page** (manual fallback only) - it selects all project media and a following `Delete` trashes every clip (2026-08-23, 12 clips); click the toast's **Undo** at once. See `flow_snippets.md`.

Also hard: reference attached **by name** every fire (the bridge does it) · act on every returned status, never re-fire on `UNCONFIRMED` · `CAPTCHA` / `LOGIN_REQUIRED` = stop and ask the user · `OUT_OF_CREDITS` = **never buy credits**.

## Scope

**Owns:** the Flow browser data plane end to end — resolve the character ref, upload it, fire
stills, download, gate, fire clips, download, gate, QA, report.

**Does not own:** script writing (a script-writing skill produces the lines; this skill fires a
manifest built from them) · other generation services (flow-local is Ultra-only) · anything
outside `work_root`.

## Files in this skill

```
.claude/skills/flow-local/
  SKILL.md              # this file - the procedure (Flow is driven by the `flow` MCP bridge)
  flow_fire.py          # manifest-driven fire script (lint stamp, sheet gate, waves, downloads)
  examples/manifest.example.json   # a minimal 2-shot manifest flow_fire.py accepts
  flow_snippets.md, refserver.py, flow_recv.py   # MANUAL FALLBACK ONLY (bridge down)
```

The bridge is the MCP server `flow` (`<root>/mcp/flow`, registered in `<root>/.mcp.json`).
`flow_snippets.md`, `refserver.py` and `flow_recv.py` are the manual fallback only - use them
solely when the bridge is down.

## Entry point — a prebuilt `shots[]` manifest

The only entry: a manifest at `Creatives/<name>.json` inside the project folder, written from the
user's script (one shot per spoken line) or left by a partial run. A legacy `Elements/Prompts/`
run converts with `preflight.py --migrate`. Shape (full example:
[`examples/manifest.example.json`](examples/manifest.example.json)):

```jsonc
{ "project": { "slug": "lighthouse_coffee", "client": "flow",
               "char_ref": "Elements/Stills/_refs/CHAR_MASCOT.png" },  // needs CHAR_MASCOT_panels/ beside it
  "shots": [ { "tag": "L01", "duration": 6, "clipName": "LH_line01", "outName": "claude_lighthouse_coffee_line01.png",
               "spoken": "<the line as it is heard>", "still_prompt": "<... no captions, no on-screen text>",
               "i2v_prompt": { "dialogue": "<verbatim line>", "voice_lock": "<identical on every clip>", "action": "<beat + movement>" },
               "refs": [], "angle": "front" } ] }   // refs / angle optional
```

`project.char_ref` is **manifest-level** (Preflight step 5 reads it, never a per-shot value) and
must be **local** (Hard rules). `spoken` is what QA diffs Whisper against: write numbers the way
they are said. `outName` / `clipName` set the local file names.

### Review gate (free) and dry-run

Before Preflight fires anything, **show the manifest as a table** and wait for the user — it costs
nothing and catches typos before they're baked into every prompt:

| tag | dialogue | alts | outName | clipName |
|---|---|---|---|---|
| L01 | Hi, I'm Beacon, and the coffee here is worth the climb. | 1 | claude_lighthouse_coffee_line01.png | LH_line01 |
| … | … | … | … | … |

Flag anything wrong (a misspelled name, a figure that doesn't match the script). **Dry run** (the
user asks for one, or just wants the math): manifest + review gate + Preflight, then **stop** —
report the balance math and asserted refs, fire nothing. Resume at Stage 1 when they say go.

## Preflight - Flow in-app (fires nothing; runs after spine step 1)

Spine steps 2-4 -> `PIPELINE.md`; `clients.json` already has the `flow` entry: `preflight.py --client flow --project <dir> --model nano_banana_2 --model google_omni --refs --align <manifest> --no-drive`. Once per project, before Stage 1, in order:

1. **Bridge up.** `flow_status`: `signed_in` true, no `captcha`, not `out_of_credits`. First run ever, the user signs in by hand in the dedicated window; `LOGIN_REQUIRED` / `CAPTCHA` -> stop and ask them.
2. **Per-creative Flow project.** `flow_open_project(name=<slug>, project_dir=<project>)` - **one per creative** (the asset grid degrades as media piles up); it binds the project's `_flow_run.json`.
3. **Balance.** `flow_status` credits (`None` until Flow's ~30 s poll lands - call again). Record it.
4. **State the math.** `shots.length x 12 x count` for Omni Flash - motion only, stills are free - vs. the balance.
5. **Assert `project.char_ref` exists locally** - a panel from the character's model sheet (-> spine step 4), the one matching the shot's angle. Confirm the file is on disk and inside `work_root`; missing or outside -> stop, ask the user. Copy it into `Elements/Stills/_refs/`. **Sheet gate (fire time, mandated):** `flow_fire.py` refuses `CHAR_NO_SHEET` when a recurring `char_ref` has no cropped sheet beside it (`<name>_panels/`: front, profile, three_quarter, closeup - `charsheet.py`), and `PRODUCT_NO_SHEET` / `PRODUCT_GENERATED` / `PRODUCT_OUTSIDE_REGISTRY` for a product that is not a cropped product sheet (face, three_quarter, scale - `productsheet.py`) in `_REGISTRY/flow/product/`. Products are never generated. **No master ref yet?** Step 0, not a blocker: generate candidates in Image mode (free), the user approves one, cut its sheet with `charsheet.py`, save it there, continue.
6. **Paths.** Every path handed to a tool lives under `<root>/<project>`; anything else is `PATH_REFUSED`. **Dry run stops here.**

## Stage 1 - stills, 0 credits (spine step 6)

1. Dispatch the **`flow-runner`** subagent (`.claude/agents/flow-runner.md`) with: project dir (project bound via `flow_open_project`, preflight step 2), stage `stills`, the approved line list, budget `0`. Still prompts stay caption-free (no on-screen text). It runs `flow_fire.py --stage stills` (lint stamp, ref upload and attach by name, Image mode, Nano Banana 2, **9:16**, `outputs` = `shot.alts`, download to `Elements/Stills/`, skip files on disk) then `qa_engine.py --profile flow`. Lint rules live in `gen_lints.py`; `flow_fire.py` enforces them, this file does not restate them. First of two chained locks: the char-ref pins identity into every still, the approved still pins composition into its clip. Flow's Character `@`-tag is **not** a substitute (2/8 identity matches).
2. **Act on its report** (*Error handling*): `PASS` -> Gate 1. `UNCONFIRMED` / `NO_MEDIA_URL` / `WRONG_ASPECT` -> report, never re-fire.

   **Naming (local stills only):** no `_v<VV>` suffix on stills (clips have one). The first
   candidate for a line takes the unversioned name; every further candidate (multi-alt outputs,
   re-rolls) lands as `_altN`, next free `N`, never reused:
   ```
   Elements/Stills/claude_<slug>_line<NN>.png        # first candidate - provisional canonical
   Elements/Stills/claude_<slug>_line<NN>_alt2.png   # second+ candidate (hero alt or re-roll)
   ```
   The unversioned name is *provisional* until Gate 1 makes it mean "approved." Record each
   candidate's `mediaId`, slot (`canonical`/`altN`) and download status in `_flow_run.json`.
3. Repeat until every line has its candidate(s) in `Elements/Stills/`.

Rerolls are free; a defect that survives several rerolls means **rephrase**, not re-run verbatim.

### Gate 1 — stills (spine step 7)

`SendUserFile` the stills (canonical + alts) to the user. Approval is **per line** — "approve 9,
re-roll 2" is normal. **Picking is a rename, not just a flag:**
- The user approves the file in the canonical name → nothing moves; mark `picked: true`.
- The user picks an alt → **rename it** to `claude_<slug>_line<NN>.png`, demote the previous holder to
  the next free `_altN`. Exactly one file per line holds the canonical name — the one Stage 2
  re-uploads.
- Re-roll → fresh candidate (free), Stage 1 for that line only, lands as the next `_altN`.
- Only picked lines advance to Stage 2.

## Stage 2 — motion, 12 credits/clip Omni Flash (spine step 8)

### Engine: Google Omni Flash

Every motion fire: **Video → Ingredients or Frames → 9:16 → Omni Flash → x1**, reference attached
as the ingredient or start frame. Committed-clip model id: `abra_r2v_8s` (Ingredients /
reference-to-video); `abra_i2v_8s` is Frames.

**Ingredients vs Frames.** Frames opens every clip on its seed frame, so seeding every clip from
the SAME image resets each cut to one neutral pose. **Per-line approved stills on hand → Frames**,
each clip seeded from its line's canonical still (frame-lock without the identical open); **only
one master ref → Ingredients**. Pass `mode="frames"` or `"ingredients"` to match.

**Seed every clip from the SAME pristine reference — never from another clip's output.**
Frame-chaining compounds re-encode/re-generate loss into smeared skin and blown-out hair by the
back half; ref-seeding keeps segment 16 as clean as segment 1. Voice continuity comes from the
verbatim `voice_lock`, not chaining. **Extend is unavailable on Omni** (*"Only Veo-generated
videos can be extended."*) — don't plan around it.

**The bridge attaches the start frame by name and checks the committed clip's model** (`abra_r2v_*` Ingredients, `abra_i2v_*` Frames; the name encodes duration). A wrong source (`abra_edit`, from attaching a generated clip) returns `WRONG_MODEL` - report it, never accept it.

### Duration must match the line — the 8s default is WRONG on Omni

"Always 8s, same cost as 6s" was Veo doctrine. On Omni a mismatch corrupts the read:

| Line vs. duration | What Omni does | Fix |
|---|---|---|
| Line much SHORTER than the clip | pads by **repeating words**, brand names included (`"the Acme **Acme** Network"`, `"tap below, **below, pep**"`) | drop to 6s, or 4s for very short lines |
| Line much LONGER than the clip | **compresses** — paraphrases and silently drops words (a 17-word line came back as 4 words, twice) | raise to 10s, or **split the line into two short clips** |

Prompt wording fixes neither (`"do not repeat any word"`, `"do not shorten the line"`, `"say it
exactly once"` all fail). Estimate spoken length, pick 4/6/8/10s, split what won't land at 10s.
Ragged lengths are fine. Word count is the pace dial: overfill speeds the voice (and drops
words), underfill drags.

### Prompt shape for realistic talking heads

A JSON `i2v_prompt` that reads as a real phone recording:

```jsonc
{"shot":"real handheld iPhone front-camera selfie video, she is holding the phone herself at arm's length the entire time so one arm stays visibly extended toward the lens, vertical 9:16, tight chest-up framing, constant small natural handheld wobble",
 "subject":"the exact same <person> from the reference image, same face and identity, <hair>, <wardrobe>, <location>",
 "action":"<beat> Her phone-holding arm never leaves frame and her other hand stays out of frame. She is not holding any object other than the phone she is filming with.",
 "dialogue":"<verbatim line>",
 "voice_lock":"ordinary American <woman/man> in <their> forties recording <themselves> on <their> phone, warm mid-range voice, relaxed conversational pace, natural breaths and small imperfect hesitations, slightly off-the-cuff like <they are> thinking as <they> talk, NOT a voice actor, no commercial read, no announcer energy, no perfect diction, no upbeat sales tone",
 "style":"unedited real smartphone video, ordinary flat indoor daylight, deep focus, visible skin texture and fine lines, mild sensor noise, looks like genuine user generated content",
 "negative":"no overacting, no exaggerated expressions, no theatrical eyebrow raises, no big hand gestures, no counting on fingers, no props, no second phone, no captions, no subtitles, no on-screen text, no watermarks, no logos, no music"}
```

What each part buys: **handheld + arm extended** — tripod-locked reads as AI; the extended arm
is the strongest realism cue and occupies the free hand so props stop appearing · **tight
chest-up** — wide reads staged · **`voice_lock` as an anti-brief** — a polished voice is the
quickest AI giveaway · **anti-overacting negatives** · **`no second phone`** —
contradictory-fields trap, `flow_snippets.md`.

### Reaction / watch beats

Stitch-react: the reactor is silent while the source video plays over the top. Write them as
*listening*, never *"watching her phone"* (spawns a second phone), and lock silence in four
fields at once — no-speech trap, `flow_snippets.md`. Verify with `ffmpeg -af volumedetect`:
genuinely silent lands around **-45 to -60 dB mean**. Omni always writes an AAC track, so "has
audio" proves nothing.

### Output spec and the fire loop

Output (locked): raw **720×1280 / 24fps, audio kept**; 1080×1920 is edit-side. **Download the
720p original — never Flow's 1080p upscale**; upscaling is the editor's pass.

State the math (`approved_lines × 12 × count`) vs. balance before firing. Credits are the user's
personal Ultra pool — report spend, don't ration it; if a rejection means re-firing, say so and
let them decide, never silently skip a re-roll.

**The re-upload is the key move.** The grid reorders as media lands and its search matches only
the start of Flow's auto-title, not prompt text. Re-uploading the **approved still from disk**
guarantees the bytes approved at Gate 1 are the bytes animated.

Dispatch **`flow-runner`** only on the user's Gate-1 go and their credit budget, with project dir (bound via `flow_open_project`, preflight step 2), stage `clips`, the Gate-1-approved line list and the credit budget (`lines x 12 x count`; refuses without it). It runs `flow_fire.py --stage clips` (`--count 2` only when the user asks for two takes per line), which re-uploads each canonical Gate-1 still, fires `mode="frames"` on `omni_flash` with the manifest's `i2v_prompt` pasted verbatim (`dialogue` performed by the model; `voice_lock` byte-identical across a character's clips, never edited per clip; VO-dubbed projects have neither), checks the committed model, and downloads to:
```
<root>/<project>/Elements/Clips/<clipName>_v<VV>.mp4
```
Paid clips are hard-gated by `gen_lints.py` (`duration_of` / `DUR_GRID` 4-6-8-10 s / `WPS` 1.8-3.6 words per second; stamp in `Creatives/_flow_lint.json`); the duration table above is the rule it enforces; Veo Lite is rejected until its label is verified. Local working name; delivery renames shipped takes. First take `_v01`, every re-roll the next free version, never overwritten; a line that already holds a committed clip refuses `ALREADY_COMMITTED` unless `--reroll` (the user's go only - a re-roll costs credits). The lint stamp binds the prompt only (manifest sha + per-line `prompt_sha256`): start frame, duration and count are not stamp-bound. Act on the report (*Error handling*). **Manual / recovery only:** the same bridge tools (`flow_status`, `flow_fire_still`, `flow_fire_clip`, `flow_sweep`, `flow_download`) can be driven directly when the runner is unavailable (pass `project_dir` to `flow_upload` so the shared tab is on the bound project; `flow_fire_clip` takes `reroll=True` only on the user's go); firing stays user-approved either way.

**Waves.** `flow_fire.py --wave N` (max 6; stills default 4; **clips default 1** - `--wave > 1` for clips also needs `--clip-waves-verified`, and the first paid clip run is `--wave 1 --count 1`) uploads every ref / start frame first (one reload), then fires up to N lines queued. A fire counts only on Flow-side evidence (a NEW pending tile with a progress badge, or its media record); none -> `UNCONFIRMED` (charged, never re-fired) and the run stops firing. 12 x count is charged at the click, budget checked before every fire. Nothing navigates the shared tab while any project has queued jobs rendering (tab-wide marker `_tab_inflight.json`): the next fire is prepared in place, `flow_settle` pumps on the page and reloads at most once, only when the last fire is ~90 s old AND no pending badge shows; `flow_sweep` / `flow_upload` / `flow_open_project` refuse `IN_FLIGHT` meanwhile, and a download needing the detail view is retried later (`DOWNLOAD_DEFERRED` if still waiting; `DOWNLOAD_NO_MEDIA_URL` = no signed URL, never a Download-button click). Lines not landed after ~10 min are `STILL_RENDERING` (pending; `flow_settle` later). A queued fire Flow never lists within 15 min (after that idle reload, no badge on screen) becomes `LOST` (spend unknown; media that land late are still claimed by it): the line refuses `LOST_PENDING_RELEASE` (re-run reports `LOST`) until the user, after checking the Flow grid, hand-edits that attempt's `status` from `lost` to `not_fired` in `_flow_run.json` - or `--reroll` on their go with a changed prompt. A prompt matching a pending or lost queued fire is refused `SAME_PROMPT_PENDING` (no click, no charge).

**First paid clip through the bridge = a verification step.** The 0-credit smoke never proved clip-side behavior (kind, model, download). After it lands, confirm model, 720p original and the file on disk with the user and flag it in the report before firing the rest.

### Gate 2 — clips (Flow addition, before spine step 9)

Per line like Gate 1, but re-rolls cost 12 credits — confirm with the user before re-firing. The
approved take is marked `picked: true` in `_flow_run.json`; delivery ships exactly the picked takes.

## `_flow_run.json` — run state

Updated after every fire and download, so a half-dead browser session resumes instead of
re-paying. Clips: fire-time `_vNN`. Stills: **slot/pick** tracking — one canonical unversioned
file per line plus `_altN`, reassignable by a Gate-1 pick.

```jsonc
{ "projectId": "...", "flowProjectName": "...",
  "lines": { "L01": { "still": {"candidates": [
               {"slot": "canonical", "mediaId": "...", "file": "claude_lighthouse_coffee_line01.png", "downloaded": true, "picked": true},
               {"slot": "alt2", "mediaId": "...", "file": "claude_lighthouse_coffee_line01_alt2.png", "downloaded": true, "picked": false}
             ]},
             "clip":  {"versions": [{"v": 1, "mediaId": "...", "downloaded": false, "qc": null, "picked": false}]} } } }
```

- Exactly one candidate per line is `picked: true` once Gate 1 closes; its `file` is what Stage 2
  re-uploads. A re-pick swaps `slot`/`file`/`picked` on the two candidates; `mediaId` and
  `downloaded` are untouched (renamed bytes, not re-fired).
- **`candidates[]` is the authority for next-free-`_altN`** — never a disk scan, which can't see a
  slot promoted/demoted in the JSON but not yet renamed on disk.
- **Resume rule:** skip any candidate/version on disk and **> 10 KB** (catches truncated
  downloads and zero-byte stubs). A deliberate re-roll targets the next free `altN`/`v`, so the
  rule never blocks intentional new takes.

## Destination layout — local (Drive layout → spine step 10)

```
<root>/<project folder>/
  Creatives/ <name>.json                               # the manifest
             + the script/brief, _preflight.json / _postflight.json, _cut_sheet.csv + PDF
  Elements/
    Stills/  claude_<slug>_line<NN>.png                # Stage 1, canonical = Gate-1-picked
             claude_<slug>_line<NN>_alt2.png ...       #   unpicked alts / re-rolls
             _anchors/  _refs/<character ref>.png      # local copy of the char ref + its _panels/
    Clips/   <clipName>_v<VV>.mp4                      # Stage 2, fire-time versioning
    Audio/   (optional) VO / music
  _delivery/Clips/                                      # flat cut-order stage (Delivery)
  _flow_run.json                                        # run state
```

Local names come from the manifest's `outName` / `clipName`. Delivered `Elements/Clips` carry
cut-order names from `plan()` instead.

## Delivery — Flow sequence (spine step 10)

Only Gate-2-picked takes ship. Nothing inside `Elements/Clips/` is renamed or moved; delivery
copies into a flat stage and uploads that.

0. **Postflight first:** `python <root>/.claude/skills/asset-qc-local/postflight.py --client flow --manifest "<manifest>" --drive-folder-id <id> --eyes "<path to the contact sheet / crv grid you reviewed>"` (`--no-drive` instead of the id for a local-only run) must exit 0; it writes `_postflight.json`, and `deliver_stage.py` refuses without it (exit 2 = Drive unresolved or no eye pass recorded; `--eyes` must be a path on disk, postflight checks `os.path.exists`).
1. **Cut order + sections — confirm with the user, don't infer.** Cut order = manifest `shots[]`
   order unless they re-order. `sec` = the brief's script section; a hook opener gets `hook`
   (letter) instead. A manifest with no sections or hooks — ask how its lines group first.
2. **Map, stage, sheet, upload** with `deliver_stage.py --manifest "<Creatives/<name>.json>"`, one command at a time, each with `--dry-run` first: `map` (writes the cut-order map from picked takes; `--only` narrows, `--rev` sets the revision) -> `stage` (flat `_delivery/Clips` named by `plan()`, `Creatives/_cut_sheet.csv`) -> `sheet` (cut-sheet PDF, one per hook) -> `upload` (sequential rclone per destination: Clips, Stills without `_qa/`, Creatives). It refuses unless `_postflight.json` shows qa_exit 0 and is newer than the newest staged clip. A line split into two clips is two manifest shots.
3. **Verify** → spine step 10 (`VERIFY: PASS`), then the editor-notes block → spine step 11.

## QA — Flow layers on top of spine step 9

Layer 0 = `qa_engine.py --profile flow` → spine step 9. The `flow` profile bakes in
`--min-still 768`, `audio require`, 720×1280 @ 24 fps, and diffs Whisper against **`spoken`** with
numbers normalised ("$369" heard as "$300" = **NUMERIC-MISMATCH FAIL**). Layers 1-3 are Flow's
additions and the manual fallback; the stills and grids read in Layer 2 are the eye pass recorded
with `postflight.py --eyes`.

QA belongs at Stage 1 where it's free: a burned-in caption caught in a still costs nothing, in a
clip 12 credits. Run Layer 1 + relevant Layer 2 right after each stage's downloads, before the gate.

**Layer 1 — spec QC, deterministic** (ffprobe + Pillow):

```
python <root>/.claude/skills/asset-qc-local/asset_qc.py "<root>/<project>/Elements/Stills" --min-still 768
python <root>/.claude/skills/asset-qc-local/asset_qc.py "<root>/<project>/Elements/Clips" --audio require --min-duration 7.8 --max-duration 8.2
```

(Set `--min-duration` / `--max-duration` around each clip's manifest `duration`.) Two load-bearing
inversions of the defaults: **`--min-still 768`** — Flow NB2 stills are 768×1376; the default 1024
flags every clean still `LOW-RES` (don't "fix" by regenerating). **`--audio require`** — clips
carry the spoken line; a **missing** track (`AUDIO-MISSING`) means the generation dropped the
voice. Exit **1** = something flagged, a finding not a crash. `asset_qc.py` skips resolution and
framerate; the spec locks **720×1280 / 24fps**, so check each clip:
```
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,avg_frame_rate -of csv=p=0 <clip>
```
Expected output: `720,1280,24/1`.

**Layer 2 — frame QC, visual:**
- **Stills:** identity vs. the ref, burned-in captions (none), malformed hands, on-screen figure
  count/text vs. the beat graphic spec.
- **Clips:** contact-sheet each one, read `crv-out/<name>/grids/` — catches morph, boil, static
  clips, drift from the start frame:
  ```
  crv "<root>/<project>/Elements/Clips/LH_line01_v01.mp4" --grid --no-transcribe -o "<scratchpad>/crv-out/LH_line01_v01"
  ```

**Layer 3 — audio QC, compliance** (highest-value automated check): `crv` **with** transcription:
```
crv "<root>/<project>/Elements/Clips/LH_line01_v01.mp4" --grid -o "<scratchpad>/crv-out/LH_line01_v01_transcribed"
```
Diff `transcript.json` against the shot's **`spoken`** — **never `dialogue`** (a canon "$1,214" vs.
spoken "twelve hundred fourteen dollars" false-positives every numbered line). Every numeric
mismatch is a failure — `$369` as `$300`, or a flapped digit, blocks Gate 2 however good the
performance.

**Gate-2 triage:**
- **First-frame-only defect ≠ rejection** — e.g. product in the wrong hand at frame 1, correct
  after, is editor-fixable (cut the head). Pass with a note, don't burn 12 credits.
- **Reject signals:** a repeated word (brand names included), an unrequested phone/object
  spawning in hand, the phone-hand releasing mid-clip, loud background noise on an otherwise
  clean take, eye contact wandering off-lens.
- Layer 3 catches repeated/dropped words; **also listen for background noise** — `volumedetect`
  on the non-speech gaps, or flag a take whose noise floor is obviously above its sibling's.

**Limits, stated to the user in the report:** `crv` samples keyframes, not true 24fps — a one-frame
artifact can slip through all three layers. The user's player is the final gate; nothing automated
judges whether a performance is *good*.

## Error handling

Every bridge call returns a status or error code. **Nothing is ever re-fired to "fix" a status.**

| Result | Response |
|---|---|
| `UNCONFIRMED` | The fire may have landed and charged. `flow_sweep`, report what it found, **NEVER re-fire** - the line stays blocked until the sweep settles it |
| `UNCONFIRMED` that never lands | Only **the user** releases it: after a `flow_sweep` (which reloads the project) confirms nothing landed for that line and they have checked the Flow grid themselves, they hand-edit that attempt's `status` in `<project>/_flow_run.json` from `unconfirmed`/`pending` to `not_fired`. Claude never edits it and never re-fires before they have |
| `NO_MEDIA_URL` | Flow reported the media but never a media URL - a failed/refused generation. Credits are `unknown`, it is **not** a take (claimed, never `vNN`). Report to the user with a `flow_screenshot`; never re-fire on your own |
| `WRONG_MODEL` / `MODEL_UNKNOWN` / `WRONG_ASPECT` (see `aspect_checked`) | Report to the user, they decide. A wrong-source clip (`abra_edit`) passes every other gate, so never wave one through |
| `CHAR_NO_SHEET` / `PRODUCT_NO_SHEET` / `PRODUCT_GENERATED` / `PRODUCT_OUTSIDE_REGISTRY` | Sheet gate (Preflight step 5). Cut the missing sheet (`charsheet.py` / `productsheet.py`) or move the product into `_REGISTRY/flow/product/`; never generate a product |
| `partial` | `flow_sweep`, report which outputs landed |
| `OUT_OF_CREDITS` | Stop, tell the user. **Never buy credits** |
| `CAPTCHA` / `LOGIN_REQUIRED` | **Stop and ask the user** to clear it in the dedicated window. Never solve it, never retry - retries deepen the block |
| `UI_CHANGED` / `SETTINGS_MISMATCH` / `REF_NOT_ATTACHED` | Bridge guard tripped. `flow_screenshot`, report, stop; do not work around it |
| `PATH_REFUSED` | Path is outside `work_root` or inside a `denied_roots` entry - fix the path (Hard rules) |
| `BROWSER_DOWN` | Ask the user to relaunch the window / re-run `flow_status`, then `flow_sweep` - generations survive server-side |
| Download fails | `flow_download` again by `mediaId`; never regenerate |
| Run dies mid-batch | `flow_sweep` first, then resume - skip any version on disk > 10 KB |

Bridge down entirely -> manual fallback: `flow_snippets.md` (JS payloads, `refserver.py`, `flow_recv.py`, window-focus / background-tab / renderer-wedge choreography).

**Full trap table:** the [Trap Table in `flow_snippets.md`](flow_snippets.md#trap-table) — linked,
not duplicated, so traps are added in exactly one place.

## End of run

1. **Nothing to stop** - the bridge owns its Edge window. (Manual fallback only: stop `refserver.py` / `flow_recv.py` by recorded task id.)
2. **Trap check — before step 3.** Any new Flow UI behavior not in the trap table (a moved button,
   a new default, a dead endpoint) gets appended to `flow_snippets.md`'s Trap Table as
   `| <trap description> | <countermeasure> |`. Mandatory before closing any run.
3. **Report** to the user: credits spent vs. balance, per-line gate outcomes (approved / re-rolled /
   pending), QA flags from Layers 1–3 to check before using the clips, confirmation any new trap
   was appended, and — when delivered to Drive — the `VERIFY:` line verbatim (no PASS line =
   "delivery not verified"). Then the editor-notes block → spine step 11.
