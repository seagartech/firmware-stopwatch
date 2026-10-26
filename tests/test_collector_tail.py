"""多轮实测：每轮清零之后才补打出来的"记最后一次"（设备上可能晚到的点，见 fw_stopwatch.h 的 FSW_TAIL_MASK）。
- 清零后 1 秒内补打的（FSW,LATE,…，时间在清零之后）：是上一轮这个点更晚的那一次——已收齐的上一轮按它更新，
  差这个终点、在等尾巴的上一轮把它补上；不记进下一轮（它比下一轮的起点早）。
- 10-26 起一轮按起点分：清零后、下一轮起点之前到的（晚过 1 秒的、设备打的 FSW,TAIL 也算）都是上一轮的，取最后那次；
  不记进下一轮。
用 8772 那次识别存下的页面（小智：P9 首段出声之后三段并排：末段出声 P10、回到聆听 P12、播放放空 P11）。"""

import json
from pathlib import Path

from fsw import measure as meas
from fsw import standards as stds

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"


def spec():
    page = json.loads((HERE / "identify-6afa2c1-page.json").read_text(encoding="utf-8"))["page"]
    return meas.Spec.from_scenario(stds.real_scenario(page, stds.find(page["steps"])))


def round_lines(t0, late10=True):
    s = lambda x: str(int(t0 + x * 1_000_000))
    out = [f"FSW,RESET,{s(0)},0", f"FSW,P1,{s(1.0)},0", f"FSW,P2,{s(1.0001)},0", f"FSW,P5,{s(1.001)},0", f"FSW,P7,{s(1.5)},0",
           f"FSW,P8,{s(1.6)},0", f"FSW,P9,{s(1.7)},0", f"FSW,P11,{s(2.5)},0", f"FSW,P12,{s(2.6)},0", f"FSW,LATE,P6,{s(1.4)},3"]
    return out + ([f"FSW,LATE,P10,{s(2.55)},5"] if late10 else [])


def run(lines):
    c = meas.Collector(spec())
    out = []
    for l in lines:
        out += c.feed(l)
    out += c.finish()
    return [r for r in meas.merge_updates(out) if r.get("ok")]


def seg(r, k):
    return round(r["segments"][k], 3) if "segments" in r else round(r["segs"][k], 3)


def test_spec_knows_last_output_is_a_keep_last_point():
    sp = spec()
    assert "P10" in sp.marks and "P10" not in sp.last


def two_rounds(tail, late10=True):
    """第 1 轮（0 秒清零开始）→ 2.61 秒清零 → 清零后补打的尾巴 → 第 2 轮（10 秒起，自己的清零）→ 12.61 秒清零。"""
    return (round_lines(0, late10) + ["FSW,RESET,2610000,0"] + tail + round_lines(10_000_000)[1:]
            + ["FSW,RESET,12610000,0"])


def test_tail_within_one_second_updates_the_previous_round():
    base = run(two_rounds([]))
    late = run(two_rounds(["FSW,LATE,P10,2900000,6"]))
    assert len(base) == 2 and seg(base[0], "P9-P10") == 0.85
    assert len(late) == 2 and seg(late[0], "P9-P10") == 1.2 and seg(late[1], "P9-P10") == 0.85  # 下一轮不受影响


def test_tail_later_than_one_second_still_belongs_to_its_round_and_does_not_leak_into_the_next_round():
    r = run(two_rounds(["FSW,LATE,P10,4200000,6", "FSW,TAIL,P10,4300000,0"]))
    assert len(r) == 2 and seg(r[0], "P9-P10") == 2.6 and seg(r[1], "P9-P10") == 0.85


def test_round_waiting_for_its_last_output_gets_it_from_the_tail():
    r = run(two_rounds(["FSW,LATE,P10,2900000,6"], late10=False))
    assert len(r) == 2 and seg(r[0], "P9-P10") == 1.2


def test_replayed_8767_log_is_unchanged_by_tail_handling():
    from tests.test_multi_sentence_round import CARRIED, log, old_page, replay, scenario
    rs = replay(scenario(old_page()), log(), CARRIED)
    assert rs  # 原样的日志里没有清零后补打的行：结果和以前一样（具体数见 test_multi_sentence_round）
