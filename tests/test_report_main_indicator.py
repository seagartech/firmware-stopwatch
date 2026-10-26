"""10-26 报告主指标和分轮（8784 那次全程报告核对出来的几处，对这一类设备通用，不认具体固件）：
A. 主线上有几个"第一下感觉到"的点：主指标是设备往外给出结果的第一下（识别时标了输出的那一步，比如扬声器首次出声），
   中间结果（识别文字到达）降成次要的线、照样单列；AI 自己说用户看不到的点不当主指标；标题、体验标准都挂主指标。
B. 清零之后、下一轮真开始之前，残留音频又触发的"第一次"点不当新一轮的开头，这段里的收尾点归上一轮。
C. 数据检查不再一律说"上一轮被打断"；标题跟着主指标。
D. 图和表只放线路径上的段；同时在走的段单列、不进合计；合计和各段中位数对不上时写明。
E. 主线以外会改变看到、听到的分支：码表列出、大模型逐条留或不留，留下的各成一条顺带的线（可以好几条）。"""

import json
import shutil
import statistics as st
import subprocess
from pathlib import Path

import pytest

from fsw import identify as idf, measure as meas, standards as stds

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/report_main_regression"
JS = (ROOT / "fsw/web/static/app.js").read_text(encoding="utf-8")
node = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
STUB = ("const f=(x,d=2)=>x==null||!isFinite(x)?'—':x.toFixed(d);"
        "const htmlEsc=t=>String(t||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));"
        "const SANS='';const med=a=>{const s=[...a].sort((x,y)=>x-y),m=s.length/2;return s.length%2?s[m|0]:(s[m-1]+s[m])/2};")


def _fn(name, end):
    i = JS.index(name)
    return JS[i:JS.index(end, i)]


def _run(js):
    return subprocess.run(["node", "-e", STUB + js], capture_output=True, text=True, check=True).stdout


def _page(steps_extra=None):
    """一条通用的语音类主线：说完 → 上传 → 识别文字到达（屏上被藏起来）→ 首段出声 → 放完。"""
    steps = [{"p": "P1", "n": "说话结束", "line": "start"},
             {"p": "P2", "n": "上传首包", "line": "talk"},
             {"p": "P3", "n": "收到识别文字", "line": "talk", "felt": "first", "kind": "画面", "note": "字幕被藏起来，屏幕上看不到"},
             {"p": "P4", "n": "收到首段音频", "line": "talk"},
             {"p": "P5", "n": "扬声器首次出声", "line": "talk", "felt": "first", "kind": "声音", "out": True},
             {"p": "P6", "n": "回复播完", "line": "talk", "felt": "last", "kind": "声音", "whole": True}]
    for s in steps_extra or []:
        steps.append(s)
    segs = [{"a": "P1", "b": "P2", "t": "上传", "who": "固件"}, {"a": "P2", "b": "P3", "t": "识别", "who": "服务器", "out": True},
            {"a": "P2", "b": "P4", "t": "生成回复", "who": "服务器", "out": True}, {"a": "P4", "b": "P5", "t": "解码", "who": "固件"},
            {"a": "P5", "b": "P6", "t": "播放", "who": "固件"}]
    return {"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "回复这条"}, "main": [s["p"] for s in steps],
            "measure": "说话结束 → 回复播完", "category": "语音对话"}


# ---------- A ----------

def test_primary_is_the_first_output_not_the_hidden_text():
    sc = stds.with_default_experience(stds.real_scenario(_page(), None))
    by = {l["name"]: l for l in sc["lines"]}
    assert by["到扬声器首次出声"].get("first") and not by["到收到识别文字"].get("first")
    assert by["到收到识别文字"].get("also_first")  # 中间结果照样单列一条
    names = [l["name"] for l in sc["lines"]]
    assert names.index("到收到识别文字") < names.index("到扬声器首次出声") < len(names) - 1  # 全程那条最后
    assert [e["line"] for e in sc.get("experiences") or [] if e.get("fair") is not None] == ["到扬声器首次出声"]


def test_unseen_point_is_never_primary_even_without_output_role():
    p = _page()
    p["steps"][4].pop("out")
    sc = stds.real_scenario(p, None)
    assert next(l for l in sc["lines"] if l.get("first"))["name"] == "到扬声器首次出声"


