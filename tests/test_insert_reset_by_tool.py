"""10-25 晚，连着三次插法都栽在"每轮清零放哪"上：8769 放进主循环开头、8771 放在 HandleStateChangedEvent 最开头
（每次换状态都清零，回复一开始这一轮就被切断，试一次只到 6/10）。一条条补检查是打地鼠。

改成和找关键步骤的"码表定位置、AI 只挑"一样：每轮清零由码表按找到的关键步骤放——
一轮的结束那一步（表里最后一个"一轮只经过一次"的点）；它那一句是把状态设成某个值、同一个文件里正好有一处处理这个值的 case，
就放进那个 case 的第一句前面（进入这个状态的每条路都经过，一轮一次）；否则放在结束那一步的计时点紧后面。
AI 写的清零一律不用。找不到能确定的地方才用 AI 的位置（照旧核对），并提醒。
重放 8767、8769、8770、8771 存下的插法（不调模型）：清零都落在第 1027 行「case kDeviceStateListening:」那一支里。
"""

import json
import shutil
from pathlib import Path

import pytest

from fsw import instrument as ins
from fsw.llm import FakeLLM

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"


def steps():
    return json.loads((HERE / "insert-steps-6e9c22a.json").read_text(encoding="utf-8"))


def resets(plan):
    return [(x["file"], x["after_line"]) for x in plan["inserts"] for l in x["lines"] if "FSW_ROUND_RESET" in l]


def test_round_end_is_the_last_once_per_round_step():
    assert ins.round_end(steps())["n"] == "回到聆听"


@pytest.mark.parametrize("name", ["insert-reply-00ab8bc.txt", "insert-reply-1742b64.txt", "insert-reply-0a026ac.txt"])
def test_saved_plans_get_the_reset_in_the_enter_listening_branch(name):
    r = ins.parse_plan((HERE / name).read_text(encoding="utf-8"), SRC, steps())
    assert r["ok"], r.get("message")
    assert resets(r) == [("main/application.cc", 1027)]  # 只有码表放的这一句
    f = [x for x in r["fixes"] if x["kind"] == "reset_by_tool"]
    assert f and all(x["to"] == 1027 for x in f) and "case kDeviceStateListening:" in f[0]["why"]
    assert any("每轮清零由码表放在" in n for n in r["notes"])


def test_8771_reset_at_top_of_state_handler_is_replaced():
    r = ins.parse_plan((HERE / "insert-reply-00ab8bc.txt").read_text(encoding="utf-8"), SRC, steps())
    f = next(x for x in r["fixes"] if x["kind"] == "reset_by_tool")
    assert f["after_line"] == 1000 and f["to"] == 1027


def test_good_8767_plan_also_gets_the_tool_reset():
    d = json.loads((HERE / "insert-plan-4e1e8da.json").read_text(encoding="utf-8"))
    r = ins.parse_plan(d["reply"], SRC, d["steps"])
    assert r["ok"] and resets(r) == [("main/application.cc", 1027)]
    assert {x["step"] for x in r["inserts"]} >= {"arm"}  # 武装照用 AI 的


def test_applied_reset_is_first_thing_in_the_listening_case(tmp_path):
    work = tmp_path / "src"
    shutil.copytree(SRC, work)
    r = ins.parse_plan((HERE / "insert-reply-00ab8bc.txt").read_text(encoding="utf-8"), work, steps())
    rec = ins.apply(work, r, tmp_path / "backup")
    assert rec["ok"], rec.get("message")
    lines = (work / "main/application.cc").read_text(encoding="utf-8").split("\n")
    k = next(i for i, l in enumerate(lines) if "case kDeviceStateListening:" in l and "FSW_ROUND_RESET" in lines[i + 2])
    assert lines[k + 2].strip() == "FSW_ROUND_RESET();"
    assert sum("FSW_ROUND_RESET();" in l for l in lines) == 1


