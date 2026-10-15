import json, sys
import shutil
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[2] / "mcp" / "flow" / "src"))
sys.path.insert(0, str(HERE.parent / "asset-qc-local"))
import flow_fire

PID = "12144f2f-7426-4e1f-97bb-f3837a9efe62"
VL = "ordinary American woman in her forties"
def make(tmp_path, dur=8, vl=VL):
    d = HERE.parents[2] / "_flow_mcp_test" / ("ff_" + tmp_path.name)
    shutil.rmtree(d, ignore_errors=True)
    (d / "Creatives").mkdir(parents=True, exist_ok=True)
    (d / "Elements" / "Stills").mkdir(parents=True, exist_ok=True)
    ref = d / "Elements" / "Stills" / "_refs"; ref.mkdir(parents=True, exist_ok=True); (ref / "hero.png").write_bytes(b"x")
    pan = ref / "hero_panels"; pan.mkdir(exist_ok=True)  # anchors are sheets: the fire gate needs the crops
    for n in ("front", "profile", "three_quarter", "closeup"): (pan / (n + ".png")).write_bytes(b"x")
    (d / "Elements" / "Stills" / "claude_nj_line01.png").write_bytes(b"x" * 20000)
    m = {"project": {"slug": "nj", "char_ref": str(ref / "hero.png"), "client": "flow"},
         "shots": [{"tag": "L01", "duration": dur, "clipName": "NJ_line01", "outName": "claude_nj_line01.png",
                    "spoken": "Hi I am Lumen and my coffee is hot",
                    "still_prompt": "portrait, no captions, no on-screen text",
                    "i2v_prompt": {"dialogue": "Hi I am Lumen and my coffee is hot", "voice_lock": vl, "action": "talks, handheld wobble"}}]}
    (d / "Creatives" / "home_nj.json").write_text(json.dumps(m), encoding="utf-8")
    (d / "_flow_run.json").write_text(json.dumps({"projectId": PID}), encoding="utf-8")
    return d

class Stub:
    def __init__(self, status="committed"): self.calls, self.status = [], status
    def upload(self, p, project_dir=None): self.calls.append(("upload", Path(p).name)); return {"asset": Path(p).stem}
    def fire_still(self, *a, **k): self.calls.append(("fire_still", k.get("refs"))); return {"status": self.status, "media_ids": ["m1"], "credits": 0}
    def fire_clip(self, *a, **k): self.calls.append(("fire_clip",)); return {"status": self.status, "media_ids": ["c1"], "credits": 12}
    def sweep(self, p): self.calls.append(("sweep",)); return {"resolved": [], "still_pending": ["x"]}
    def download(self, p, ids): self.calls.append(("download", tuple(ids))); return {"written": ["f"], "skipped": []}
    def upload_many(self, paths, project_dir):
        from flow_mcp.guards import FlowError
        self.calls.append(("upload_many", len(paths)))
        assets, errors = {}, {}
        for x in paths:
            try:
                assets[str(x)] = self.upload(x, project_dir)
            except FlowError as e:
                errors[str(x)] = e.code
        return {"assets": assets, "errors": errors}

def test_dry_run_fires_nothing_and_writes_stamp(tmp_path, capsys):
    d = make(tmp_path); s = Stub()
    assert flow_fire.main(["--project", str(d), "--stage", "clips", "--dry-run"], service_factory=lambda: s) == 0
    assert s.calls == []
    st = json.loads((d / "Creatives" / "_flow_lint.json").read_text())
    assert st["lines"]["L01"]["status"] in ("PASS", "WARN")
    assert "12" in capsys.readouterr().out

def test_clip_lint_fail_skips_fire(tmp_path):
    d = make(tmp_path, dur=7); s = Stub()
    assert flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s) == 1
    assert not any(c[0] == "fire_clip" for c in s.calls)