def test_without_hints_the_earliest_first_point_is_primary():
    p = _page()
    p["steps"][2].pop("note")
    p["steps"][4].pop("out")
    sc = stds.real_scenario(p, None)
    assert next(l for l in sc["lines"] if l.get("first"))["name"] == "到收到识别文字"
    assert any(l.get("also_first") and l["name"] == "到扬声器首次出声" for l in sc["lines"])


def test_primary_first_order():
    sp = {"a": {"felt": "first", "note": "用户看不到"}, "b": {"felt": "first"}, "c": {"felt": "first", "out": True}}
    assert stds.primary_first(["a", "b", "c"], sp) == "c"
    assert stds.primary_first(["a", "b"], sp) == "b"
    assert stds.primary_first(["a"], sp) == "a"


def test_to_page_marks_output_step():
    r = {"steps": [{"id": "P1", "name": "a", "file": "x.c", "line": 1, "line_of": "start", "why": "", "sure": "高", "code": "", "loc_ok": True,
                    "where": "before", "once": "once", "func": "", "doubt": "", "felt": "", "chain_step": 1},
                   {"id": "P2", "name": "b", "file": "x.c", "line": 5, "line_of": "main", "why": "", "sure": "高", "code": "", "loc_ok": True,
                    "where": "before", "once": "first", "func": "", "doubt": "", "felt": "first", "kind": "声音", "chain_step": 2}],
         "segments": [{"from": "P1", "to": "P2", "name": "s"}], "flow": {"chain": [{"n": 1, "role": ""}, {"n": 2, "role": "输出"}]}}
    pg = idf.to_page(r, "w")
    assert pg["steps"][1].get("out") is True and not pg["steps"][0].get("out")


# ---------- D ----------

def test_lines_hold_only_their_path_and_parallel_is_listed():
    sc = stds.real_scenario(_page(), None)
    by = {l["name"]: l for l in sc["lines"]}
    assert by["到扬声器首次出声"]["segments"] == ["P1-P2", "P2-P4", "P4-P5"]
    assert by["到收到识别文字"]["segments"] == ["P1-P2", "P2-P3"]
    assert "P2-P3" not in by["到扬声器首次出声"]["segments"]


# ---------- A+B 回归（8784 那次的计时点日志，只留 FSW 行；页面去掉了源码路径） ----------

def _regress():
    page = json.loads((FIX / "page.json").read_text(encoding="utf-8"))
    sc = stds.with_default_experience(stds.real_scenario(page, stds.find(page["steps"])))
    rs = [r for r in meas.collect((FIX / "fsw.txt").read_text().splitlines(), meas.Spec.from_scenario(sc)) if r["ok"]]
    return sc, rs


def test_regression_main_indicator_and_whole_round():
    sc, rs = _regress()
    assert len(rs) == 10
    by = {l["name"]: i for i, l in enumerate(sc["lines"])}
    vals = lambda name: [r["ends"][by[name]] for r in rs if r["ends"][by[name]] is not None]
    first = next(l["name"] for l in sc["lines"] if l.get("first"))
    assert first == "到扬声器首次出声"
    v = vals(first)
    assert len(v) == 10 and st.median(v) == pytest.approx(1.846, abs=0.001)
    assert min(v) == pytest.approx(1.401, abs=0.001) and max(v) == pytest.approx(2.273, abs=0.001)
    assert st.median(vals("到收到识别文字")) == pytest.approx(0.387, abs=0.001)
    w = vals(sc["lines"][-1]["name"])
    assert len(w) == 10 and st.median(w) == pytest.approx(2.995, abs=0.001)
    assert min(w) == pytest.approx(2.43, abs=0.01) and max(w) == pytest.approx(5.86, abs=0.01)
    drain = [r["segs"]["P12-P14"] for r in rs if r["segs"].get("P12-P14") is not None]
    assert len(drain) == 10 and st.median(drain) == pytest.approx(1.00, abs=0.01)
    assert [e["line"] for e in sc.get("experiences") or [] if e.get("fair") is not None] == ["到扬声器首次出声"]


# ---------- B 通用的小例子 ----------

def _end_page():
    """一轮按"收到回复结束"清零（roundEnd），量到"播放放空"（每次都记、取最后那次）。"""
    p = _page()
    p["steps"][5].update(once="every")
    p["steps"].append({"p": "P7", "n": "收到回复结束", "line": "talk", "felt": "last", "kind": "声音", "once": "once"})
    p["segs"].append({"a": "P5", "b": "P7", "t": "等结束消息", "who": "服务器", "out": True})
    p["main"].append("P7")
    p["roundEnd"] = "P7"
    return p


