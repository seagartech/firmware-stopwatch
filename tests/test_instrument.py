"""任务 14：插计时点（AI 只给"插在哪、插什么"，码表自己插、核对只加不删）、还原逐字节一样、编译出错找到是哪一处。

不真调用大模型；计时点工具头文件用本机编译器真编一次（C 和 C++ 两种文件一起链接），确认格式和"只记第一次"。
"""

import json
import shutil
import subprocess

import pytest

from fsw import instrument as ins
from fsw.llm import FakeLLM
from tests.identify_fixtures import door_source

STEPS = [
    {"p": "P1", "n": "读到卡", "path": "main/rfid_task.c", "l": 88, "where": "after", "once": "last", "line": "start"},
    {"p": "P2", "n": "卡号读完", "path": "main/rfid_task.c", "l": 131, "where": "after", "once": "first", "line": "talk"},
    {"p": "P5", "n": "发出开锁", "path": "main/lock_ctrl.c", "l": 55, "where": "after", "once": "first", "line": "talk"},
]

PLAN = {
    "header": "main/fw_stopwatch.h",
    "inserts": [
        {"step": "include", "file": "main/rfid_task.c", "after_line": 0, "lines": ['#include "fw_stopwatch.h"']},
        {"step": "P1", "file": "main/rfid_task.c", "after_line": 88, "lines": ["        // 计时点 P1：读到卡", "        FSW_ROUND_RESET();", "        FSW_MARK(1, 0);"]},
        {"step": "P2", "file": "main/rfid_task.c", "after_line": 131, "lines": ["    // 计时点 P2：卡号读完", "    FSW_MARK_ONCE(2, 0);"]},
        {"step": "include", "file": "main/lock_ctrl.c", "after_line": 0, "lines": ['#include "fw_stopwatch.h"']},
        {"step": "P5", "file": "main/lock_ctrl.c", "after_line": 55, "lines": ["    // 计时点 P5：发出开锁", "    FSW_MARK_ONCE(5, 0);"]},
    ],
    "notes": [],
}


def reply(plan=PLAN):
    return "插好了。\n```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```\n"


@pytest.fixture
def door(tmp_path):
    return door_source(tmp_path / "door")


