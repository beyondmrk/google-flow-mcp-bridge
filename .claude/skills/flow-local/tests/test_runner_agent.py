from pathlib import Path
AG = Path(__file__).resolve().parents[3] / "agents" / "flow-runner.md"
SK = Path(__file__).resolve().parents[1] / "SKILL.md"

def test_agent_frontmatter_and_never():
    t = AG.read_text(encoding="utf-8")
    assert t.startswith("---\nname: flow-runner\n")
    for s in ("tools: Bash, Read, Grep, Glob, mcp__flow__flow_status", "## Never", "## Report", "flow_fire.py", "qa_engine.py"):
        assert s in t, s
    assert "flow_fire_clip" not in t   # the agent fires only through the script

def test_skill_dispatches_runner_and_gated_delivery():
    t = SK.read_text(encoding="utf-8")
    for s in ("flow-runner", "flow_fire.py", "deliver_stage.py", "gen_lints"):
        assert s in t, s
    assert "rclone sync" not in t   # inline delivery replaced by deliver_stage.py

def test_binding_precondition_in_both():
    for p in (AG, SK):
        assert "flow_open_project" in p.read_text(encoding="utf-8"), p

def test_delivery_postflight_runnable():
    t = SK.read_text(encoding="utf-8")
    assert "postflight.py" in t and "--client flow" in t

def test_never_retry_list_covers_every_non_committed_status():
    t = AG.read_text(encoding="utf-8")
    never = t.split("## Never")[1].split("## Steps")[0]
    for s in ("NO_START_FRAME", "REF_NOT_ATTACHED", "REF_MISSING", "DOWNLOAD_", "NOT_RUN", "ERROR",
              "ALREADY_COMMITTED"):
        assert s in never, s

def test_skill_review_doc_fixes():
    t = SK.read_text(encoding="utf-8")
    assert "a two-ref fire needs the manual fallback" not in t
    assert "--eyes \"<path to the contact sheet / crv grid you reviewed>\"" in t
    assert "binds the prompt only" in t and "project_dir` to `flow_upload" in t
    assert len(t.splitlines()) <= 505
