"""10-25 晚 8769：插法 AI 把"每轮清零"放在主循环开头（application.cc 第 188 行 xEventGroupWaitBits 之后），
主循环每来一个事件就转一圈，一轮会清零几十上百次，实测会全乱。码表原来只核对插的是什么，不核对放在哪。

改成：清零、武装一轮只该经过一次，看结构（不认文件名、函数名）——直接在循环体里、或和逐帧逐包的计时点一起直接写在函数体里，
就不能用；能去掉就去掉（还有别的清零），去掉会缺就在同一段对话里追问 AI 一次挪地方，还不对才失败。
重放 8769 存下的 AI 回答（insert-reply-0a026ac.txt），追问用假模型；8767 那份放对了的插法（insert-plan-4e1e8da.json）照样能用。
"""

import json
import re
from pathlib import Path

from fsw import instrument as ins
from fsw.llm import FakeLLM

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"


def steps():
    return json.loads((HERE / "insert-steps-6e9c22a.json").read_text(encoding="utf-8"))


def bad_text():
    return (HERE / "insert-reply-0a026ac.txt").read_text(encoding="utf-8")


def plan_of(text):
    return json.loads(re.search(r"```json\s*(.*?)```", text, re.S).group(1))


def as_reply(p):
    return "```json\n" + json.dumps(p, ensure_ascii=False) + "\n```"


def moved(text):
    """假模型追问后的回答：清零挪到设备进入聆听状态那一支（8767 那份放的地方）。"""
    p = plan_of(text)
    for x in p["inserts"]:
        if x["step"] == "reset":
            x["after_line"] = 1029
            x["lines"] = ["            // 计时点：进入聆听，一轮开始，清零", "            FSW_ROUND_RESET();"]
    return as_reply(p)


def test_reset_in_main_event_loop_is_caught():
    r = ins.parse_plan(bad_text(), SRC, steps())
    assert not r["ok"] and "一个文件都没改" in r["message"]
    assert [b["step"] for b in r["bad"]] == ["reset"]
    why = r["bad"][0]["why"]
    assert "第 188 行后面的清零" in why and "while (true)" in why and "循环体里" in why
    assert "每轮清零那一句被去掉了" in r["lost"]  # 只有这一处清零：不能只去掉


def test_follow_up_moves_the_reset_and_plan_is_used():
    llm = FakeLLM("", followups=[moved(bad_text())])
    plan, again = ins.fix_plan(llm, bad_text(), SRC, steps())
    assert plan["ok"] and again and len(llm.asked) == 1
    q = llm.asked[0]
    assert "第 188 行后面的清零" in q and "一轮只经过一次的地方" in q and "主循环" in q
    assert [(f["kind"], f["step"]) for f in plan["fixes"]] == [("rewritten", "reset")]
    assert [x["after_line"] for x in plan["inserts"] if x["step"] == "reset"] == [1029]


def test_follow_up_that_keeps_the_reset_in_the_loop_fails_plainly():
    plan, _ = ins.fix_plan(FakeLLM("", followups=[bad_text()]), bad_text(), SRC, steps())
    assert not plan["ok"] and "码表追问它改了一次还是不对" in plan["message"] and "循环体里" in plan["message"]


def test_extra_reset_in_the_loop_is_just_dropped_when_another_one_is_fine():
    p = plan_of(bad_text())
    p["inserts"].append({"step": "reset", "file": "main/application.cc", "after_line": 1029,
                         "lines": ["            // 计时点：进入聆听，清零", "            FSW_ROUND_RESET();"]})
    llm = FakeLLM("", followups=[])
    plan, again = ins.fix_plan(llm, as_reply(p), SRC, steps())
    assert plan["ok"] and again is None and llm.asked == []
    assert [(f["kind"], f["after_line"]) for f in plan["fixes"]] == [("dropped", 188)]
    assert [x["after_line"] for x in plan["inserts"] if x["step"] == "reset"] == [1029]
    assert any("码表去掉了 AI 多加的一处" in n for n in plan["notes"])


def test_reset_and_arm_in_once_per_round_branches_are_fine():
    """8767 那份插法：清零在进入聆听状态那一支，武装在回复开始那一支（都在会被反复调用的函数里，但在某一支里）——照样能用。"""
    d = json.loads((HERE / "insert-plan-4e1e8da.json").read_text(encoding="utf-8"))
    r = ins.parse_plan(d["reply"], SRC, d["steps"])
    assert r["ok"] and r["fixes"] == []
    assert {x["step"] for x in r["inserts"]} >= {"reset", "arm"}


def test_reset_at_top_of_a_per_packet_function_is_caught(tmp_path):
    """逐包执行的函数（里面直接写着一轮记第一次的点）开头放清零：每一包都清零一次。"""
    (tmp_path / "main").mkdir()
    (tmp_path / "main" / "net.c").write_text(
        "#include <stdio.h>\n"
        "void on_packet(int n) {\n"
        "    send(n);\n"
        "}\n"
        "void on_ready(void) {\n"
        "    start();\n"
        "}\n", encoding="utf-8")
    st = [{"p": "P1", "n": "准备好", "path": "main/net.c", "l": 6, "where": "before", "once": "once", "line": "start"},
          {"p": "P2", "n": "发出一包", "path": "main/net.c", "l": 3, "where": "before", "once": "first", "line": "talk"}]
    p = {"header": "main/fw_stopwatch.h", "inserts": [
        {"step": "include", "file": "main/net.c", "after_line": 1, "lines": ['#include "fw_stopwatch.h"']},
        {"step": "P1", "file": "main/net.c", "after_line": 5, "lines": ["    FSW_MARK_ONCE(1, 0);"]},
        {"step": "reset", "file": "main/net.c", "after_line": 2, "lines": ["    FSW_ROUND_RESET();"]},
        {"step": "P2", "file": "main/net.c", "after_line": 2, "lines": ["    FSW_MARK_ONCE(2, 0);"]}]}
    r = ins.parse_plan(as_reply(p), tmp_path, st)
    assert not r["ok"] and r["bad"][0]["step"] == "reset" and "发出一包" in r["bad"][0]["why"]
    p["inserts"][2]["after_line"] = 5  # 放在一轮只走一次的地方：能用
    assert ins.parse_plan(as_reply(p), tmp_path, st)["ok"]


def test_prompt_says_where_reset_goes_and_last_points_use_keep_last():
    t = ins.build_prompt(steps(), "main/fw_stopwatch.h")
    assert "一轮只经过一次的地方" in t and "不能放在主循环、事件循环" in t
    assert "不用 FSW_MARK" in t
