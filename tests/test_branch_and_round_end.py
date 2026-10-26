"""10-26 8775 试一次只到 6/8 的几处根因（通用修法，不认文件名、函数名）：
- 点放在"按设置选一支"的判断的某一支里（协议 3 那一支），别的支调用同一个函数：挪到整个判断前面；
- 一轮一次的点全被标成"不一定走"，一轮结束（清零）放不下，"记最后一次"的点一直报不出来：结束那一步改回一定走、
  清零照样放，清单里既没有清零也没有单独打印的直接拦下；
- 试一次：后面的点都到了、一轮也走完了，就某一个没到：直接说是哪一个、在哪一行，连着两轮这样就不等满时间；
- 源码改了无关的文件（指纹变了）：上一版的识别结果相关代码都没变就接着用，不调用大模型。"""

import json
import shutil
from pathlib import Path

from fsw import candidates, identify, instrument, structure
from tests.test_fsw_api import FakeDevice, run_to_try, setup, wait  # noqa: F401
from tests.test_instrument import STEPS

FX = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = FX / "src"
WS = "main/protocols/websocket_protocol.cc"


def _src(rel):
    return structure.Project(SRC).file(rel)


def test_alt_branch_finds_setting_chains_only():
    ws = _src(WS)
    for line, head in ((118, 111), (128, 111), (50, 29)):
        hit = structure.alt_branch(ws, line)
        assert hit and hit["head"] == head and hit["names"] == ["version_"]
    assert structure.alt_branch(ws, 134) is None  # 最后那个默认的 else：不挪
    assert structure.alt_branch(ws, 52) is None
    app = _src("main/application.cc")
    assert structure.alt_branch(app, 635) is None  # 回到聆听在默认 else 里
    afe = _src("main/audio/engines/afe_audio_engine.cc")
    for line in (482, 483, 492):  # 检测结果、局部变量、带函数调用的条件：运行时的情况，不是设置
        assert structure.alt_branch(afe, line) is None


def _replay(name):
    d = json.loads((FX / name).read_text(encoding="utf-8"))
    return d, identify.parse(d["reply"], str(SRC), chain=d["chain"], strict=True)


def test_replay_8775_lifts_and_keeps_round_end():
    d, r = _replay("identify-f67b276.json")
    assert r["ok"] and r["rules"] == identify.RULES_VER == 4
    by = {s["id"]: s for s in r["steps"]}
    assert (by["P9"]["file"], by["P9"]["line"], by["P9"]["where"]) == (WS, 111, "before") and by["P9"]["lifted"]
    assert (by["P8"]["file"], by["P8"]["line"]) == (WS, 29) and by["P8"]["lifted"]
    assert by["P20"]["line"] == 635 and not by["P20"].get("lifted") and by["P20"]["always"] is True
    assert not any(c["level"] == "fail" for c in by["P9"]["chk"])
    assert any("第 128 行只在「version_ == 3」" in n for n in r["seg_repairs"])
    page = identify.to_page(r, d["what"])
    assert not next(s for s in page["steps"] if s["n"] == "回到聆听").get("opt")
    assert d["chain"][9]["line"] == 128  # 调用方的流程不被改


def test_replay_good_0855_unchanged():
    d, r = _replay("identify-cdd6fed.json")
    key = lambda s: (s["id"], s["file"], s["line"], s.get("where"), s.get("once"), s.get("always"), s.get("line_of"))
    assert [key(s) for s in r["steps"]] == [key(s) for s in d["steps"]]
    assert not any(s.get("lifted") for s in r["steps"])


def test_old_plan_gets_round_end_and_reset():
    steps = json.loads((FX / "insert-steps-f67b276.json").read_text(encoding="utf-8"))
    assert instrument.round_end(steps)["n"] == "回到聆听"  # 全是可选的：照样用最后那个一轮一次的
    plan = instrument.parse_plan((FX / "insert-reply-f67b276.txt").read_text(encoding="utf-8"), SRC, steps)
    assert plan["ok"] and plan["end"]["name"] == "回到聆听"
    assert any(x["step"] == "reset" for x in plan["inserts"])
    assert "P7" in {t["step"] for t in plan["tail"]}  # 上行末包：清零时补打


def test_keep_last_without_any_reset_is_blocked():
    steps = [s for s in json.loads((FX / "insert-steps-f67b276.json").read_text(encoding="utf-8"))
             if s.get("once") != "once" or s.get("line") == "start"]
    plan = instrument.parse_plan((FX / "insert-reply-f67b276.txt").read_text(encoding="utf-8"), SRC, steps)
    assert not plan["ok"] and plan["kind"] == "no_round_end"
    assert "上行末包发出" in plan["message"] and "一次也报不出来" in plan["message"]


