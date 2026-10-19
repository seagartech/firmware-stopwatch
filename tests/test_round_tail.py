"""上一轮的尾巴在新一轮开始后才到（比如打断回答后，上一句的结束消息晚 0.4 秒才到）：
"一轮只记第一次"的点起点之前照常打印、但不占名额，这一轮真正的那一次照样记上（10-19 真测 22:29：10 轮里 5 轮缺"收到开口通知"）。
起点编号由插点时写进计时点工具；老的插法（不知道起点）和以前一样。按键、刷卡这类不受影响。"""

import shutil
import subprocess

import pytest

from fsw import instrument as ins
from fsw import measure as meas
from tests.test_instrument import STEPS, door_source, reply

cc = pytest.mark.skipif(not shutil.which("cc"), reason="这台电脑没有 C 编译器")


def run_c(tmp_path, body, start=None):
    (tmp_path / "fw_stopwatch.h").write_text(ins.header_text(start), encoding="utf-8")
    (tmp_path / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n", encoding="utf-8")
    (tmp_path / "m.c").write_text('#include "fw_stopwatch.h"\nint64_t esp_timer_get_time(void){static int64_t t=1000;return t+=10;}\n'
                                  f"int main(void){{{body}return 0;}}\n", encoding="utf-8")
    subprocess.run(["cc", "m.c", "-o", "m", "-I."], cwd=tmp_path, check=True)
    return [l.split(",")[1] for l in subprocess.run(["./m"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.splitlines()]


# 对话类：起点是"记最后一次"的点（P1），P6 每条消息都会走到。打断后：新一轮开始（RESET）→ 上一句的结束消息到（P6）→ 起点 → 真正的开口消息（P6）
TAIL = "FSW_ROUND_RESET();FSW_MARK_ONCE(6,0);FSW_KEEP_LAST(1);FSW_FLUSH_LAST(1);FSW_MARK_ONCE(6,0);FSW_MARK_ONCE(6,0);"


@cc
def test_tail_before_start_does_not_eat_this_rounds_point(tmp_path):
    assert run_c(tmp_path, TAIL, start=1) == ["RESET", "P6", "P1", "P6"]     # 起点之后的第一次照样打出来，之后只记一次


@cc
def test_old_header_without_start_behaves_as_before(tmp_path):
    assert run_c(tmp_path, TAIL) == ["RESET", "P6", "P1"]                    # 不知道起点：和以前一样（这一轮的 P6 被尾巴占了）


@cc
def test_button_device_where_start_is_mark_once_is_unchanged(tmp_path):
    """刷卡：起点 P1 本身是"只记第一次"，后面的点在起点之后，打印和以前一模一样。"""
    body = "FSW_ROUND_RESET();FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);FSW_MARK_ONCE(2,0);FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(3,0);"
    assert run_c(tmp_path, body, start=1) == run_c(tmp_path, body) == ["RESET", "P1", "P2", "P3"]


def test_plan_knows_the_start_point_and_writes_it_into_the_header(tmp_path):
    p = ins.parse_plan(reply(), door_source(tmp_path / "door"), STEPS)
    assert p["ok"] and p["start"] == 1                                       # 刷卡门禁：起点是"读到卡"
    assert "#define FSW_START_ID 1" in ins.header_text(p["start"]) and "#define FSW_START_ID -1" in ins.header_text()


def test_collector_keeps_the_point_printed_after_start_and_drops_the_tail():
    """码表这边：起点之前那一次（上一轮的尾巴）不算，起点之后那一次算进这一轮。"""
    sc = {"format": "fsw-scenario/1", "name": "对话", "start": {"point": "P1", "name": "说完"},
          "segments": {"P1-P6": {"name": "等开口通知", "from": "P1", "to": "P6"}, "P6-P7": {"name": "切到说话", "from": "P6", "to": "P7"}},
          "lines": [{"name": "线", "end": "说话", "segments": ["P1-P6", "P6-P7"]}], "experiences": [], "problems": []}
    log = []
    for r in range(3):
        b = 10_000_000 * (r + 1)
        log += [f"FSW,RESET,{b},0", f"FSW,P6,{b + 400_000},0",                # 上一轮的尾巴：起点之前
                f"FSW,P1,{b + 2_000_000},0", f"FSW,P6,{b + 2_500_000},0", f"FSW,P7,{b + 2_510_000},0"]
    got = [r for r in meas.collect(log, meas.Spec.from_scenario(sc)) if r["ok"]]
    assert len(got) == 3 and all(abs(r["segs"]["P1-P6"] - 0.5) < 1e-6 for r in got)
