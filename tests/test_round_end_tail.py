"""每轮清零放好之后，再核对"这一轮的点会不会在清零之后才打出来"（8cde497 之后补的几处，所有设备通用，不认名字）：

1. 一轮会走好几次的点（记最后一次、每次都记），从它到结束那一步之间没有一轮只走一次的步骤卡着（或分段里它和结束并排），
   最后那次和结束谁先谁后说不准：码表把它们写进头文件的 FSW_TAIL_MASK，清零后 1 秒内走到的还算上一轮
   （记最后一次的补打 LATE，每次都记的照常打印）；更晚的打一行 FSW,TAIL，试一次点名它晚了多久，
   一轮只走一次的那种，码表重插时把清零挪到它后面。
2. 每轮结束那一步和识别结果（识别说的结束、分段）对不上：不自己放清零，大白话说清楚。
3. 好几处 case 同一个状态：挑结束那一句调用的函数走得到的那个；说不准照旧放在结束那一步旁边、提醒一句。
4. 结构看不懂的文件（大括号对不上）：照样插，fsw-plan.json 记"没核对"，看改动那页直接写出来。
5. 重插时告诉 AI 的那句话存在项目文件夹里（码表重开也在），提前准备插法时也带上。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fsw import instrument as ins
from fsw import structure
from fsw.llm import FakeLLM
from tests.test_fsw_api import FakeDevice, run_to_try, setup, wait  # noqa: F401
from tests.test_instrument import STEPS

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
XZ_STEPS = json.loads((HERE / "insert-steps-6e9c22a.json").read_text(encoding="utf-8"))
# 8772 那次识别存下的分段（P9 之后三段并排：末段出声、等回复结束、播放放空）和"这条线到「回到聆听」结束"
XZ_FLOW = {"end": "回到聆听", "segs": [
    {"a": "P1", "b": "P2"}, {"a": "P2", "b": "P5"}, {"a": "P5", "b": "P6"}, {"a": "P6", "b": "P7"}, {"a": "P7", "b": "P8"},
    {"a": "P8", "b": "P9"}, {"a": "P9", "b": "P10"}, {"a": "P9", "b": "P12"}, {"a": "P9", "b": "P11"}]}


# ---------- 1. 哪些点可能在清零之后才打出来 ----------
def test_xiaozhi_last_output_and_queue_empty_may_come_after_the_end_but_upload_last_packet_cannot():
    assert ins.end_check(XZ_STEPS, XZ_FLOW) == []
    assert [s["p"] for s in ins.tail_points(XZ_STEPS, XZ_FLOW)] == ["P10", "P11"]
    assert [s["p"] for s in ins.tail_points(XZ_STEPS)] == ["P10", "P11"]  # 没有分段也一样（只看先后）


def test_replayed_plans_carry_the_tail_into_the_plan_and_header():
    for name in ("insert-reply-6afa2c1.txt", "insert-reply-00ab8bc.txt", "insert-reply-1742b64.txt", "insert-reply-0a026ac.txt"):
        r = ins.parse_plan((HERE / name).read_text(encoding="utf-8"), HERE / "src", XZ_STEPS, None, XZ_FLOW)
        assert r["ok"], name
        assert [x["step"] for x in r["tail"]] == ["P10", "P11"] and r["end"] == {"step": "P12", "name": "回到聆听"}
        assert any("可能在每轮清零之后才打出来" in n for n in r["notes"])
        h = ins.header_text(r["start"], [x["step"] for x in r["tail"]])
        assert f"#define FSW_TAIL_MASK 0x{(1 << 10) | (1 << 11):X}UL" in h


def test_point_before_the_reset_in_the_same_function_counts_as_checked(tmp_path):
    root = state_project(tmp_path)
    steps = state_steps(root, extra=[{"p": "P4", "n": "状态变了", "path": "main/app.c", "l": find(root, 'printf("changed'),
                                      "where": "before", "once": "every", "line": "talk"}])
    auto = ins.place_reset(root, steps)
    assert auto and auto["after_line"] == find(root, "case kListening:")  # 走得到的那一处 case
    assert [s["p"] for s in ins.tail_points(steps)] == ["P2", "P4"]
    assert [x["step"] for x in ins.after_reset(root, steps, auto)] == ["P2"]  # P4 在同一个函数里、清零之前按顺序执行


# ---------- 头文件：清零后的尾巴 ----------
cc = pytest.mark.skipif(not shutil.which("cc"), reason="这台电脑没有 C 编译器")


def run_c(tmp_path, body, start=1, tail=("P8", "P9")):
    (tmp_path / "fw_stopwatch.h").write_text(ins.header_text(start, list(tail)), encoding="utf-8")
    (tmp_path / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n", encoding="utf-8")
    (tmp_path / "m.c").write_text('#include "fw_stopwatch.h"\nint64_t now_us = 1000;\nint64_t esp_timer_get_time(void){return now_us;}\n'
                                  f"int main(void){{{body}return 0;}}\n", encoding="utf-8")
    subprocess.run(["cc", "m.c", "-o", "m", "-I.", "-Wall", "-Werror"], cwd=tmp_path, check=True)
    return subprocess.run(["./m"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.splitlines()


@cc
def test_last_value_after_reset_within_one_second_is_printed_as_late_with_running_count(tmp_path):
    body = ("FSW_MARK_ONCE(1,0);now_us=2000;FSW_KEEP_LAST(8);now_us=3000;FSW_KEEP_LAST(8);now_us=4000;FSW_ROUND_RESET();"
            "now_us=500000;FSW_KEEP_LAST(8);now_us=900000;FSW_KEEP_LAST(8);"
            "now_us=5000000;FSW_MARK_ONCE(1,0);FSW_KEEP_LAST(8);")
    assert run_c(tmp_path, body) == ["FSW,P1,1000,0", "FSW,LATE,P8,3000,2", "FSW,RESET,4000,0",
                                     "FSW,LATE,P8,500000,3", "FSW,LATE,P8,900000,4", "FSW,P1,5000000,0"]


@cc
def test_late_after_one_second_prints_one_tail_line_and_every_point_prints_each_time_inside_the_window(tmp_path):
    body = ("FSW_MARK_ONCE(1,0);now_us=4000;FSW_ROUND_RESET();"
            "now_us=10000;FSW_MARK(9,0);now_us=20000;FSW_MARK(9,0);"
            "now_us=2000000;FSW_KEEP_LAST(8);FSW_KEEP_LAST(8);FSW_MARK(9,0);FSW_MARK(9,0);")
    assert run_c(tmp_path, body) == ["FSW,P1,1000,0", "FSW,RESET,4000,0", "FSW,P9,10000,0", "FSW,P9,20000,0",
                                     "FSW,TAIL,P8,2000000,0", "FSW,P9,2000000,0"]


@cc
def test_reset_of_a_round_without_start_opens_no_tail(tmp_path):
    body = "FSW_ROUND_RESET();now_us=2000;FSW_KEEP_LAST(8);FSW_MARK(9,0);FSW_MARK(9,0);now_us=3000000;FSW_KEEP_LAST(8);"
    assert run_c(tmp_path, body) == ["FSW,RESET,1000,0", "FSW,P9,2000,0"]


@cc
def test_no_tail_mask_behaves_as_before(tmp_path):
    body = "FSW_MARK_ONCE(1,0);now_us=4000;FSW_ROUND_RESET();now_us=5000;FSW_KEEP_LAST(8);FSW_MARK(9,0);FSW_MARK(9,0);"
    assert run_c(tmp_path, body, tail=()) == ["FSW,P1,1000,0", "FSW,RESET,4000,0", "FSW,P9,5000,0"]


# ---------- 2. 结束那一步和识别结果对不上 ----------
def test_identified_end_is_an_earlier_once_step_so_tool_does_not_place_the_reset(tmp_path):
    root = state_project(tmp_path)
    steps = state_steps(root, extra=[{"p": "P4", "n": "收到回复", "path": "main/app.c", "l": find(root, 'printf("reply'),
                                      "where": "before", "once": "once", "line": "talk"}], end_last=True)
    flow = {"end": "收到回复"}
    bad = ins.end_check(steps, flow)
    assert bad and "「收到回复」" in bad[0] and "「回到聆听」" in bad[0]
    warn = []
    assert ins.place_reset(root, steps, flow, warn) is None and warn == bad
    r = ins.parse_plan(reply_with_reset(root, steps), root, steps, None, flow)
    assert r["ok"] and any(f["kind"] == "reset_end_unsure" for f in r["fixes"])
    assert any("每轮结束是哪一步说不准" in c for c in ins.plan_checks(r))
    assert r["tail"] == [] and r["end"] is None


def test_segment_after_the_end_and_reversed_order_are_flagged(tmp_path):
    root = state_project(tmp_path)
    steps = state_steps(root, extra=[{"p": "P4", "n": "收到回复", "path": "main/app.c", "l": find(root, 'printf("reply'),
                                      "where": "before", "once": "once", "line": "talk"}], end_last=True)
    bad = ins.end_check(steps, {"segs": [{"a": "P3", "b": "P4"}]})
    assert any("「回到聆听」后面还接着一轮只走一次的「收到回复」" in b for b in bad)
    steps2 = state_steps(root, extra=[{"p": "P4", "n": "收到回复", "path": "main/app.c", "l": find(root, 'printf("reply'),
                                       "where": "before", "once": "first", "line": "talk"}], end_last=False)
    assert any("正好反过来" in b for b in ins.end_check(steps2, {"segs": [{"a": "P4", "b": "P3"}]}))
    assert ins.end_check(steps, {"end": "回到聆听", "segs": [{"a": "P1", "b": "P3"}]}) == []


def test_side_line_step_after_the_end_is_not_the_round_end():
    steps = [*XZ_STEPS, {"p": "P14", "n": "亮灯", "path": "main/application.cc", "l": 680, "where": "before", "once": "once", "line": "face"}]
    assert ins.round_end(steps)["p"] == "P12"


# ---------- 3. 好几处 case ----------
def test_two_cases_pick_the_one_reachable_from_the_end_statement(tmp_path):
    root = state_project(tmp_path)
    steps = state_steps(root)
    first = find(root, "case kListening:")
    auto = ins.place_reset(root, steps)
    assert auto["after_line"] == first and "case kListening" in auto["where"]


def test_two_reachable_cases_fall_back_next_to_the_end_with_a_warning(tmp_path):
    root = state_project(tmp_path, both=True)
    steps = state_steps(root)
    warn = []
    auto = ins.place_reset(root, steps, None, warn)
    end_line = find(root, "SetState(kListening);")
    assert auto["after_line"] == end_line - 1 and warn and "2 处「case kListening:」" in warn[0] and "好几处都走得到" in warn[0]


# ---------- 4. 结构看不懂的文件 ----------
def test_if_else_branches_that_each_open_a_brace_are_read_as_one():
    src = structure.Source("void f(int a) {\n#if A\n  if (a) {\n#else\n  if (!a) {\n#endif\n    g();\n  }\n#if 0\n  }}\n#endif\n}\n")
    assert src.balanced and [b["kind"] for b in src.blocks] == ["function", "branch"]


def test_unreadable_file_is_inserted_but_recorded_as_unchecked(tmp_path):
    root = state_project(tmp_path)
    (root / "main/app.c").write_text((root / "main/app.c").read_text() + "#define OPEN {\nvoid h(void) OPEN }\n}\n")
    steps = state_steps(root)
    r = ins.parse_plan(reply_with_reset(root, steps), root, steps)
    assert r["ok"] and r["unchecked"] and r["unchecked"][0]["file"] == "main/app.c"
    assert any("没核对" in c and "main/app.c" in c for c in ins.plan_checks(r))
    assert any("码表看不懂这个文件的结构" in n for n in r["notes"])


# ---------- 试一次：清零之后才打出来的点 ----------
STEPS_TAIL = [STEPS[0], {**STEPS[1], "once": "every"}, {**STEPS[2], "once": "once"}]  # 「卡号读完」每次都记、排在结束前
STEPS_PAR = [STEPS[0], {**STEPS[1], "once": "once"}, {**STEPS[2], "once": "once"}]  # 「卡号读完」和结束并排（看分段）


def insert(c, r, steps):
    c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": steps, "chip": "ESP32-S3"})
    return wait(c, lambda j: j["status"] in ("inserted", "failed"))


def test_repeated_point_two_seconds_after_reset_is_named(setup):
    c, r, *_ = setup(device=FakeDevice(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P5,30,0", "FSW,RESET,50,0",
                                        "FSW,P2,2000100,0", "FSW,RESET,2000200,0"]))
    j = insert(c, r, STEPS_TAIL)
    assert j["status"] == "inserted" and any("可能在每轮清零之后才打出来" in n for n in j["notes"])
    j = run_to_try(c)
    m = j["err"]["message"]
    assert j["status"] == "failed" and "「卡号读完」走到了" in m and "一轮会走好几次" in m and "2.0 秒" in m and "「发出开锁」" in m


def test_tail_line_from_device_is_named_too(setup):
    c, r, *_ = setup(device=FakeDevice(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P5,30,0", "FSW,RESET,50,0",
                                        "FSW,TAIL,P2,3500050,0", "FSW,RESET,3600000,0"]))
    insert(c, r, STEPS_TAIL)
    j = run_to_try(c)
    assert j["status"] == "failed" and "3.5 秒" in j["err"]["message"]


def test_repeated_point_within_one_second_after_reset_still_counts(setup):
    c, r, *_ = setup(device=FakeDevice(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P5,30,0", "FSW,RESET,50,0", "FSW,P2,500050,0"]))
    insert(c, r, STEPS_TAIL)
    assert run_to_try(c)["status"] == "done"


def test_once_point_after_reset_moves_the_reset_behind_it_on_reinsert(setup, monkeypatch):
    prompts = spy(monkeypatch)
    c, r, *_ = setup(device=FakeDevice(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P5,30,0", "FSW,RESET,50,0",
                                        "FSW,P2,3000050,0", "FSW,RESET,3000100,0"]))
    pd = Path(r["project_dir"])
    (pd / "identify").mkdir(parents=True, exist_ok=True)
    (pd / "identify" / "x.json").write_text(json.dumps({"main": {"end": "发出开锁"}, "page": {"segs": [
        {"a": "P1", "b": "P2"}, {"a": "P1", "b": "P5"}]}}), encoding="utf-8")
    j = insert(c, r, STEPS_PAR)
    assert j["status"] == "inserted"
    j = run_to_try(c)
    m = j["err"]["message"]
    assert j["status"] == "failed" and "一轮其实到「卡号读完」才完" in m and "3.0 秒" in m
    redo = json.loads((pd / "fsw-redo.json").read_text(encoding="utf-8"))
    assert redo["end"] == {"end": "P2", "name": "卡号读完"} and "卡号读完" in redo["note"]
    n = len(prompts)
    j = insert(c, r, STEPS_PAR)
    assert j["status"] == "inserted" and len(prompts) == n + 1 and "一轮其实到「卡号读完」才完" in prompts[-1]
    places = [p for p in j["view"]["places"] if p["step"] == "reset"]
    assert [(p["file"], p["at"]) for p in places] == [("main/rfid_task.c", 131)]
    plan = json.loads((pd / "fsw-plan.json").read_text(encoding="utf-8"))
    assert plan["end"] == {"step": "P2", "name": "卡号读完"}
    assert "note" not in json.loads((pd / "fsw-redo.json").read_text(encoding="utf-8"))  # 用掉了；结束那一步留着


# ---------- 5. 重插时告诉 AI 的那句话存在磁盘上 ----------
def spy(monkeypatch):
    prompts = []
    orig = FakeLLM.investigate

    def f(self, prompt, root, on_text=None, on_tool=None):
        prompts.append(prompt)
        return orig(self, prompt, root, on_text=on_text, on_tool=on_tool)
    monkeypatch.setattr(FakeLLM, "investigate", f)
    return prompts


def test_note_on_disk_survives_restart_and_goes_into_the_prompt(setup, monkeypatch):
    prompts = spy(monkeypatch)
    c, r, *_ = setup()
    pd = Path(r["project_dir"])
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "fsw-redo.json").write_text(json.dumps({"note": "上次每轮在「卡号读完」之后就被清零"}), encoding="utf-8")
    j = insert(c, r, STEPS)
    assert j["status"] == "inserted" and "上次每轮在「卡号读完」之后就被清零" in prompts[-1]
    assert "note" not in json.loads((pd / "fsw-redo.json").read_text(encoding="utf-8"))
    plan = json.loads((pd / "fsw-plan.json").read_text(encoding="utf-8"))
    assert {"unchecked", "tail", "end"} <= set(plan)


def test_background_prepare_uses_the_note_and_insert_takes_it(setup, monkeypatch):
    prompts = spy(monkeypatch)
    c, r, *_ = setup()
    pd = Path(r["project_dir"])
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "fsw-redo.json").write_text(json.dumps({"note": "编译不过：P2 放错"}), encoding="utf-8")
    body = {"work": r["work"], "project_dir": r["project_dir"], "steps": STEPS, "chip": "ESP32-S3"}
    c.post("/api/fsw/prepare", json=body)
    import time
    t = time.time()
    while c.get("/api/fsw/prepare").json()["status"] == "running" and time.time() - t < 5:
        time.sleep(0.02)
    assert c.get("/api/fsw/prepare").json()["status"] == "ready" and "编译不过：P2 放错" in prompts[-1]
    n = len(prompts)
    c.post("/api/fsw/insert", json=body)
    j = wait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted" and j["prepared"] and len(prompts) == n


# ---------- 小项目：两个函数里各有一处 case kListening ----------
APP = """#include <stdio.h>
typedef enum { kIdle, kListening, kSpeaking } State;
static State state_;
void HandleStateChanged(State s) {
    printf("changed\\n");
    switch (s) {
        case kListening:
            printf("listen\\n");
            break;
        default:
            break;
    }
}
void LogState(State s) {
    switch (s) {
        case kListening:
            printf("log\\n");
            break;
        default:
            break;
    }
}
void SetState(State s) {
    state_ = s;
    HandleStateChanged(s);%s
}
void OnTtsStop(void) {
    if (state_ == kSpeaking) {
        printf("reply\\n");
        SetState(kListening);
    }
}
void OnPacket(void) {
    printf("pkt\\n");
}
void OnSpeechEnd(void) {
    printf("end\\n");
}
"""


def state_project(tmp_path, both=False):
    root = tmp_path / "proj"
    (root / "main").mkdir(parents=True)
    (root / "main/app.c").write_text(APP % ("\n    LogState(s);" if both else ""), encoding="utf-8")
    return root


def find(root, text):
    return next(i for i, l in enumerate((root / "main/app.c").read_text().split("\n"), 1) if text in l)


def state_steps(root, extra=(), end_last=True):
    s = [{"p": "P1", "n": "说完", "path": "main/app.c", "l": find(root, 'printf("end'), "where": "before", "once": "once", "line": "start"},
         {"p": "P2", "n": "收到一包", "path": "main/app.c", "l": find(root, 'printf("pkt'), "where": "before", "once": "last", "line": "talk"}]
    end = {"p": "P3", "n": "回到聆听", "path": "main/app.c", "l": find(root, "SetState(kListening);"), "where": "before", "once": "once", "line": "talk"}
    return s + list(extra) + [end] if end_last else s + [end] + list(extra)


def reply_with_reset(root, steps):
    ins_ = [{"step": "include", "file": "main/app.c", "after_line": 1, "lines": ['#include "fw_stopwatch.h"']}]
    calls = {"once": "FSW_MARK_ONCE({n}, 0);", "first": "FSW_MARK_ONCE({n}, 0);", "last": "FSW_KEEP_LAST({n});", "every": "FSW_MARK({n}, 0);"}
    for s in steps:
        n = int(s["p"][1:])
        ins_.append({"step": s["p"], "file": "main/app.c", "after_line": s["l"] - 1,
                     "lines": [f"    // 计时点 {s['p']}：{s['n']}", "    " + calls[s["once"]].format(n=n)]})
    ins_.append({"step": "reset", "file": "main/app.c", "after_line": find(root, 'printf("listen') - 1,
                 "lines": ["            // 清零", "            FSW_ROUND_RESET();"]})
    return "```json\n" + json.dumps({"header": "main/fw_stopwatch.h", "inserts": ins_, "notes": []}, ensure_ascii=False) + "\n```"
