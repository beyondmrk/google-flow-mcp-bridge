# Google Flow MCP Bridge

A Claude Code project (the `flow` MCP bridge plus the `flow-local` skill) that turns an ad script into one 9:16 talking clip per line, generated inside Google Flow
on **your own** Google Ultra account: Nano Banana 2 stills (free), Google Omni Flash motion
(12 credits per clip), with an approval gate after the stills and after the clips.

## You need

- Windows with Microsoft Edge
- [Claude Code](https://claude.com/claude-code)
- [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`) and Python 3.12+
- ffmpeg + ffprobe on PATH (`winget install Gyan.FFmpeg`)
- A Google account with a Flow / Ultra plan. Every clip bills the account signed in to the Flow window.

## Set up (once)

```bash
git clone https://github.com/beyondmrk/google-flow-mcp-bridge.git
cd google-flow-mcp-bridge
pip install -r requirements-qa.txt
claude
```

1. Claude Code asks to approve the `flow` MCP server from `.mcp.json`. Approve it.
2. Ask Claude: **"check flow status"**. A dedicated Edge window opens (its own profile, separate
   from your normal Edge). Sign in to Google there once. If a CAPTCHA shows, solve it yourself.

## Use

In Claude Code, inside this folder: **"fire this in Flow"** and point at a manifest
(`<project>/Creatives/<name>.json`, one entry per line of the script - see
`.claude/skills/flow-local/examples/manifest.example.json`). The `flow-local` skill takes it from
there: manifest review, preflight, stills, your approval, clips, your approval, QA.

- Project folders go **inside this folder** (e.g. `my-first-ad/`). The bridge refuses any path
  outside it. `.gitignore` keeps them out of git. The docs write this folder as `<root>`.
- **Characters are model sheets.** A character used in more than one line needs a model sheet
  cropped into panels (`charsheet.py --prompt`, then `--crop`): `CHAR_X.png` plus
  `CHAR_X_panels/` with front, profile, three_quarter, closeup. `flow_fire.py` refuses
  `CHAR_NO_SHEET` otherwise.
- **Products are never generated.** A product is a product sheet built from real photos
  (`productsheet.py`) in `_REGISTRY/flow/product/`, with face, three_quarter, scale panels.

## Never

- Never buy credits when Flow runs out. Stop and tell whoever runs the account.
- Never press `Ctrl+A` in the Flow page. It selects every clip in the project, and a `Delete` trashes them.
- Never re-fire a line the report calls `UNCONFIRMED`. It may already be charged.

## Layout

| Path | What |
|---|---|
| `.claude/skills/flow-local/` | The skill: procedure, manifest builder, fire script |
| `.claude/skills/asset-qc-local/` | Shared pipeline doc, lints, QA engine, preflight/postflight, delivery |
| `.claude/agents/flow-runner.md` | The subagent that fires one approved batch |
| `mcp/flow/` | The MCP bridge that drives Flow in Edge (`config.toml`: paths relative to it) |

## Updating

`git pull`. Your project folders and the Edge sign-in (`mcp/flow/.edge-profile/`) are untouched.