def test_clip_fires_and_downloads(tmp_path):
    d = make(tmp_path); s = Stub()
    assert flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s) == 0
    assert ("fire_clip",) in s.calls and ("download", ("c1",)) in s.calls

def test_budget_stops_before_fire(tmp_path):
    d = make(tmp_path); s = Stub()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--budget", "11"], service_factory=lambda: s)
    assert not any(c[0] == "fire_clip" for c in s.calls)

def test_unconfirmed_sweeps_never_refires(tmp_path):
    d = make(tmp_path); s = Stub(status="UNCONFIRMED")
    flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "1"], service_factory=lambda: s)
    assert [c[0] for c in s.calls].count("fire_clip") == 1 and ("sweep",) in s.calls

def test_still_fires_with_refs_even_on_warn(tmp_path):
    d = make(tmp_path); s = Stub()
    assert flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: s) == 0
    fs = [c for c in s.calls if c[0] == "fire_still"]
    assert [Path(x).name for x in fs[0][1]] == ["front.png", "three_quarter.png"]  # panels, never the flat still

def test_stamp_prompt_sha_matches_fired_prompt(tmp_path):
    from flow_mcp import stamp
    d = make(tmp_path); s = Stub()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--dry-run"], service_factory=lambda: s)
    m = json.loads((d / "Creatives" / "home_nj.json").read_text())
    txt = json.dumps(m["shots"][0]["i2v_prompt"], ensure_ascii=False)
    st = json.loads((d / "Creatives" / "_flow_lint.json").read_text())
    assert st["lines"]["L01"]["prompt_sha256"] == stamp.prompt_sha(txt)
    stamp.check(d, stamp.manifest_for(d), "L01", txt)

def test_missing_char_ref_exits_2(tmp_path):
    d = make(tmp_path); p = d / "Creatives" / "home_nj.json"
    m = json.loads(p.read_text()); del m["project"]["char_ref"]; p.write_text(json.dumps(m))
    assert flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: Stub()) == 2

def test_unknown_only_tag_exits_2(tmp_path):
    d = make(tmp_path)
    assert flow_fire.main(["--project", str(d), "--stage", "stills", "--only", "L99"], service_factory=lambda: Stub()) == 2


def _edit(d, fn):
    p = d / "Creatives" / "home_nj.json"
    m = json.loads(p.read_text()); fn(m); p.write_text(json.dumps(m))

def _second(m):
    s2 = json.loads(json.dumps(m["shots"][0])); s2["tag"] = "L02"; s2["clipName"] = "NJ_line02"
    s2["outName"] = "claude_nj_line02.png"; m["shots"].append(s2)

def test_duplicate_tags_exit_2(tmp_path):
    d = make(tmp_path); _edit(d, lambda m: m["shots"].append(dict(m["shots"][0])))
    assert flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: Stub()) == 2

def test_only_twice_exit_2(tmp_path):
    d = make(tmp_path)
    assert flow_fire.main(["--project", str(d), "--stage", "stills", "--only", "L01,L01"], service_factory=lambda: Stub()) == 2

def test_stop_code_marks_rest_not_run(tmp_path, capsys):
    from flow_mcp.guards import FlowError
    d = make(tmp_path); _edit(d, _second)
    (d / "Elements" / "Stills" / "claude_nj_line02.png").write_bytes(b"x" * 20000)
    class S(Stub):
        def fire_clip(self, *a, **k): self.calls.append(("fire_clip",)); raise FlowError("OUT_OF_CREDITS", "no")
    s = S()
    assert flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s) == 1
    assert [c[0] for c in s.calls].count("fire_clip") == 1
    assert "L02 | clips | NOT_RUN" in capsys.readouterr().out