# ---------- 计时点工具头文件：C 和 C++ 都能用，总开关关掉等于没插 ----------
@pytest.mark.skipif(not shutil.which("cc") or not shutil.which("c++"), reason="这台电脑没有 C 编译器")
def test_header_works_in_c_and_cpp_together(tmp_path):
    (tmp_path / "fw_stopwatch.h").write_text(ins.HEADER, encoding="utf-8")
    (tmp_path / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\n#ifdef __cplusplus\nextern \"C\"\n#endif\nint64_t esp_timer_get_time(void);\n", encoding="utf-8")
    (tmp_path / "a.c").write_text('#include "fw_stopwatch.h"\nint64_t esp_timer_get_time(void){static int64_t t=1000;return t+=10;}\n'
                                  'void in_c(void){FSW_MARK_ONCE(2, 7);}\n', encoding="utf-8")
    (tmp_path / "b.cc").write_text('#include "fw_stopwatch.h"\nextern "C" void in_c(void);\n'
                                   'int main(){FSW_ROUND_RESET();FSW_KEEP_LAST(1);FSW_KEEP_LAST(1);in_c();in_c();FSW_FLUSH_LAST(1);'
                                   'FSW_MARK_ONCE_ARMED(3,0);FSW_ARM();FSW_MARK_ONCE_ARMED(3,0);FSW_MARK_ONCE_ARMED(3,0);FSW_MARK(4,1);return 0;}\n', encoding="utf-8")
    subprocess.run(["cc", "-c", "a.c", "-o", "a.o", "-I."], cwd=tmp_path, check=True)
    subprocess.run(["c++", "-std=c++17", "b.cc", "a.o", "-o", "t", "-I."], cwd=tmp_path, check=True)
    out = subprocess.run(["./t"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0].startswith("FSW,RESET,")
    assert [l.split(",")[1] for l in out[1:]] == ["P2", "P1", "P3", "P4"]  # P2 只记一次（C 文件里），P1 记最后一次，P3 武装后才记
    assert out[2].endswith(",2")  # 记最后一次时附加值 = 发生了几次
    # 总开关关掉：什么都不打印
    subprocess.run(["cc", "-DFW_STOPWATCH_ENABLE=0", "-c", "a.c", "-o", "a0.o", "-I."], cwd=tmp_path, check=True)
    subprocess.run(["c++", "-std=c++17", "-DFW_STOPWATCH_ENABLE=0", "b.cc", "a0.o", "-o", "t0", "-I."], cwd=tmp_path, check=True)
    assert subprocess.run(["./t0"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout == ""


# ---------- 读 AI 给的插入清单，码表自己核对 ----------
def test_parse_plan_accepts_only_timing_lines(door):
    p = ins.parse_plan(reply(), door, STEPS)
    assert p["ok"] and len(p["inserts"]) == 5
    bad = json.loads(json.dumps(PLAN))
    bad["inserts"][2]["lines"] = ["    FSW_MARK_ONCE(2, 0);", "    uid_ok = 0;"]  # 夹带了别的代码
    r = ins.parse_plan(reply(bad), door, STEPS)
    assert not r["ok"] and "只能加计时点" in r["message"]


def test_parse_plan_needs_every_confirmed_step(door):
    bad = json.loads(json.dumps(PLAN))
    bad["inserts"] = [x for x in bad["inserts"] if x["step"] != "P5"]
    r = ins.parse_plan(reply(bad), door, STEPS)
    assert not r["ok"] and "发出开锁" in r["message"]


def test_parse_plan_rejects_bad_location(door):
    bad = json.loads(json.dumps(PLAN))
    bad["inserts"][2]["after_line"] = 99999
    assert not ins.parse_plan(reply(bad), door, STEPS)["ok"]


# ---------- 插进去、只加不删、还原逐字节一样 ----------
def test_apply_only_adds_and_restore_is_byte_identical(tmp_path, door):
    orig = tmp_path / "orig"
    shutil.copytree(door, orig)
    plan = ins.parse_plan(reply(), door, STEPS)
    rec = ins.apply(door, plan, tmp_path / "backup")
    assert rec["added"] == 9 + len(ins.HEADER.splitlines()) and rec["deleted"] == 0
    assert (door / "main/fw_stopwatch.h").exists()
    text = (door / "main/rfid_task.c").read_text(encoding="utf-8").splitlines()
    # 原来第 131 行前面多了 1 行 include、3 行 P1，变成第 135 行；P2 的两行在第 136、137 行
    assert text[0] == '#include "fw_stopwatch.h"' and "FSW_MARK_ONCE(2, 0);" in text[136]
    assert rec["steps"]["P2"]["file"] == "main/rfid_task.c" and rec["steps"]["P2"]["lines"] == [136, 137]
    for _ in range(10):
        r = ins.restore(door, tmp_path / "backup")
        assert r["ok"] and ins.compare(door, orig)["same"]
        ins.apply(door, plan, tmp_path / "backup")
    ins.restore(door, tmp_path / "backup")
    r = ins.compare(door, orig)
    assert r["same"] and r["diff"] == [] and r["extra"] == [] and not (door / "main/fw_stopwatch.h").exists()


def test_compare_names_files_that_differ(tmp_path, door):
    orig = tmp_path / "orig"
    shutil.copytree(door, orig)
    (door / "main/ui_led.c").write_text("x", encoding="utf-8")
    r = ins.compare(door, orig)
    assert not r["same"] and r["diff"] == ["main/ui_led.c"]


def test_diff_view_lists_each_place(door, tmp_path):
    plan = ins.parse_plan(reply(), door, STEPS)
    rec = ins.apply(door, plan, tmp_path / "backup")
    v = ins.diff_view(rec, STEPS)
    assert v["files"] == 3 and v["added"] == rec["added"] and v["deleted"] == 0
    p2 = next(x for x in v["places"] if x["step"] == "P2")
    assert p2["n"] == "卡号读完" and p2["file"] == "main/rfid_task.c" and p2["at"] == 131
    assert p2["lines"][0] == [" ", "    uid_ok = rc522_read_uid(dev, &uid);"] and p2["lines"][1][0] == "+"


# ---------- 编译出错：找到是哪一处计时点，撤掉重编 ----------
def test_locate_error_points_to_the_step(door, tmp_path):
    rec = ins.apply(door, ins.parse_plan(reply(), door, STEPS), tmp_path / "backup")
    err = f"{door}/main/rfid_task.c:137:5: error: expected ';' before 'uid_ok'"
    assert ins.locate_error(err, rec, door) == "P2"
    assert ins.locate_error("main/other.c:3: error: x", rec, door) is None


def test_drop_step_removes_only_that_place(door, tmp_path):
    plan = ins.parse_plan(reply(), door, STEPS)
    ins.apply(door, plan, tmp_path / "backup")
    rec = ins.drop(door, plan, "P2", tmp_path / "backup")
    text = (door / "main/rfid_task.c").read_text(encoding="utf-8")
    assert "FSW_MARK_ONCE(2" not in text and "FSW_MARK(1, 0);" in text and "P2" not in rec["steps"]


# ---------- 整个插入：调用一次大模型（只读），码表插 ----------
def test_insert_with_model(door, tmp_path):
    m = FakeLLM(reply())
    r = ins.insert(m, door, STEPS, tmp_path / "backup")
    assert r["ok"] and r["view"]["deleted"] == 0 and "FSW_MARK" in m.prompts[0] and "读到卡" in m.prompts[0]


def test_insert_bad_plan_changes_nothing(door, tmp_path):
    before = {p: p.read_bytes() for p in door.rglob("*") if p.is_file()}
    r = ins.insert(FakeLLM("没给清单"), door, STEPS, tmp_path / "backup")
    assert not r["ok"] and {p: p.read_bytes() for p in door.rglob("*") if p.is_file()} == before


# ---------- 试一次：数收到了几个计时点 ----------
def test_count_marks():
    lines = ["I (123) app: hello", "FSW,RESET,100,0", "FSW,P2,120,0", "FSW,P1,110,3", "FSW,P2,130,0", "garbage FSW,P9"]
    assert ins.count_marks(lines) == {1, 2}


def test_include_line_may_have_a_trailing_comment(door):
    plan = json.loads(json.dumps(PLAN))
    plan["inserts"][0]["lines"] = ['#include "fw_stopwatch.h"  // 计时点工具（固件码表）']
    plan["inserts"][3]["lines"] = ["#include <fw_stopwatch.h>"]
    assert ins.parse_plan(reply(plan), door, STEPS)["ok"]
    plan["inserts"][3]["lines"] = ['#include "other.h"']
    assert not ins.parse_plan(reply(plan), door, STEPS)["ok"]


def test_compare_ignores_files_the_build_generated_but_not_ours(tmp_path, door):
    orig = tmp_path / "orig"
    shutil.copytree(door, orig)
    (door / "main/assets").mkdir()
    (door / "main/assets/lang_config.h").write_text("// Auto-generated", encoding="utf-8")
    r = ins.compare(door, orig)
    assert r["same"] and r["extra"] == ["main/assets/lang_config.h"]
    (door / "main/fw_stopwatch.h").write_text(ins.HEADER, encoding="utf-8")  # 码表自己加的没删掉：算不一样
    assert ins.compare(door, orig)["diff"] == ["main/fw_stopwatch.h"]
    (orig / "main/ui_led.c").unlink()
    (door / "main/fw_stopwatch.h").unlink()
    assert ins.compare(door, orig)["same"]  # 原版里没有的不算……
    (door / "main/ui_led.c").unlink()
    (orig / "main/ui_led.c").write_text("x", encoding="utf-8")
    assert ins.compare(door, orig)["diff"] == ["main/ui_led.c"]  # ……原版里有、这边没了的算


# ---------- 附加值：问题规则要看的计时点顺带记一个值（只读已有的变量），码表核对 ----------

NEED = {"P2": {"what": "读卡器当时的模式", "ok": "正常读卡", "how": "读读卡器当前模式（转成整数）", "value": 1, "for": ["读错卡"]}}


def plan_with_value(expr, values=None):
    p = json.loads(json.dumps(PLAN))
    p["inserts"][2]["lines"] = ["    // 计时点 P2：卡号读完（附加值：读卡器当时的模式）", f"    FSW_MARK_ONCE(2, {expr});"]
    if values is not None:
        p["values"] = values
    return p


def test_prompt_asks_for_the_value_where_a_rule_needs_it():
    t = ins.build_prompt(STEPS, "main/fw_stopwatch.h", needs=NEED)
    assert "附加值" in t and "读卡器当时的模式" in t and "正常读卡" in t and "只能读" in t
    assert '"values"' in t
    assert "读卡器当时的模式" not in ins.build_prompt(STEPS, "main/fw_stopwatch.h")  # 不需要就不提


@pytest.mark.parametrize("expr", ["(int)g_reader.mode", "static_cast<int>(Reader::GetInstance().GetMode())",
                                  "reader->mode", "rfid_mode()", "0", "-1", "(int32_t)state_"])
def test_value_may_only_read(door, expr):
    r = ins.parse_plan(reply(plan_with_value(expr, [{"step": "P2", "expect": 1}])), door, STEPS, needs=NEED)
    assert r["ok"], r.get("message")


@pytest.mark.parametrize("expr", ["mode = 1", "count++", "--count", "set_mode(1)", "a, b", "x += 1", "f(g())",
                                  '"abc"', "x ? 1 : 0"])
def test_value_that_changes_anything_is_refused(door, expr):
    r = ins.parse_plan(reply(plan_with_value(expr)), door, STEPS, needs=NEED)
    assert not r["ok"] and "附加值" in r["message"]


def test_recorded_value_goes_into_the_record(door, tmp_path):
    plan = ins.parse_plan(reply(plan_with_value("(int)g_reader.mode", [{"step": "P2", "expect": 3, "why": "枚举第 3 个"}])),
                          door, STEPS, needs=NEED)
    assert plan["values"] == {"P2": {"expr": "(int)g_reader.mode", "expect": 3, "what": "读卡器当时的模式"}}
    rec = ins.apply(door, plan, tmp_path / "bk")
    assert ins.current(tmp_path / "bk")["values"]["P2"]["expect"] == 3
    v = ins.diff_view(rec, STEPS)
    assert any("读卡器当时的模式" in p["n"] for p in v["places"])


def test_needed_value_not_recorded_is_said_not_failed(door):
    plan = ins.parse_plan(reply(), door, STEPS, needs=NEED)  # AI 没记（附加值还是 0）
    assert plan["ok"] and plan["values"] == {}
    assert any("读卡器当时的模式" in n and "读错卡" in n for n in plan["notes"])


def test_expect_defaults_to_the_standard(door):
    plan = ins.parse_plan(reply(plan_with_value("(int)g_reader.mode")), door, STEPS, needs=NEED)
    assert plan["values"]["P2"]["expect"] == 1
