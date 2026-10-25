"""10-25 晚 8770：找关键步骤说「回到聆听」插在 application.cc 第 635 行 SetDeviceState(kDeviceStateListening) 之前（else 那一支），
插法 AI 写成"第 633 行后面"——633 行在手动停止那一支（if 里），差一行跨过了 `} else {`，点进了正常用法走不到的那一支，试一次卡在 9/10。
码表原来只核对行号、内容，不核对插的位置和对应的那一句在不在同一个代码块里。

改成（看结构，不认文件名、函数名）：插的位置和对应的那一句之间隔着没配对的 { }、或同一层的 else / case / default，就算放错；
那一句清楚就码表自己挪到它紧前面（之后的挪到紧后面），说不准才在同一段对话里追问 AI 一次，还不对才失败。
重放 8770 存下的 AI 回答（insert-reply-1742b64.txt）；8767、8769 那几份放对了的点照样不动。
"""

import json
from pathlib import Path

import pytest

from fsw import instrument as ins
from fsw.llm import FakeLLM

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"

@pytest.fixture(autouse=True)
def _model_reset_only(monkeypatch):
    """这里查的是 AI 写的位置怎么核对（码表找不到地方放清零时的老办法）：不让码表自己放清零（见 test_insert_reset_by_tool）。"""
    monkeypatch.setattr(ins, "place_reset", lambda *a, **k: None)



def steps():
    return json.loads((HERE / "insert-steps-6e9c22a.json").read_text(encoding="utf-8"))


def reply(name):
    return (HERE / name).read_text(encoding="utf-8")


def as_reply(p):
    return "```json\n" + json.dumps(p, ensure_ascii=False) + "\n```"


def test_back_to_listening_in_the_other_branch_is_moved_back():
    r = ins.parse_plan(reply("insert-reply-1742b64.txt"), SRC, steps())
    assert r["ok"]
    p12 = [x for x in r["inserts"] if x["step"] == "P12"]
    assert [x["after_line"] for x in p12] == [634]  # 紧挨着第 635 行之前、else 那一支里
    m = next(f for f in r["fixes"] if f["step"] == "P12")
    assert m["kind"] == "moved" and m["after_line"] == 633 and m["to"] == 634
    assert "第 635 行" in m["why"] and "不在同一个代码块里" in m["why"]
    assert any("码表把 P12 从 main/application.cc 第 633 行后面挪到第 634 行后面" in n for n in r["notes"])


def test_other_points_of_that_plan_that_crossed_a_block_are_moved_too():
    """同一份回答里还有几处跨了块（比如「说话结束」插进了"开始说话"那一支）：都挪到对应那一句紧前面。"""
    r = ins.parse_plan(reply("insert-reply-1742b64.txt"), SRC, steps())
    moved = {f["step"]: (f["after_line"], f["to"]) for f in r["fixes"]}
    assert moved["P1"] == (480, 481)  # 原来在 VAD_SPEECH 那一支（说话开始）
    by = {s["p"]: s["l"] for s in steps()}
    for x in r["inserts"]:
        if x["step"] in moved:
            assert x["after_line"] == by[x["step"]] - 1


def test_good_plans_are_not_touched():
    d = json.loads(reply("insert-plan-4e1e8da.json"))
    r = ins.parse_plan(d["reply"], SRC, d["steps"])
    assert r["ok"] and r["fixes"] == []  # 中间夹着一整个配对好的 if 块，不算跨
    for name in ("insert-reply-0a026ac.txt",):  # 8769：清零放错另说，计时点都放对了
        data = ins._json_block(reply(name))
        ids = {f"P{ins.number(s['p'], steps())}": s for s in steps()}
        for x in data["inserts"]:
            if x["step"] in ids:
                assert ins._same_block(SRC.resolve(), x["file"], int(x["after_line"]), ids[x["step"]], {}) is None, x["step"]


def _proj(tmp_path, text):
    (tmp_path / "main").mkdir(exist_ok=True)
    (tmp_path / "main" / "a.c").write_text(text, encoding="utf-8")


