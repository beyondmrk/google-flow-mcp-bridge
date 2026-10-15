# PIPELINE.md — the one generation loop (spine)

Every client generation skill runs THIS loop. The client skill supplies values
(account, models, fire script, delivery quirks) and nothing else. If a client SKILL.md
and this file disagree, this file wins — fix the client file.

Client skill on the spine in this kit: `flow-local`. Values live in `clients.json` beside
this file; another client = another `clients.json` entry + a profile in `profiles/`.

The loop used to be pasted into every client file and drifted. Now it lives once, here.

---

## Checklist (the whole loop, in order)

| # | Step | Mandatory | Gate |
|---|---|---|---|
| 1 | Preflight, first pass (`--script`) | always | exit 0 |
| 2 | Watch every reference, record it | always | `_references.json` |
| 3 | Align the shots to the script | always | `--align` exit 0 |
| 4 | Lock entities (character sheets SITUATIONAL, product ALWAYS from registry, CAST LOCK for anyone on camera twice) | see §4 | `--refs` exit 0 · `castlock` clean |
| 5 | Gate 1: shot table + dry-run cost | always | ✋ the user |
| 6 | Stills | | |
| 7 | Gate 2: still approval | always | ✋ the user |
| 8 | Motion | | |
| 9 | QA engine + eye pass + postflight | always | exit 0 |
| 10 | Delivery to Drive + VERIFY | always | `VERIFY: PASS` |
| 11 | Editor-notes block in CHAT | always | pasted |
| 12 | Iteration → back to step 5 | | ✋ the user |

Steps 1, 4 (product), 9, 10, 11 are mandatory on every client run.
A "client run" = a project with `Creatives/_preflight.json` on disk.

---

## 1. Preflight, first pass

```
python <root>/.claude/skills/asset-qc-local/preflight.py --client <client> --project "<root>/<PROJECT>" --script "<script or brief>" --model <stills model> --model <motion model> --drive-folder-id <id>
```

Exit 0 = go. Exit 2 = FLAG, do not fire. Exit 3 = config error.
Writes `Creatives/_preflight.json`. Creates `Creatives/`, `Elements/{Stills,Clips,Audio}`
locally and on Drive, copies the script into `Creatives/`.

Rows: engine · account + workspace · models allowed · layout + script · Elements contents ·
ref locks (only with `--refs`) · references watched (every call with a script) ·
alignment (only with `--align`).

Run this FIRST, before watching references. The `_preflight.json` it writes is the marker
that a media-read hook (if you install one) can use to allow reference grids and contact
sheets inside this project while denying image reads elsewhere.

Per-client `--project` and `--drive` forms: see the client skill's values block.

## 2. Watch every reference

Every link or file in the brief is WATCHED, never "opened": render a frame grid (or play it),
read it, then record what it taught:

```
python <root>/.claude/skills/asset-qc-local/refwatch.py --record "<PROJECT>" --ref "<url|path>" --kind video|link --notes "<what it taught: register, pace, framing>"
```

The record lives in `Creatives/_references.json`. Row 7 of preflight FLAGs a script reference
with no record, on every preflight call. Why: an earlier run spent 304 credits in the wrong
treatment because a reference was not opened.

## 3. Align the shots to the script

Once the shotlist / manifest exists:

```
python <root>/.claude/skills/asset-qc-local/preflight.py --client <client> --project "<PROJECT>" --script "<script>" --model ... --align "<Creatives/<shotlist or manifest>>"
```

DROPPED / INVENTED / REORDERED = FLAG. PARAPHRASED passes. Writes `Creatives/_align.json`.
Blind on silent builds (no spoken lines): say so at Gate 1.

## 4. Lock entities

**Characters — situational.** A character sheet is mandatory ONLY when a person appears in
2+ beats. Pure b-roll (objects, product, atmosphere) skips this entirely.

```
python .../asset-qc-local/charsheet.py --prompt "<desc>" --name CHAR_X [--outfit]   # prints the 16:9 sheet prompt
# fire it on the client's stills model; the user picks the sheet
python .../asset-qc-local/charsheet.py --crop "<sheet.png>" --out "<Elements/Stills/_anchors/CHAR_X_panels>"
python .../asset-qc-local/charsheet.py --verify "<..._panels>"        # exit 1 = panel missing
```