def _round(t0):
    t = lambda s: str(int((t0 + s) * 1e6))
    return [f"FSW,P1,{t(0)},0", f"FSW,P2,{t(0.05)},0", f"FSW,P3,{t(0.4)},0", f"FSW,P4,{t(1.2)},0",
            f"FSW,P5,{t(1.5)},0", f"FSW,P7,{t(2.0)},0", f"FSW,RESET,{t(2.01)},0"]


def test_leftover_first_point_after_reset_does_not_steal_the_tail():
    sc = stds.real_scenario(_end_page(), None)
    log = []
    for t0 in (10, 30, 50, 70):
        # 清零之后残留的那点声音又触发一次"首次出声"（不是新一轮的开头），真正的放空在这之后才到：放空归上一轮
        log += _round(t0) + [f"FSW,P5,{int((t0 + 2.05) * 1e6)},0", f"FSW,P6,{int((t0 + 3.0) * 1e6)},0"]
    rs = [r for r in meas.collect(log, meas.Spec.from_scenario(sc)) if r["ok"]]
    assert len(rs) == 3  # 最后一轮的放空要等下一轮或安静下来才收
    k = len(sc["lines"]) - 1
    assert [round(r["ends"][k], 2) for r in rs] == [3.0] * 3


# ---------- C/D 前端 ----------

@node
def test_gap_text_does_not_blame_an_interrupted_round():
    fn = _fn("function gapLine(", "\nfunction subline(")
    out = _run(fn + "console.log(gapLine([{n:3,names:['上传首包']}]))")
    assert "被中途打断" not in out and "上一轮的" in out and "第 3 轮" in out


@node
def test_title_leads_with_main_indicator():
    fn = _fn("function headline(", "\nconst LV=")
    D = {"S": {"real": True, "startShort": "说话结束"}, "n": 10,
         "J": {"scenario": {"lines": [{"name": "到收到识别文字", "also_first": True}, {"name": "到扬声器首次出声", "first": True},
                                      {"name": "全程"}]},
               "lines": [{"name": "全程", "measure": True, "med": 2.995}]},
         "lines": [{"name": "到收到识别文字", "end": "收到识别文字", "med": 0.387, "segs": []},
                   {"name": "到扬声器首次出声", "end": "扬声器首次出声", "med": 1.846, "segs": []}]}
    out = _run(fn + f"console.log(titleHtml({json.dumps(D, ensure_ascii=False)}))")
    h1 = out.split("</h1>")[0]
    assert "扬声器首次出声 1.85 秒" in h1 and "识别文字" not in h1
    assert "收到识别文字 0.39 秒" in out and "全程 3.00 秒" in out


@node
def test_parallel_segments_are_kept_out_of_lines_and_listed():
    fn = _fn("function lenByContent(", "\n// 报告顶上一行")
    sc = {"lines": [{"name": "到首段出声", "end": "首段出声", "segments": ["a", "b"], "first": True}],
          "segments": {"a": {}, "b": {}, "x": {}}, "parallel": ["x"]}
    out = json.loads(_run(fn + f"console.log(JSON.stringify(lenByContent({json.dumps({'scenario': sc, 'lines': []})})))"))
    assert "x" not in out["segments"] and out["recOnly"]["parallel"] == ["x"] and out["cutEnd"] == "首段出声"


@node
def test_record_only_box_lists_parallel_segments():
    fn = _fn("function recOnlyBox(", "\nfunction reportPage(")
    D = {"n": 10, "S": {"startShort": "说话结束", "recOnly": {"lines": [], "segments": [], "parallel": ["P3-P5"]}},
         "J": {"scenario": {"segments": {"P3-P5": {"name": "上行音频发送", "owner": "固件"}}}, "lines": [],
               "segments": [{"k": "P3-P5", "med": 0.5, "min": 0.4, "max": 0.6, "count": 10, "of": 10}]}}
    out = _run(fn + f"console.log(recOnlyBox({json.dumps(D, ensure_ascii=False)}))")
    assert "并行的段" in out and "上行音频发送" in out and "不加进合计" in out