def test_odd_statuses_charged_and_no_typeerror(tmp_path, capsys):
    d = make(tmp_path); _edit(d, _second)
    (d / "Elements" / "Stills" / "claude_nj_line02.png").write_bytes(b"x" * 20000)
    class S(Stub):
        def fire_clip(self, *a, **k):
            self.calls.append(("fire_clip",))
            return {"status": "NO_MEDIA_URL", "media_ids": ["z"], "credits": "unknown"}
    s = S()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--budget", "12"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert [c[0] for c in s.calls].count("fire_clip") == 1   # 12 charged, second line hits BUDGET
    assert "L02 | clip | BUDGET" in out and "credits spent: 12" in out

def test_refs_truncated_warns(tmp_path):
    d = make(tmp_path); reg = d / "reg"; reg.mkdir(exist_ok=True)
    for n in "abc": (reg / (n + ".png")).write_bytes(b"x")
    def f(m): m["project"]["product_registry"] = str(reg); m["shots"][0]["refs"] = ["a", "b", "c"]
    _edit(d, f); s = Stub()
    flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: s)
    st = json.loads((d / "Creatives" / "_flow_lint.json").read_text())
    assert any(x["code"] == "REFS_TRUNCATED" for x in st["lines"]["L01"]["findings"])
    assert len([c for c in s.calls if c[0] == "fire_still"][0][1]) == 3

def _run_state(d, **line):
    (d / "_flow_run.json").write_text(json.dumps({"projectId": PID, "lines": {"L01": line}, "attempts": {}}))

def test_rerun_skips_committed_clip_and_reroll_fires(tmp_path):
    d = make(tmp_path); _run_state(d, clip={"versions": [{"v": 1}]}); s = Stub()
    assert flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s) == 0
    assert not any(c[0] == "fire_clip" for c in s.calls)
    flow_fire.main(["--project", str(d), "--stage", "clips", "--reroll"], service_factory=lambda: s)
    assert ("fire_clip",) in s.calls

def test_pending_attempt_skips_clip(tmp_path):
    d = make(tmp_path)
    (d / "_flow_run.json").write_text(json.dumps({"projectId": PID, "lines": {}, "attempts": {"a": {"line": "L01", "stage": "clip", "status": "unconfirmed"}}}))
    s = Stub(); flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s)
    assert not any(c[0] == "fire_clip" for c in s.calls)

def test_still_canonical_skipped_unless_reroll(tmp_path):
    d = make(tmp_path); _run_state(d, still={"candidates": [{"slot": "canonical"}]}); s = Stub()
    flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: s)
    assert not any(c[0] == "fire_still" for c in s.calls)
    flow_fire.main(["--project", str(d), "--stage", "stills", "--reroll"], service_factory=lambda: s)
    assert any(c[0] == "fire_still" for c in s.calls)

def test_product_registry_only_ref_not_fail(tmp_path):
    d = make(tmp_path); s = Stub()
    reg = d / "reg"; reg.mkdir(); (reg / "ref2.png").write_bytes(b"x")
    (reg / "ref2_panels").mkdir()  # a registry product is a cropped product sheet
    for n in ("face", "three_quarter", "scale"): (reg / "ref2_panels" / (n + ".png")).write_bytes(b"x")
    mp = d / "Creatives" / "home_nj.json"; m = json.loads(mp.read_text())
    m["project"]["product_registry"] = str(reg); m["shots"][0]["refs"] = ["ref2"]
    mp.write_text(json.dumps(m), encoding="utf-8")
    assert flow_fire.main(["--project", str(d), "--stage", "clips", "--dry-run"], service_factory=lambda: s) == 0
    st = json.loads((d / "Creatives" / "_flow_lint.json").read_text())
    assert st["lines"]["L01"]["status"] in ("PASS", "WARN")


def test_bound_project_from_run_state():
    assert flow_fire._bound({"projectId": "12144f2f-7426-4e1f-97bb-f3837a9efe62"})
    assert not flow_fire._bound({})
    assert not flow_fire._bound({"projectId": "../evil"})


def test_live_run_without_bound_project_exits_2(tmp_path, capsys):
    d = make(tmp_path)
    (d / "_flow_run.json").unlink()
    assert flow_fire.main(["--project", str(d), "--stage", "stills"]) == 2
    assert "flow_open_project" in capsys.readouterr().err