A repeat character from an earlier project is a reconfirm of its existing sheet, never a new one.

**Products — always from the registry, never generated.** `<root>/_REGISTRY/<client>/product/`
holds the sheet + `_panels` (`face`, `three_quarter`, `scale`). First creative that needs one
builds it with `productsheet.py --build spec.json` from supplied photography.

**Declare and verify.** Write `Creatives/_reflocks.json` (`reflocks.py --manifest <json> --client <c>`,
or emit it from the shotlist), upload the sheets to the engine, fill `locked_media_id`, then:

```
python .../asset-qc-local/preflight.py --client <client> --project "<PROJECT>" --script "<script>" --model ... --refs
```

Placeholders (`TODO`, `VERIFY_AT_USE`, empty) FLAG. Doctrine: `REF_LOCKS.md`.

**Cast lock — one master still + one voice per on-camera character.**
The sheet keeps a person consistent across stills; the cast lock keeps them identical across
the cut. On one earlier client, 7 of 16 review notes were "not the same face and voice"; the two
fixes that worked (every shot off one approved master still, every line off one master VO) are
now the rule. On the character entity:

```
{"id": "CHAR_OWNER", "kind": "character", "panels": "...",
 "master": "TH_A",                                  # the Gate-2-approved still every on-camera shot starts from
 "voice": {"vo": "Elements/Audio/VO_master.mp3"}}   # lines cut from it -> an audio-track model's audio_file (5-15 s per shot)
                                                    # or {"lock": "<voice name>"} the shot's `voice` must carry
```

A still may say `"derived_from": "TH_A"` (punch-in, face pass) and counts as the master; rerolls
(`TH_A_v02`) count. `castlock.py` runs inside the fire script's motion preflight: a character on
camera in 2+ shots with no master + voice, a speaking shot that starts elsewhere, a VO-voiced line
not on an audio-track model, or a line over 15 s (split it at a sentence break, two shots off the
same master) is an ERROR before any credit. QA then judges each clip's identity against the still
it was fired from.

## 5. Gate 1 — shot table + cost (✋ mandatory)

Print: shot table (`id | kind | dur | engine | refs | beat`), coverage map, alignment
result, lock status, and the dry-run cost from the client fire script against the live
credit balance. Wait for the user's go. No fire before it.

## 6. Stills

Client fire script, stills stage (`flow-local`: `flow_fire.py --stage stills`). Contact sheet
per batch (one composite, not N reads). The fire script takes the client as a declared key,
reads account / models / rates from `clients.json`, and refuses a real fire on a project the
gate never opened.

## 7. Gate 2 — still approval (✋ mandatory)

The user approves per shot from the contact sheet. Re-rolls via `--only <ids>`. Every regen
lands as the next `vN` beside the prior take; never overwrite, rename, or move a take.

## 8. Motion

Client fire script, motion stage (`flow-local`: `flow_fire.py --stage clips`), detached with a
log. Stream each result as it lands; no verdict attached. NO STATIC CLIPS on any client (the
fire scripts lint).

## 8b. Voice swap (when talking clips were fired on a speaking model)

`voice_stage.py` moves every talking clip to the locked voice with Higgsfield `voice_change`:
upload once, submit once, poll (never `--wait`: one probe hung 47 minutes), land beside the
original in `Elements/Clips/_voicechange/`, then verify duration, audio, words (fuzzy) and PITCH
against the VO master (one batch shipped 2.2 semitones high because nothing checked it).

```
python .../asset-qc-local/voice_stage.py --manifest <json> run [--only T1,T2]   # project.voice_swap.voice_id
python .../asset-qc-local/voice_stage.py --manifest <json> run --redo <TAG>    # one redo, lands as _v02
```

A CHECK is redone once; if it repeats, delivery lays the TTS line under the shot. Clips fired
on an audio-track model from `audio_file` skip this stage: their audio already IS the narration.

## 9. QA + eye pass + postflight (mandatory)

