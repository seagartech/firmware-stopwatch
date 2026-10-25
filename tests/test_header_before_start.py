"""计时点工具（fw_stopwatch.h）起点之前的行为（10-25 真机全流程第 1 轮）：
- 起点之前暂存的"记最后一次"（比如唤醒提示音的最后一段）在起点到的那一刻清掉；起点之前 FLUSH_LAST 不打印，结束时也不补打。
- 起点之前别的点每个最多打一次（聆听时每发一包都走到的点不再刷屏）。
- 起点每次都打（唤醒词说完、真正说完各一次），码表取第一下感觉到的结果之前的最后一次。
- 老的插法（不知道起点）和以前一样。"""

import shutil
import subprocess

import pytest

from fsw import instrument as ins

cc = pytest.mark.skipif(not shutil.which("cc"), reason="这台电脑没有 C 编译器")


def run_c(tmp_path, body, start=None):
    (tmp_path / "fw_stopwatch.h").write_text(ins.header_text(start), encoding="utf-8")
    (tmp_path / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n", encoding="utf-8")
    (tmp_path / "m.c").write_text('#include "fw_stopwatch.h"\nint64_t esp_timer_get_time(void){static int64_t t=1000;return t+=10;}\n'
                                  f"int main(void){{{body}return 0;}}\n", encoding="utf-8")
    subprocess.run(["cc", "m.c", "-o", "m", "-I."], cwd=tmp_path, check=True)
    out = subprocess.run(["./m"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.splitlines()
    return [",".join(l.split(",")[1:3]) if l.startswith("FSW,LATE") else l.split(",")[1] for l in out]


# 唤醒：提示音走回复同一句输出（P7 首段 / P8 末段，放空时打 P8、记 P9）；聆听时每包都走 P2；
# 唤醒词说完判一次说完（P1），真正说完又一次（P1）；收到识别文字（P4）；回复出声（P7/P8）；回到聆听（P10、清零）
WAKE = ("FSW_ROUND_RESET();"
        "FSW_MARK_ONCE(7,0);FSW_KEEP_LAST(8);FSW_FLUSH_LAST(8);FSW_MARK_ONCE(9,0);"
        "FSW_MARK_ONCE(2,0);FSW_KEEP_LAST(3);FSW_MARK_ONCE(2,0);FSW_KEEP_LAST(3);FSW_MARK_ONCE(2,0);FSW_KEEP_LAST(3);"
        "FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);FSW_KEEP_LAST(3);FSW_MARK_ONCE(2,0);FSW_KEEP_LAST(3);"
        "FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(4,0);"
        "FSW_MARK_ONCE(7,0);FSW_KEEP_LAST(8);FSW_MARK_ONCE(7,0);FSW_KEEP_LAST(8);"
        "FSW_MARK_ONCE(10,0);FSW_ROUND_RESET();")


@cc
def test_wake_beep_last_value_is_not_printed_and_start_prints_every_time(tmp_path):
    out = run_c(tmp_path, WAKE, start=1)
    assert out == ["RESET", "P7", "P9", "P2", "P1", "P2", "P1", "P4", "P7", "P10", "LATE,P3", "LATE,P8", "RESET"]
    assert "P8" not in out  # 提示音的末段输出没打出来


@cc
def test_pending_last_value_from_before_start_is_cleared_at_start(tmp_path):
    body = "FSW_ROUND_RESET();FSW_KEEP_LAST(8);FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(4,0);FSW_ROUND_RESET();"
    assert run_c(tmp_path, body, start=1) == ["RESET", "P1", "P4", "RESET"]  # 起点之前那次不补打 LATE


@cc
def test_round_without_start_prints_no_late(tmp_path):
    body = "FSW_ROUND_RESET();FSW_MARK(5,0);FSW_MARK(5,0);FSW_KEEP_LAST(8);FSW_ROUND_RESET();FSW_MARK(5,0);"
    assert run_c(tmp_path, body, start=1) == ["RESET", "P5", "RESET", "P5"]  # 只喊唤醒词那一轮：每个点最多一行，不补打


@cc
def test_old_header_without_start_is_unchanged(tmp_path):
    assert run_c(tmp_path, WAKE) == ["RESET", "P7", "P8", "P9", "P2", "P1", "P4", "P10", "LATE,P3", "LATE,P8", "RESET"]