# --- flow-runner review fixes -----------------------------------------------------------------

class KwStub(Stub):
    def fire_clip(self, *a, **k): self.calls.append(("fire_clip", k.get("reroll"))); return {"status": "committed", "media_ids": ["c1"]}
    def fire_still(self, *a, **k): self.calls.append(("fire_still", k.get("outputs"))); return {"status": "committed", "media_ids": ["m1"]}

def test_reroll_flag_passes_through_to_fire_clip(tmp_path):
    d = make(tmp_path); s = KwStub()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--reroll"], service_factory=lambda: s)
    assert ("fire_clip", True) in s.calls

def test_no_reroll_flag_passes_false(tmp_path):
    d = make(tmp_path); s = KwStub()
    flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s)
    assert ("fire_clip", False) in s.calls

def test_unexpected_error_after_clip_fire_prints_table_and_credits(tmp_path, capsys):
    d = make(tmp_path)
    _edit(d, _second)  # L01 + L02
    class Boom(Stub):
        def download(self, p, ids): raise ValueError("disk exploded")
    s = Boom()
    rc = flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert rc == 1
    assert "tag | stage | status" in out
    assert "ERROR" in out and "ValueError" in out and "disk exploded" in out
    assert "credits spent: 12" in out  # the clip reached the click: still charged
    assert "NOT_RUN" in out and sum(1 for c in s.calls if c[0] == "fire_clip") == 1

def test_stills_honour_alts_clamped(tmp_path):
    for alts, want in ((3, 3), (9, 4), (0, 1)):
        d = make(tmp_path)
        _edit(d, lambda m: m["shots"][0].__setitem__("alts", alts))
        s = KwStub()
        flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: s)
        assert ("fire_still", want) in s.calls, (alts, s.calls)

# --- WAVES (--wave N): fire queued, settle as they land ----------------------------------------

def _lines(d, n):
    """Grow the manifest to L01..Ln, each with its own start frame on disk."""
    def grow(m):
        for i in range(2, n + 1):
            s = json.loads(json.dumps(m["shots"][0])); s["tag"] = "L%02d" % i
            s["clipName"] = "NJ_line%02d" % i; s["outName"] = "claude_nj_line%02d.png" % i
            m["shots"].append(s)
    _edit(d, grow)
    for i in range(2, n + 1):
        (d / "Elements" / "Stills" / ("claude_nj_line%02d.png" % i)).write_bytes(b"x" * 20000)

class WaveStub(Stub):
    """Queued fires; attempt #k lands on settle call #k (items land over successive settles).
    never: attempt numbers that never land. unconf: attempt numbers whose fire returns UNCONFIRMED
    (settle may still collect them). land: optional predicate (stub, k) -> landed now."""
    def __init__(self, never=(), unconf=(), land=None):
        super().__init__(); self.n, self.settles, self.inflight, self.never = 0, 0, {}, set(never)
        self.unconf, self.land = set(unconf), land
    def fire_clip(self, *a, **k):
        self.n += 1; aid = "a%d" % self.n
        self.calls.append(("fire_clip", k.get("queue")))
        self.inflight[aid] = self.n
        st = "UNCONFIRMED" if self.n in self.unconf else "QUEUED"
        return {"status": st, "attempt": aid, "media_ids": [], "credits": 12}
    def settle(self, p):
        self.settles += 1; self.calls.append(("settle",))
        res, rend = {}, {}
        for aid, k in list(self.inflight.items()):
            landed = self.land(self, k) if self.land else self.settles >= k
            if k not in self.never and landed:
                res[aid] = {"status": "committed", "attempt": aid, "media_ids": ["c%d" % k]}
                del self.inflight[aid]
            else:
                rend[aid] = {"age_s": 5}
        return {"results": res, "still_rendering": rend}