Layers: spec, framescan, startframe, audio, speech (fuzzy), dupes, identity (+ cast
outliers), text, voice (pitch vs the VO master and the cast), lipsync, objects. Strict is the
default; `--lenient` is recorded. `asset-qc-local/SKILL.md` lists every code.

```
python <root>/.claude/skills/asset-qc-local/qa_engine.py --<dir|manifest|shotlist> "<...>" --profile <client profile>
```

Run bare, read the exit code (a pipe hides exit 1). Exit 1 = a FAIL, blocks delivery.
Then the client's own QA layers if it has them. Then the EYE PASS: one contact sheet per still
batch, one frame grid per clip take, read composite-first. Then:

```
python <root>/.claude/skills/asset-qc-local/postflight.py --client <client> --<dir|manifest|shotlist> "<...>" --drive-folder-id <id> --eyes "<path of sheet 1 you read>" --eyes "<path of grid 2 you read>"
```

`--eyes` takes FILE PATHS that exist on disk, one per sheet read. Prose here = exit 2.
Exit 0 = gate passed. Writes `Creatives/_postflight.json`.

## 10. Delivery

Layout on Drive, flat: `Elements/Clips/` = `HA_1`, `S01_01`, `S03_02_v1` in cut order;
`Elements/Stills/` (excluding `_qa`, `_rejects`); `Creatives/` = brief + cut-sheet PDF per hook.
Documents ship as PDF, never `.md`.

```
rclone sync "<local Clips>" "gdrive:<...>/Elements/Clips" --drive-root-folder-id <root>
rclone copy "<local Stills>" "gdrive:<...>/Elements/Stills" --exclude "_qa/**"
python <root>/.claude/skills/asset-qc-local/delivery_names.py --drive-folder-id <id>   # must print VERIFY: PASS
```

`deliver_stage.py` does this as a stage: `map` (one source per shot: `_picks.json`
> the verified voice-swap result > the newest take; a talking clip with no verified swap ships
with "lay the TTS line under it"), `stage` (Creatives/_delivery/, `never_ship` globs kept out,
picks as stills), `sheet` (the cut-sheet PDF), `upload` (GATED: `_postflight.json` must be
qa_exit 0, strict, and newer than the newest staged clip - never `;`-chained), `--rev N` for a
revision that ships `<slot>_vN.mp4` beside the originals with its own sheet.

```
python .../asset-qc-local/deliver_stage.py --manifest <json> map
python .../asset-qc-local/deliver_stage.py --manifest <json> stage && ... sheet && ... upload
```

Client-specific delivery quirks (offered vs automatic delivery, a required name prefix) go in
the client skill's values block.

## 11. Editor-notes block — in chat, never a file

Not written into the project, not uploaded to `Creatives/`. Pasted into the conversation
as the last thing, copy-pasteable on its own:

```
✅ Assets Generated: <Drive folder link>

<one line: what is complete — count, method/skill, model>

<per-demo/hook durations on one line, separated by " · ">

Short instructions:

<one paragraph: how the clips chain / which clip is hero-res / what the editor must
know to cut it>

Credits: ~<used> for the <n> delivered clips (<total incl. refires> including <refire
note>), at <rate> cr/s at <res>. <one caveat on how the count was derived>
```

Credits = USED only. No projections, no caps. Paste the full block, never just a link.

## 12. Iteration

Client review comes back → new shotlist rows → step 3 (`--align`) → Gate 1 again. Never a
silent re-fire.

---

## Laws that apply on top (pointers, enforced in code)

- **DONE = a check passed.** Exit codes, counts, `VERIFY: PASS`. Never "looks good".
- **Client credits.** `--dry-run` before every bulk stage, state the math, right account
  (on the Higgsfield CLI engine: `higgsfield account status` every session; `workspace set`,
  not `select`).
- **Regen = next vN in place.** Never overwrite, rename, `rm`, or relocate a prior take.
- **No QC outside a client run.** Image reads are for projects with `Creatives/_preflight.json`
  up the path.
- **Recycle before generate.** At the coverage-map step: past clip → past still + animate →
  fresh gen.