def test_structure_check_flags_unlifted_point():
    steps = [{"id": "P1", "name": "收到", "file": WS, "line": 128, "where": "before", "once": "first", "loc_ok": True}]
    structure.check(str(SRC), steps)
    assert any(c["kind"] == "branch" and "version_ == 3" in c["text"] for c in steps[0]["chk"])


def test_candidates_skip_alt_branches():
    got = candidates.find(str(SRC), "语音对话")
    for c in got:
        assert structure.alt_branch(structure.Project(SRC).file(c["file"]), c["line"]) is None


# ---------- 源码改了无关的文件：沿用上一版的识别结果 ----------

class NoLLM:
    def __getattr__(self, name):
        raise AssertionError("不该调用大模型：" + name)


def _proj(tmp_path, mutate=None):
    root = tmp_path / "src"
    shutil.copytree(SRC, root)
    d = json.loads((FX / "identify-cdd6fed.json").read_text(encoding="utf-8"))
    d["candidates"] = candidates.find(str(root), d.get("chain_category") or "")  # 演示源码只是一部分：按它重算一份当作"当时的"
    d["rules"] = 3
    proj = tmp_path / "proj"
    f = identify._cache_file(proj, "fpA", d["what"], d["provider"])
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    (proj / "project.json").write_text(json.dumps({"fingerprint": "fpB", "history": ["fpA", "fpB"]}), encoding="utf-8")
    (root / "main" / "unrelated_note.txt").write_text("改了一个无关的文件")
    if mutate:
        mutate(root)
    return root, proj, d


def test_reuse_across_fingerprint_when_code_same(tmp_path):
    root, proj, d = _proj(tmp_path)
    r = identify.identify_cached(NoLLM(), str(root), d["what"], str(proj), "fpB", d["provider"])
    assert r["ok"] and r["reused"] and r["calls"] == 0 and r["reused_older"] == "fpA"
    assert [(s["id"], s["line"]) for s in r["steps"]] == [(s["id"], s["line"]) for s in d["steps"]]
    assert identify._cache_file(proj, "fpB", d["what"], d["provider"]).is_file()


def test_no_reuse_when_a_point_line_changed(tmp_path):
    def shift(root):
        p = root / "main" / "application.cc"
        p.write_text("// 新加一行\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
    root, proj, d = _proj(tmp_path, shift)
    f, why = identify.reuse_older(str(proj), str(root), d["what"], d["provider"], "fpB")
    assert f is None and why


# ---------- 试一次：后面的都到了，就它没到 ----------

STEPS_END = [*STEPS[:2], {**STEPS[2], "once": "once"}]


def _insert(c, r):
    c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": STEPS_END, "chip": "ESP32-S3"})
    return wait(c, lambda j: j["status"] in ("inserted", "failed"))


def test_try_names_point_that_is_never_reached(setup):
    lines = ["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P5,30,0", "FSW,RESET,50,0",
             "FSW,P1,1000010,1", "FSW,P5,1000030,0", "FSW,RESET,1000050,0", "FSW,P1,2000010,1"]
    c, r, *_ = setup(device=FakeDevice(lines))
    assert _insert(c, r)["status"] == "inserted"
    j = run_to_try(c)
    assert j["status"] == "failed"
    m = j["err"]["message"]
    assert "「卡号读完」这一轮没走到" in m and "main/rfid_task.c:131" in m and "删掉" in m
    assert "连着 2 轮" in m
    assert j["err"]["skip"][0]["name"] == "卡号读完"


def test_old_reply_with_lifted_steps_is_moved_by_tool():
    d, r = _replay("identify-f67b276.json")
    pg = identify.to_page(r, d["what"])
    flow = {"end": (r.get("main") or {}).get("end"), "segs": pg["segs"], "hint": None}
    plan = instrument.parse_plan((FX / "insert-reply-f67b276.txt").read_text(encoding="utf-8"), SRC, pg["steps"], flow=flow)
    assert plan["ok"]
    at = {x["step"]: (x["file"], x["after_line"]) for x in plan["inserts"]}
    assert at["P9"] == (WS, 110) and at["P8"] == (WS, 28)  # 码表自己挪到整个判断前面，不用再问 AI
    assert plan["end"]["name"] == "回到聆听"
