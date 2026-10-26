"""10-26 8783（fdf81d5）烧录前的插法：每轮清零放在主循环里"上报播放结束"那一支（application.cc 第 217 行，判断"播放放空"事件位）。
这个事件位是"队列播放放空"那一句调用的回调每走一次就置一次的，回复分几句播时一轮里会走好几次：清零每句之间都清一次。
识别时这一步标了最后结果、不在候选里，追问也没改过来，可还是带着往下走了。

改成通用的（不认文件名、函数名、事件名）：
1. 一轮只记一次的点，插的那一句是跟着一个"一轮会走好几次"的点触发的（那一句调用的回调被赋成的 lambda 里置的事件位、
   这个点在判断它的那一支里；或者就在那一句直接调用的函数里）：当成每次都记，不当一轮结束、不在那里清零；
   它是人感觉到的最后一下时取这一轮最后那次。清零挪到一轮只走一次的地方（这里是结束消息那一步 → 进入聆听的 case）。
2. 量的终点照旧到 measure 说的"播放结束"：结束那一步也是感觉到的最后一下之一时，和别的几个合成一段取最晚的。
3. AI 的说明要和最后插的对得上：提到的语句没插的那几句去掉。
重放存下的回答（不调用大模型、不碰设备）；源码用 src/（和 8783 那次这几份文件一字不差）。
"""

import json
from pathlib import Path

from fsw import instrument as ins
from fsw import standards as stds

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"
STEPS = json.loads((HERE / "insert-steps-fdf81d5.json").read_text(encoding="utf-8"))
REPLY = (HERE / "insert-reply-fdf81d5.txt").read_text(encoding="utf-8")
IDENT = json.loads((HERE / "identify-fdf81d5.json").read_text(encoding="utf-8"))
FLOW = {"hint": None, "end": IDENT["main_end"], "segs": IDENT["segs"], "follow": []}


def resets(plan):
    return [(x["file"], x["after_line"]) for x in plan["inserts"] for l in x["lines"] if "FSW_ROUND_RESET" in l]


def lines_of(plan, step):
    return [l.strip() for x in plan["inserts"] if x["step"] == step for l in x["lines"]]


# ---------- 1. 跟着"一轮会走好几次"的点触发的点 ----------
def test_report_drained_follows_queue_drained_through_the_event_bit():
    d = ins.repeat_driven(SRC, STEPS)
    assert list(d) == ["P15"] and d["P15"]["by"] == "P14"
    assert "MAIN_EVENT_PLAYBACK_DRAINED" in d["P15"]["why"]
    eff, _ = ins.effective_steps(SRC, STEPS)
    assert next(x for x in eff if x["p"] == "P15")["once"] == "every"
    assert ins.round_end(eff)["p"] == "P13"           # 一轮结束：结束消息（切回聆听），不是上报播放结束
    assert ins.round_end(STEPS)["p"] == "P15"         # 不看结构时就是 8783 那次的样子


def test_8783_plan_resets_once_per_round_in_the_enter_listening_case():
    r = ins.parse_plan(REPLY, SRC, STEPS, None, FLOW)
    assert r["ok"], r.get("message")
    assert r["end"]["step"] == "P13"
    assert resets(r) == [("main/application.cc", 1027)]     # case kDeviceStateListening: 那一支，一轮一次
    src = (SRC / "main/application.cc").read_text(encoding="utf-8").splitlines()
    assert src[1026].strip() == "case kDeviceStateListening:"
    assert lines_of(r, "P15")[-1] == "FSW_MARK(15, 0);"    # 每次都记，取最后那次
    assert lines_of(r, "P13")[-1] == "FSW_MARK_ONCE(13, 0);"
    assert {t["step"] for t in r["tail"]} >= {"P14", "P15"}  # 清零之后才到的放空、上报：照样记回这一轮
    assert any("FSW_KEEP_LAST(12)" in l for l in lines_of(r, "P12"))  # 记最后一次的点照旧，清零时补打
    kinds = {f["kind"] for f in r["fixes"]}
    assert {"every_by_tool", "reset_by_tool"} <= kinds
    assert any("跟着「队列播放放空」走" in n for n in r["notes"])
    # 说明和实际插的对得上：没有一句还说清零放在 217 那一支、或提到没插的语句
    assert not any("ROUND_RESET" in n and "1027" not in n for n in r["notes"])


def test_no_source_means_no_change():
    assert ins.effective_steps(None, STEPS) == (STEPS, {})


def test_once_step_in_a_branch_set_by_a_once_step_is_not_touched(tmp_path):
    (tmp_path / "main").mkdir()
    (tmp_path / "main/a.cc").write_text(
        "#include <x.h>\n"
        "void setup() {\n"
        "    cb.on_done = [this]() {\n"
        "        set_bits(EV_DONE);\n"
        "    };\n"
        "}\n"
        "void loop(int bits) {\n"
        "    if (bits & EV_DONE) {\n"
        "        report();\n"
        "    }\n"
        "}\n"
        "void work() {\n"
        "    begin();\n"
        "    cb.on_done();\n"
        "}\n", encoding="utf-8")
    st = [{"p": "P1", "n": "开始", "path": "main/a.cc", "l": 13, "where": "before", "once": "once", "line": "start"},
          {"p": "P2", "n": "做完", "path": "main/a.cc", "l": 14, "where": "before", "once": "once", "line": "talk"},
          {"p": "P3", "n": "上报", "path": "main/a.cc", "l": 9, "where": "before", "once": "once", "line": "talk"}]
    assert ins.repeat_driven(tmp_path, st) == {}
    st[1]["once"] = "every"  # 做完那一句一轮会走好几次：上报跟着走好几次
    assert ins.repeat_driven(tmp_path, st) == {"P3": {"by": "P2", "why": ins.repeat_driven(tmp_path, st)["P3"]["why"]}}
    assert ins.round_end(ins.effective_steps(tmp_path, st)[0]) is None or ins.round_end(ins.effective_steps(tmp_path, st)[0])["p"] != "P3"


def test_notes_that_mention_statements_not_inserted_are_dropped():
    out = [{"step": "P1", "lines": ["FSW_MARK_ONCE(1, 0);"]}, {"step": "reset", "lines": ["FSW_ROUND_RESET();"]}]
    notes = ["P1 用 FSW_MARK_ONCE(1, 0)", "在 P8 处 FSW_FLUSH_LAST(7) 打印", "清零 FSW_ROUND_RESET() 放在主循环", "普通说明"]
    kept = ins._notes_match(notes, out, auto={"file": "a", "after_line": 1})
    assert kept[:2] == ["P1 用 FSW_MARK_ONCE(1, 0)", "普通说明"] and "2 句" in kept[-1]
    assert ins._notes_match(["清零 FSW_ROUND_RESET() 放在主循环"], out, auto=None) == ["清零 FSW_ROUND_RESET() 放在主循环"]


# ---------- 2. 量的终点照旧到播放结束 ----------
def test_measure_still_ends_at_playback_end_when_round_end_is_the_end_message():
    page = {"steps": IDENT["page_steps"], "segs": IDENT["segs"], "main": IDENT["page_main"], "measure": IDENT["measure"], "roundEnd": "P13"}
    sc = stds.real_scenario(page, None)
    full = next(l for l in sc["lines"] if l["name"].startswith("全程"))
    assert full["name"] == "全程：说话结束 → 播放结束"
    last = sc["segments"][full["segments"][-1]]
    assert last["to"] == "P15" and set(last["to_any"]) == {"P13", "P14", "P15"}
    assert "结束消息" not in last["name"]  # 段名按量到的那一步叫