# ---------- E ----------

def _r_for_side(tmp_path):
    src = tmp_path / "app.c"
    src.write_text("void on_msg(const char* t) {\n  if (is(t, \"stt\")) {\n    show_text(t);\n  } else if (is(t, \"llm\")) {\n"
                   "    set_emotion(t);\n  } else if (is(t, \"ota\")) {\n    show_upgrade(t);\n  }\n}\n", encoding="utf-8")
    base = {"why": "", "sure": "高", "code": "", "loc_ok": True, "where": "before", "once": "once", "func": "", "doubt": ""}
    steps = [{**base, "id": "P1", "name": "说话结束", "file": "app.c", "line": 1, "line_of": "start", "felt": "", "chain_step": 1},
             {**base, "id": "P2", "name": "收到识别文字", "file": "app.c", "line": 3, "line_of": "main", "felt": "first", "chain_step": 2}]
    cands = [{"id": "K1", "role": "消息", "file": "app.c", "line": 3, "code": "show_text(t);", "area": [2, 4], "where": "stt"},
             {"id": "K2", "role": "消息", "file": "app.c", "line": 5, "code": "set_emotion(t);", "area": [4, 6], "where": "llm"},
             {"id": "K3", "role": "消息", "file": "app.c", "line": 7, "code": "show_upgrade(t);", "area": [6, 8], "where": "ota"},
             {"id": "K4", "role": "消息", "file": "app.c", "line": 7, "code": "count++;", "area": [6, 8], "where": "ota"}]
    return {"steps": steps, "segments": [{"from": "P1", "to": "P2", "name": "识别"}], "candidates": cands,
            "chain": [{"n": 1, "file": "app.c", "line": 1}, {"n": 2, "file": "app.c", "line": 3}], "side": None}


def test_side_candidates_skip_main_branches_and_invisible_lines(tmp_path):
    r = _r_for_side(tmp_path)
    assert [c["id"] for c in idf.side_candidates(r)] == ["K2", "K3"]


def test_side_review_keeps_several_lines_and_rejects_the_rest(tmp_path):
    r = _r_for_side(tmp_path)
    r["candidates"].append({"id": "K5", "role": "消息", "file": "app.c", "line": 7, "code": "show_upgrade(t);", "area": [6, 8]})
    cands = idf.side_candidates(r)
    ans = ('```json\n{"review": [{"id": "K2", "keep": true, "name": "表情这条", "end": "换上表情", "from": "P2", "kind": "画面", "why": "每轮回复都带"},'
           '{"id": "K3", "keep": false, "why": "只在升级时走"}]}\n```')
    rv = idf.apply_side_review(str(tmp_path), r, cands, ans)
    assert {x["id"]: x["keep"] for x in rv} == {"K2": True, "K3": False}
    new = [s for s in r["steps"] if s["line_of"] == "side"]
    assert [(s["id"], s["file"], s["line"], s["felt"]) for s in new] == [("P3", "app.c", 5, "first")]
    pg = idf.to_page(r, "w")
    assert pg["sides"][0]["name"] == "表情这条" and pg["sideAsk"] and "换上表情" in pg["sideAsk"]["h"]
    assert [x["id"] for x in pg["sideReview"]] == ["K2", "K3"]
    # 两条一起留：各成一条线
    r2 = _r_for_side(tmp_path)
    ans2 = ('```json\n{"review": [{"id": "K2", "keep": true, "name": "表情这条", "end": "换上表情", "from": "P2"},'
            '{"id": "K3", "keep": true, "name": "提示这条", "end": "弹出提示", "from": "P1"}]}\n```')
    idf.apply_side_review(str(tmp_path), r2, idf.side_candidates(r2), ans2)
    pg2 = idf.to_page(r2, "w")
    sc = stds.real_scenario(pg2, None)
    sides = {l["name"]: l["segments"] for l in sc["lines"] if l.get("sl")}
    assert sides == {"表情这条": ["P1-P2", "P2-P3"], "提示这条": ["P1-P4"]}


def test_side_review_bad_reply_keeps_nothing(tmp_path):
    r = _r_for_side(tmp_path)
    rv = idf.apply_side_review(str(tmp_path), r, idf.side_candidates(r), "看不懂")
    assert all(x["keep"] is None for x in rv) and not any(s["line_of"] == "side" for s in r["steps"])