def _fast(monkeypatch, timeout=5.0):
    monkeypatch.setattr(flow_fire, "_sleep", lambda s: None)
    monkeypatch.setattr(flow_fire, "SETTLE_TIMEOUT_S", timeout)

def test_wave_of_three_commits_all_and_downloads(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 3); s = WaveStub()
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "3", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    names = [c[0] for c in s.calls]
    assert rc == 0, out
    assert names.index("settle") > max(i for i, n in enumerate(names) if n == "fire_clip")  # all 3 in flight
    assert ("fire_clip", True) in s.calls and names.count("fire_clip") == 3
    assert {c for c in s.calls if c[0] == "download"} == {("download", ("c1",)), ("download", ("c2",)), ("download", ("c3",))}
    assert out.count("| committed |") == 3 and "credits spent: 36" in out

def test_wave_keeps_at_most_n_in_flight(tmp_path, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 3); s = WaveStub()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    names = [c[0] for c in s.calls if c[0] in ("fire_clip", "settle")]
    assert names[:3] == ["fire_clip", "fire_clip", "settle"]
    third = [i for i, n in enumerate(names) if n == "fire_clip"][2]
    assert "settle" in names[:third] and names[third + 1:] and set(names[third + 1:]) == {"settle"}

def test_budget_stops_mid_wave(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 3); s = WaveStub()
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "3", "--clip-waves-verified", "--budget", "24"],
                        service_factory=lambda: s)
    out = capsys.readouterr().out
    assert rc == 1 and [c[0] for c in s.calls].count("fire_clip") == 2
    assert "L03 | clip | BUDGET" in out and out.count("| committed |") == 2 and "credits spent: 24" in out

def test_still_rendering_after_settle_timeout(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch, timeout=0.05); d = make(tmp_path); _lines(d, 2); s = WaveStub(never={2})
    monkeypatch.setattr(flow_fire, "_sleep", lambda sec: __import__("time").sleep(0.01))
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert rc == 1 and "L02 | clips | STILL_RENDERING" in out and "L01 | clips | committed" in out
    assert "credits spent: 24" in out and [c[0] for c in s.calls].count("fire_clip") == 2

def test_wave_one_is_the_blocking_path(tmp_path, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); s = KwStub()
    assert flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "1"], service_factory=lambda: s) == 0
    class Q(Stub):
        def fire_clip(self, *a, **k): self.calls.append(("fire_clip", k.get("queue"))); return {"status": "committed", "media_ids": ["c1"]}
    q = Q(); flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "1", "--reroll"], service_factory=lambda: q)
    assert ("fire_clip", False) in q.calls and not any(c[0] == "settle" for c in q.calls)

def test_wave_bounds(tmp_path):
    d = make(tmp_path)
    for bad in ("0", "7"):
        try:
            flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", bad], service_factory=lambda: Stub())
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError(bad)

def test_refund_only_for_pre_click_codes(tmp_path, capsys, monkeypatch):
    from flow_mcp.guards import FlowError
    _fast(monkeypatch)
    class Pre(Stub):
        def fire_clip(self, *a, **k): self.calls.append(("fire_clip",)); raise FlowError("UI_CHANGED", "button gone")
    d = make(tmp_path); flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: Pre())
    assert "credits spent: 0" in capsys.readouterr().out
    def post(code):
        e = FlowError(code, "after the click"); e.after_click = True; return e
    for code in ("BROWSER_DOWN", "UI_CHANGED"):
        s = Stub()
        def boom(*a, _c=code, _s=s, **k):
            _s.calls.append(("fire_clip",)); raise post(_c)
        s.fire_clip = boom
        d = make(tmp_path)
        flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s)
        out = capsys.readouterr().out
        assert "credits spent: 12" in out, (code, out)
    class Down(Stub):
        def fire_clip(self, *a, **k): self.calls.append(("fire_clip",)); raise FlowError("BROWSER_DOWN", "x")
    d = make(tmp_path); _lines(d, 2); s = Down()
    flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert "credits spent: 12" in out and "L02 | clips | NOT_RUN" in out  # BROWSER_DOWN: charged + stop