def test_without_tool_spot_model_reset_at_function_top_before_a_switch_is_caught(monkeypatch):
    monkeypatch.setattr(ins, "place_reset", lambda *a, **k: None)
    r = ins.parse_plan((HERE / "insert-reply-00ab8bc.txt").read_text(encoding="utf-8"), SRC, steps())
    assert not r["ok"] and r["bad"][0]["step"] == "reset" and "最开头" in r["bad"][0]["why"]
    good = (HERE / "insert-reply-00ab8bc.txt").read_text(encoding="utf-8").replace('"after_line": 1000', '"after_line": 1027')
    llm = FakeLLM("", followups=[good])
    plan, again = ins.fix_plan(llm, (HERE / "insert-reply-00ab8bc.txt").read_text(encoding="utf-8"), SRC, steps())
    assert plan["ok"] and len(llm.asked) == 1 and resets(plan) == [("main/application.cc", 1027)]


def _proj(tmp_path, text):
    (tmp_path / "main").mkdir(exist_ok=True)
    (tmp_path / "main" / "a.c").write_text(text, encoding="utf-8")


def _reply(inserts):
    return "```json\n" + json.dumps({"header": "main/fw_stopwatch.h", "inserts": inserts}, ensure_ascii=False) + "\n```"


ST = [{"p": "P1", "n": "开始", "path": "main/a.c", "l": 3, "where": "before", "once": "once", "line": "start"},
      {"p": "P2", "n": "做完", "path": "main/a.c", "l": 7, "where": "before", "once": "once", "line": "talk"}]


def test_no_state_case_puts_reset_right_after_the_end_point(tmp_path):
    _proj(tmp_path, "#include <stdio.h>\nvoid on_start(void) {\n    begin();\n}\nvoid on_done(int ok) {\n    if (ok) {\n        finish();\n    }\n}\n")
    r = ins.parse_plan(_reply([
        {"step": "include", "file": "main/a.c", "after_line": 1, "lines": ['#include "fw_stopwatch.h"']},
        {"step": "P1", "file": "main/a.c", "after_line": 2, "lines": ["    FSW_ROUND_RESET();", "    FSW_MARK_ONCE(1, 0);"]},
        {"step": "P2", "file": "main/a.c", "after_line": 6, "lines": ["        FSW_MARK_ONCE(2, 0);"]}]), tmp_path, ST)
    assert r["ok"] and resets(r) == [("main/a.c", 6)]
    order = [x["step"] for x in r["inserts"] if x["after_line"] == 6]
    assert order == ["P2", "reset"]  # 先记结束那一步，再清零
    assert [l for x in r["inserts"] if x["step"] == "P1" for l in x["lines"]] == ["    FSW_MARK_ONCE(1, 0);"]  # AI 夹带的清零去掉了


def test_case_inside_a_loop_is_not_used(tmp_path):
    """case 在一直转的循环里（每一下定时都走）：不能放那儿，退回结束那一步旁边。"""
    _proj(tmp_path, "#include <stdio.h>\nvoid tick(void) {\n    begin();\n    while (1) {\n        switch (st) {\n        case READY:\n            idle();\n            break;\n        }\n    }\n}\nvoid on_done(void) {\n    set_state(READY);\n}\n")
    st = [ST[0], {**ST[1], "l": 13}]
    p = ins.place_reset(tmp_path, st)
    assert p and p["after_line"] == 12 and "case" not in p["where"]


def test_state_case_in_the_same_file_is_used(tmp_path):
    _proj(tmp_path, "#include <stdio.h>\nvoid on_start(void) {\n    begin();\n}\nvoid on_done(void) {\n    set_state(READY);\n}\n"
          "void on_state(int s) {\n    switch (s) {\n    case BUSY:\n        busy();\n        break;\n    case READY:\n        led_on();\n        break;\n    }\n}\n")
    st = [ST[0], {**ST[1], "l": 6}]
    p = ins.place_reset(tmp_path, st)
    assert p["after_line"] == 13 and "case READY:" in p["where"] and p["lines"][-1] == "        FSW_ROUND_RESET();"


def test_no_once_end_step_keeps_model_reset():
    from tests.test_instrument import STEPS
    assert ins.round_end(STEPS) is None


def test_prompt_carries_the_problem_seen_in_the_last_try():
    t = ins.build_prompt(steps(), "main/fw_stopwatch.h", extra="每轮清零那一句放错了：每一轮在「回复开始」之后就被清零")
    assert "上一次插完试的时候发现的问题" in t and "「回复开始」之后就被清零" in t
