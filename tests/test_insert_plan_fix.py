"""10-25 晚 8768：插计时点那一步，AI 在插法里夹带了一句 `if (task.playback_id == 0) FSW_ARM();`，码表整份不用，用户卡住。

改成：只插计时点、注释和 #include 的规矩不变；不合规的那几条去掉后每个计时点都还在、都能照常工作，就只去掉这几条；
会缺东西（比如去掉武装那一句，用"武装后才记"的点就永远不记了）就在同一段对话里追问 AI 一次，只改这几条；还不对才失败。
重放那次存下的 AI 回答（tests/fixtures/fullflow_multi_sentence/insert-reply-6e9c22a.txt），追问用假模型，不调用大模型。
"""

import json
import re
from pathlib import Path


from fsw import instrument as ins
from fsw.llm import FakeLLM
from tests.identify_fixtures import door_source
from tests.test_instrument import PLAN, STEPS, reply

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"


def saved():
    text = (HERE / "insert-reply-6e9c22a.txt").read_text(encoding="utf-8")
    steps = json.loads((HERE / "insert-steps-6e9c22a.json").read_text(encoding="utf-8"))
    return text, steps


def plan_of(text):
    return json.loads(re.search(r"```json\s*(.*?)```", text, re.S).group(1))


def rewritten(text):
    """假模型追问后的回答：去掉带 if 的武装那一句，首段出声改成"回复开始之后才记"。"""
    p = plan_of(text)
    p["inserts"] = [x for x in p["inserts"] if x["step"] != "arm"]
    for x in p["inserts"]:
        if x["step"] == "P9":
            x["lines"] = ["        // 计时点 P9：首段出声", "        FSW_MARK_ONCE_AFTER(9, 8, 0);"]
    return "改好了。\n```json\n" + json.dumps(p, ensure_ascii=False) + "\n```\n"


def test_saved_bad_plan_is_not_used_as_is_and_says_what_is_wrong():
    text, steps = saved()
    r = ins.parse_plan(text, SRC, steps)
    assert not r["ok"] and "一个文件都没改" in r["message"]
    assert [b["step"] for b in r["bad"]] == ["arm"] and "if (task.playback_id == 0) FSW_ARM();" in r["bad"][0]["lines"][1]
    assert any("武装" in x for x in r["lost"])  # 只去掉它：首段出声用的是"武装后才记"，就永远不记了，所以不能只去掉


def test_follow_up_once_rewrites_only_the_bad_entry_and_plan_is_used():
    text, steps = saved()
    llm = FakeLLM("", followups=[rewritten(text)])
    plan, again = ins.fix_plan(llm, text, SRC, steps)
    assert plan["ok"] and again and plan["asked"]
    assert len(llm.asked) == 1
    q = llm.asked[0]
    assert "if (task.playback_id == 0) FSW_ARM();" in q and "不能加 if" in q and "FSW_MARK_ONCE_AFTER" in q
    assert [f["kind"] for f in plan["fixes"]] == ["rewritten"] and plan["fixes"][0]["step"] == "arm"
    assert not any("if" in l for x in plan["inserts"] for l in x["lines"] if not l.strip().startswith("//"))
    assert "追问它改了一次" in plan["notes"][0]


def test_follow_up_still_bad_fails_with_plain_message():
    text, steps = saved()
    plan, again = ins.fix_plan(FakeLLM("", followups=[text]), text, SRC, steps)
    assert not plan["ok"] and again == text
    assert plan["message"].startswith("AI 给的插法里有不能用的地方，码表追问它改了一次还是不对，一个文件都没改：")


def test_no_follow_up_possible_fails_as_before():
    text, steps = saved()
    plan, again = ins.fix_plan(FakeLLM("", followups=None), text, SRC, steps)  # 这种接法不支持追问
    assert not plan["ok"] and again is None and not plan.get("asked")
    plan, again = ins.fix_plan(None, text, SRC, steps)  # 沿用的插法：没有对话可以追问
    assert not plan["ok"] and again is None


def test_bad_entry_that_nothing_needs_is_just_dropped(door=None):
    """多加的那条去掉后什么都不缺（比如武装那一句，但没有点用"武装后才记"）：只去掉它，其余照插，不追问。"""
    text, steps = saved()
    p = plan_of(text)
    for x in p["inserts"]:
        if x["step"] == "P9":
            x["lines"] = ["        // 计时点 P9：首段出声", "        FSW_MARK_ONCE_AFTER(9, 8, 0);"]
    t2 = "```json\n" + json.dumps(p, ensure_ascii=False) + "\n```"
    llm = FakeLLM("", followups=[])
    plan, again = ins.fix_plan(llm, t2, SRC, steps)
    assert plan["ok"] and again is None and llm.asked == []
    assert [(f["kind"], f["step"]) for f in plan["fixes"]] == [("dropped", "arm")]
    assert not any(x["step"] == "arm" for x in plan["inserts"])
    assert any("码表去掉了 AI 多加的一处" in n for n in plan["notes"])


def test_dropping_a_point_entry_is_never_silent(tmp_path):
    """计时点自己那条不合规：去掉就缺这个点，不能只去掉（老规矩照旧）。"""
    src = door_source(tmp_path / "door")
    bad = json.loads(json.dumps(PLAN))
    bad["inserts"][2]["lines"] = ["    FSW_MARK_ONCE(2, 0);", "    uid_ok = 0;"]
    r = ins.parse_plan(reply(bad), src, STEPS)
    assert not r["ok"] and "卡号读完" in r["message"] and r["bad"][0]["step"] == "P2"


def test_dropping_the_only_flush_of_a_keep_last_point_is_not_allowed(tmp_path):
    src = door_source(tmp_path / "door")
    p = json.loads(json.dumps(PLAN))
    p["inserts"][2]["lines"] = ["    FSW_KEEP_LAST(2);"]
    p["inserts"].append({"step": "flush", "file": "main/lock_ctrl.c", "after_line": 55, "lines": ["    if (ok) FSW_FLUSH_LAST(2);"]})
    r = ins.parse_plan(reply(p), src, STEPS)
    assert not r["ok"] and any("P2 记最后一次" in x for x in r["lost"])


def test_prompt_says_no_conditions_and_tool_already_ignores_before_start():
    text = ins.build_prompt(STEPS, "main/fw_stopwatch.h")
    assert "不能加 if" in text and "起点之后" in text and "不能套在 if 里" in text
    assert "比如提示音也走播放" not in text  # 不再引着 AI 自己去筛提示音