# --- fix round 1 -------------------------------------------------------------------------------

def test_first_fire_of_a_wave_unconfirmed_stops_the_run(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch, timeout=0.05); d = make(tmp_path); _lines(d, 3)
    s = WaveStub(never={1, 2, 3}, unconf={1, 2, 3})
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "3", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert rc == 1 and [c[0] for c in s.calls].count("fire_clip") == 1
    assert "L01 | clips | UNCONFIRMED" in out and "L02 | clips | NOT_RUN" in out and "L03 | clips | NOT_RUN" in out
    assert not any(c[0] == "sweep" for c in s.calls) and "credits spent: 12" in out

def test_any_unconfirmed_in_a_wave_stops_firing(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 3)
    s = WaveStub(unconf={2})  # a2 UNCONFIRMED (lands on settle #2): no blind firing after it
    flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "3", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert [c[0] for c in s.calls].count("fire_clip") == 2 and "L03 | clips | NOT_RUN" in out
    assert "L02 | clips | committed" in out  # still collected by settle


def test_queued_unconfirmed_not_swept_and_settle_commits_it(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 2)
    s = WaveStub(unconf={2})
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert not any(c[0] == "sweep" for c in s.calls)
    assert ("download", ("c1",)) in s.calls and ("download", ("c2",)) in s.calls
    assert "L02 | clips | committed" in out and rc == 0

def test_still_rendering_row_committed_by_a_later_wave(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch, timeout=0.05)
    monkeypatch.setattr(flow_fire, "_sleep", lambda sec: __import__("time").sleep(0.01))
    d = make(tmp_path); _lines(d, 3)
    s = WaveStub(land=lambda st, k: st.settles >= k if k != 2 else st.n >= 3)  # L02 lands after L03 fired
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert "L02 | clips | committed" in out and ("download", ("c2",)) in s.calls
    assert "STILL_RENDERING" not in out and rc == 0 and "credits spent: 36" in out

def test_same_prompt_pending_refused_without_charge(tmp_path, capsys, monkeypatch):
    from flow_mcp.guards import FlowError
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 2)
    class S(WaveStub):
        def fire_clip(self, *a, **k):
            if self.n == 1:
                self.calls.append(("fire_clip_refused",))
                raise FlowError("SAME_PROMPT_PENDING", "L01 same prompt pending")
            return super().fire_clip(*a, **k)
    s = S()
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert "L02 | clips | SAME_PROMPT_PENDING" in out and "L01 | clips | committed" in out
    assert "credits spent: 12" in out and rc == 1


# --- redesign: no navigation in flight, Flow-side acceptance, LOST, deferred downloads -------------

def test_wave_uploads_everything_before_the_first_fire(tmp_path, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 3); s = WaveStub()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "3", "--clip-waves-verified"], service_factory=lambda: s)
    names = [c[0] for c in s.calls]
    first_fire = names.index("fire_clip")
    assert names[:first_fire].count("upload") == 3 and "upload" not in names[first_fire:]

def test_wave_upload_failure_skips_that_line_uncharged(tmp_path, capsys, monkeypatch):
    from flow_mcp.guards import FlowError
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 2)
    class S(WaveStub):
        def upload(self, p, project_dir=None):
            if Path(p).name == "claude_nj_line02.png": raise FlowError("REF_NOT_ATTACHED", "stuck")
            return super().upload(p, project_dir)
    s = S()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert "L02 | clips | UPLOAD_REF_NOT_ATTACHED" in out and "credits spent: 12" in out
    assert [c[0] for c in s.calls].count("fire_clip") == 1