def _plan(*pts):
    return as_reply({"header": "main/fw_stopwatch.h", "inserts": [
        {"step": "include", "file": "main/a.c", "after_line": 1, "lines": ['#include "fw_stopwatch.h"']}] + [
        {"step": f"P{i + 1}", "file": "main/a.c", "after_line": a, "lines": [f"    FSW_MARK({i + 1}, 0);"]} for i, a in enumerate(pts)]})


def _st(*lw):
    return [{"p": f"P{i + 1}", "n": f"第{i + 1}步", "path": "main/a.c", "l": l, "where": w, "once": "once",
             "line": "start" if i == 0 else "talk"} for i, (l, w) in enumerate(lw)]


SRC_C = (
    "#include <stdio.h>\n"          # 1
    "int f(int x) {\n"              # 2
    "    if (x) {\n"                # 3
    "        a();\n"                # 4
    "        b();\n"                # 5
    "    } else {\n"                # 6
    "        c();\n"                # 7
    "    }\n"                       # 8
    "    switch (x) {\n"            # 9
    "    case 1:\n"                 # 10
    "        d();\n"                # 11
    "        break;\n"              # 12
    "    case 2:\n"                 # 13
    "        e();\n"                # 14
    "        break;\n"              # 15
    "    }\n"                       # 16
    "    if (!send(x)) {\n"         # 17
    "        g();\n"                # 18
    "    }\n"                       # 19
    "    return h();\n"             # 20
    "}\n")                          # 21


def test_legit_places_pass(tmp_path):
    _proj(tmp_path, SRC_C)
    # 块里最后一句之后（after 5）、return 之前（before 20）、写在 if 条件里的发送（before 17）、if 那一句之后进了这一支的第一句
    st = _st((5, "after"), (20, "before"), (17, "before"), (7, "before"), (14, "before"))
    r = ins.parse_plan(_plan(5, 19, 16, 6, 13), tmp_path, st)
    assert r["ok"] and r["fixes"] == []


def test_wrong_branch_and_wrong_case_are_moved(tmp_path):
    _proj(tmp_path, SRC_C)
    st = _st((5, "after"), (7, "before"), (14, "before"))
    r = ins.parse_plan(_plan(5, 5, 12), tmp_path, st)  # P2 进了 if 那一支，P3 进了 case 1
    assert r["ok"]
    assert {f["step"]: (f["after_line"], f["to"]) for f in r["fixes"]} == {"P2": (5, 6), "P3": (12, 13)}
    st = _st((5, "after"))
    r = ins.parse_plan(_plan(6), tmp_path, st)  # 块里最后一句"之后"写成了出块以后（} else { 那一行后面）
    assert r["ok"] and r["fixes"][0]["to"] == 5


def test_unclear_target_asks_once_then_uses_answer(tmp_path):
    """对应的那一句没写完（下一行接着写）：码表说不准该放哪，追问 AI 一次。"""
    _proj(tmp_path, "#include <stdio.h>\nint f(int x) {\n    if (x) {\n        a(x,\n          1);\n    }\n    b();\n}\n")
    st = _st((7, "before"), (4, "after"))
    bad = _plan(6, 6)  # P2 写在块外
    good = _plan(6, 5)
    llm = FakeLLM("", followups=[good])
    plan, again = ins.fix_plan(llm, bad, tmp_path, st)
    assert len(llm.asked) == 1 and "不在同一个代码块里" in llm.asked[0] and "第 6 行后面" in llm.asked[0]
    assert plan["ok"] and [(f["kind"], f["step"]) for f in plan["fixes"]] == [("rewritten", "P2")]
    plan, _ = ins.fix_plan(FakeLLM("", followups=[bad]), bad, tmp_path, st)
    assert not plan["ok"] and "码表追问它改了一次还是不对" in plan["message"]


def test_prompt_says_same_block():
    t = ins.build_prompt(steps(), "main/fw_stopwatch.h")
    assert "同一个代码块里" in t and "case 或 default" in t
    assert "同一个代码块里" in ins.PLAN_FIX
