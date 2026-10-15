from pathlib import Path
SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

def test_skill_uses_bridge_not_raw_js():
    t = SKILL.read_text(encoding="utf-8")
    for banned in ("javascript_tool", "IFRAME-UPLOAD", "refserver.py --root", "flow_recv.py --root"):
        assert banned not in t, banned
    for tool in ("flow_status", "flow_fire_still", "flow_fire_clip", "flow_sweep", "flow_download"):
        assert tool in t, tool