def test_lost_from_settle_reported_and_stays_charged(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 2)
    class S(WaveStub):
        def settle(self, p):
            r = super().settle(p)
            if "a2" in self.inflight:
                del self.inflight["a2"]; r["still_rendering"].pop("a2", None)
                r["results"]["a2"] = {"status": "LOST", "attempt": "a2", "media_ids": [], "credits": "unknown"}
            return r
    s = S()
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert rc == 1 and "L02 | clips | LOST" in out and "spend unknown" in out and "credits spent: 24" in out

def test_lost_attempt_blocks_a_plain_rerun():
    run = {"attempts": {"a": {"line": "L01", "stage": "clip", "status": "lost"}}}
    assert flow_fire._already(run, "L01", "clips") == "LOST"

def test_deferred_download_is_retried_after_the_wave(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 2)
    class S(WaveStub):
        def download(self, p, ids):
            self.calls.append(("download", tuple(ids)))
            if self.inflight:  # something still in flight: the service defers a detail-view fetch
                return {"written": [], "skipped": [], "deferred": list(ids), "failed": {}}
            return {"written": ["f_" + ids[0]], "skipped": [], "deferred": [], "failed": {}}
    s = S()
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: s)
    out = capsys.readouterr().out
    assert rc == 0 and "f_c1" in out and "f_c2" in out and "DOWNLOAD_DEFERRED" not in out

def test_download_deferred_to_the_end_is_reported(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path)
    class S(WaveStub):
        def download(self, p, ids): return {"written": [], "skipped": [], "deferred": list(ids), "failed": {}}
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: S())
    out = capsys.readouterr().out
    assert rc == 1 and "L01 | clips | DOWNLOAD_DEFERRED" in out

def test_download_without_obtainable_url_is_reported(tmp_path, capsys, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path)
    class S(WaveStub):
        def download(self, p, ids): return {"written": [], "skipped": [], "deferred": [], "failed": {ids[0]: "NO_MEDIA_URL"}}
    rc = flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "2", "--clip-waves-verified"], service_factory=lambda: S())
    assert rc == 1 and "L01 | clips | DOWNLOAD_NO_MEDIA_URL" in capsys.readouterr().out


# --- redesign fix round ---------------------------------------------------------------------------

def test_clip_waves_need_the_verified_flag(tmp_path):
    d = make(tmp_path)
    try:
        flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "4"], service_factory=lambda: Stub())
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("clip --wave 4 without --clip-waves-verified must exit 2")

def test_clips_default_to_wave_one_stills_to_four(tmp_path, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path)
    class Q(Stub):
        def fire_clip(self, *a, **k): self.calls.append(("fire_clip", k.get("queue"))); return {"status": "committed", "media_ids": ["c1"]}
        def fire_still(self, *a, **k): self.calls.append(("fire_still", k.get("queue"))); return {"status": "committed", "media_ids": ["m1"]}
    q = Q(); flow_fire.main(["--project", str(d), "--stage", "clips"], service_factory=lambda: q)
    assert ("fire_clip", False) in q.calls
    q = Q(); flow_fire.main(["--project", str(d), "--stage", "stills"], service_factory=lambda: q)
    assert ("fire_still", True) in q.calls

def test_wave_uploads_with_one_upload_many_call(tmp_path, monkeypatch):
    _fast(monkeypatch); d = make(tmp_path); _lines(d, 3); s = WaveStub()
    flow_fire.main(["--project", str(d), "--stage", "clips", "--wave", "3", "--clip-waves-verified"],
                   service_factory=lambda: s)
    assert [c for c in s.calls if c[0] == "upload_many"] == [("upload_many", 3)]

def test_still_reroll_passes_through(tmp_path):
    d = make(tmp_path)
    class Q(Stub):
        def fire_still(self, *a, **k): self.calls.append(("fire_still", k.get("reroll"))); return {"status": "committed", "media_ids": ["m1"]}
    q = Q(); flow_fire.main(["--project", str(d), "--stage", "stills", "--reroll"], service_factory=lambda: q)
    assert ("fire_still", True) in q.calls
