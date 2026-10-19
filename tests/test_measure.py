"""任务 15：多轮实测的计算——读计时点行、按轮切分、算每段用时、查问题、没收全的不算。

核对标准：验证期收数据小程序用 20 轮模拟日志算出的每轮数据（tests/fixtures/v07/）。
"""

import csv
import json
from pathlib import Path

import pytest

from fsw import measure as m

V07 = Path(__file__).parent / "fixtures" / "v07"
SWALLOW = {"key": "sw", "name": "回答开头被吞", "level": "bad",
           "rule": {"type": "any", "rules": [{"type": "value_not", "point": "P7", "value": 6},
                                             {"type": "point_before", "a": "P8", "b": "P13"}]}}


def v07_spec():
    """验证期的分段（毫秒）换成码表的格式：每一段单独当一条线算。"""
    std = json.loads((V07 / "ranges.json").read_text(encoding="utf-8"))
    segs = {s["名称"]: [f"P{s['从']}", f"P{s['到']}"] for s in std["分段"]}
    return m.Spec(start="P2", segments=segs,
                  lines=[{"name": "总等待", "segs": [["P2", "P10"]]}], problems=[SWALLOW],
                  need=["P2", "P10"])


def test_parse_line():
    assert m.parse_line("FSW,P7,20084055,6") == ("P7", 20084055, 6)
    assert m.parse_line("FSW,RESET,13125950,0") == ("RESET", 13125950, 0)
    assert m.parse_line("I (845) Application: STATE: idle") is None
    assert m.parse_line("FSW,P7,abc,6") is None


def test_validation_07_mock_log_matches_every_segment_of_every_round():
    lines = (V07 / "mock_log.txt").read_text(encoding="utf-8").splitlines()
    rounds = m.collect(lines, v07_spec())
    ok = [r for r in rounds if r["ok"]]
    assert len(ok) == 20
    with open(V07 / "per_round.csv", encoding="utf-8-sig") as f:
        want = list(csv.DictReader(f))
    names = [n for n in json.loads((V07 / "ranges.json").read_text(encoding="utf-8"))["分段"]]
    for r, w in zip(ok, want):
        for s in names:
            assert round(r["segs"][s["名称"]] * 1000, 1) == pytest.approx(float(w[s["名称"]]), abs=0.051), (r["n"], s["名称"])
    assert [r["n"] for r in ok if "sw" in r["probs"]] == [4, 11, 17]  # 3 次吞字全查出


def test_points_printed_later_still_count_by_timestamp():
    # P2 只在内存记最后一次、到 P5 才打印：日志里排在后面，按时间戳算
    lines = ["FSW,RESET,1000,0", "FSW,P4,3000,0", "FSW,P2,2000,3", "FSW,P5,4000,0", "FSW,P10,5000,0"]
    spec = m.Spec(start="P2", segments={"a": ["P2", "P4"], "b": ["P4", "P5"], "c": ["P5", "P10"]},
                  lines=[{"name": "开口", "segs": [["P2", "P10"]]}])
    r = m.collect(lines, spec)[0]
    assert r["ok"] and r["segs"]["a"] == 0.001 and r["ends"] == [0.003]


def test_missing_point_round_is_not_counted_and_says_what():
    spec = m.Spec(start="P2", segments={"a": ["P2", "P4"], "b": ["P4", "P10"]},
                  lines=[{"name": "开口", "segs": [["P2", "P10"]]}], names={"P10": "开始出声"})
    lines = ["FSW,RESET,0,0", "FSW,P2,10,0", "FSW,P4,20,0",            # 没收到 P10
             "FSW,RESET,100,0", "FSW,P2,110,0", "FSW,P4,120,0", "FSW,P10,130,0"]
    rs = m.collect(lines, spec)
    assert [r["ok"] for r in rs] == [False, True]
    assert rs[0]["missing"] == ["开始出声"] and rs[1]["n"] == 1 and rs[0]["att"] == 1 and rs[1]["att"] == 2


def test_half_round_before_first_reset_and_lines_after_done_are_ignored():
    spec = m.Spec(start="P1", segments={"a": ["P1", "P2"]}, lines=[{"name": "x", "segs": [["P1", "P2"]]}])
    lines = ["FSW,P2,5,0",                                       # 开始收的时候已经在一轮中间
             "FSW,RESET,10,0", "FSW,P1,11,0", "FSW,P2,13,0", "FSW,P1,14,0", "FSW,P2,99,0"]  # 同一轮重复的只认第一次
    rs = m.collect(lines, spec)
    assert [r["ok"] for r in rs] == [True] and rs[0]["segs"]["a"] == pytest.approx(2e-6)


def test_problem_rules():
    spec = m.Spec(start="P1", segments={"s": ["P1", "P2"], "f": ["P1", "P3"]},
                  lines=[{"name": "反应", "segs": [["P1", "P3"]]}, {"name": "主", "segs": [["P1", "P2"]]}],
                  problems=[{"key": "rev", "rule": {"type": "line_order", "first": "反应", "second": "主"}},
                            {"key": "slow", "rule": {"type": "segment_over", "segment": "s", "seconds": 1.0}},
                            {"key": "lo", "rule": {"type": "line_over", "line": "主", "seconds": 0.5}}])
    fast = m.collect(["FSW,RESET,0,0", "FSW,P1,0,0", "FSW,P3,100000,0", "FSW,P2,400000,0"], spec)[0]
    slow = m.collect(["FSW,RESET,0,0", "FSW,P1,0,0", "FSW,P3,1900000,0", "FSW,P2,1500000,0"], spec)[0]
    assert fast["probs"] == [] and sorted(slow["probs"]) == ["lo", "rev", "slow"]


def test_live_collector_counts_rounds_as_they_finish():
    spec = m.Spec(start="P1", segments={"a": ["P1", "P2"]}, lines=[{"name": "x", "segs": [["P1", "P2"]]}])
    c = m.Collector(spec)
    events = []
    for l in ["FSW,RESET,0,0", "FSW,P1,1,0", "FSW,P2,2,0", "FSW,RESET,10,0", "FSW,P1,11,0"]:
        events += c.feed(l)
    assert [e["n"] for e in events if e["ok"]] == [1]  # 下一轮开始时，上一轮收尾
    assert c.progress() == (1, 2)  # 正在收的这轮：收到 1 个 / 共要 2 个
    assert c.finish() == []  # 停下时没收齐的这一轮不算，也不算"没收全"


def test_spec_from_scenario_demo_data():
    d = json.loads((Path(__file__).resolve().parent.parent / "fsw/web/demo/door.json").read_text(encoding="utf-8"))
    spec = m.Spec.from_scenario(d["scenario"])
    assert spec.start == "P1" and [l["name"] for l in spec.lines] == ["反应线", "开门线"]
    assert set(spec.need) == {"P1", "P2", "P3", "P4", "P5"}


def test_demo_replay_gives_back_the_demo_numbers_and_problems():
    """门禁刷卡和语音对话的演示数据：回放成设备输出再算一遍，和演示数据一样（含问题轮）。"""
    root = Path(__file__).resolve().parent.parent / "fsw/web/demo"
    for name in ("door", "voice"):
        d = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
        spec = m.Spec.from_scenario(d["scenario"])
        rs = [r for r in m.collect(list(m.demo_lines(d, 20)), spec) if r["ok"]]
        assert len(rs) == 20
        for k, vals in d["result"]["segments"].items():
            assert [round(r["segs"][k], 4) for r in rs] == pytest.approx(vals, abs=1e-4), (name, k)
        for key, rounds in d["result"]["problems"].items():
            assert [r["n"] for r in rs if key in r["probs"]] == rounds, (name, key)


def test_demo_replay_can_drop_a_round_for_resampling():
    d = json.loads((Path(__file__).resolve().parent.parent / "fsw/web/demo/voice.json").read_text(encoding="utf-8"))
    rs = m.collect(list(m.demo_lines(d, 20, drop_at=9)), m.Spec.from_scenario(d["scenario"]))
    assert len([r for r in rs if r["ok"]]) == 20 and [r["att"] for r in rs if not r["ok"]] == [9] and len(rs) == 21


# ---- 10-19 真测：打断回答（点屏幕）后服务器补发"开始说话"，落在下一轮你说完之前 ----
INTERRUPT_LOG = """FSW,P10,69168287,7
FSW,RESET,69168804,0
FSW,P6,69341391,0
FSW,P3,75641730,0
FSW,P7,75648442,0
FSW,P4,76093919,0
FSW,P5,76094869,0
FSW,P8,76751974,0
FSW,P9,76768330,0
I (82273) Application: Abort speaking
FSW,P1,75278148,3
FSW,P2,75636538,107
FSW,P10,82588245,98
FSW,RESET,82588773,0
FSW,P6,82602641,0
FSW,P3,88749576,0
FSW,P7,88755932,0
FSW,P4,89299717,0
FSW,P5,89300716,0
FSW,P8,89751380,0
FSW,P9,89768278,0
I (99533) Application: Abort speaking
FSW,P1,88318085,2
FSW,P2,88706223,101
FSW,P10,99728308,167
FSW,RESET,99729057,0
FSW,P6,99865126,0
FSW,P3,100809388,0
FSW,P7,100865092,0
FSW,P4,101224776,0
FSW,P5,101225785,0
FSW,P8,101753818,0
FSW,P9,101768300,0
I (104313) Application: Abort speaking
FSW,P1,100308040,2
FSW,P2,100854889,18
FSW,P10,104528258,47
FSW,RESET,104528777,0
FSW,P6,104642261,0""".splitlines()


def interrupt_spec():
    segs = {"P1-P2": ["P1", "P2"], "P2-P3": ["P2", "P3"], "P3-P6": ["P3", "P6"], "P6-P7": ["P6", "P7"],
            "P7-P8": ["P7", "P8"], "P8-P9": ["P8", "P9"], "P9-P10": ["P9", "P10"], "P3-P4": ["P3", "P4"], "P4-P5": ["P4", "P5"]}
    talk = [segs[k] for k in ("P1-P2", "P2-P3", "P3-P6", "P6-P7", "P7-P8", "P8-P9")]
    return m.Spec(start="P1", segments=segs, lines=[
        {"name": "表情", "segs": [segs[k] for k in ("P1-P2", "P2-P3", "P3-P4", "P4-P5")]},
        {"name": "到开始出声", "segs": talk}, {"name": "到声音放完", "segs": talk + [segs["P9-P10"]]}],
        need=["P1", "P2", "P3", "P6", "P7", "P8", "P9", "P10"], last={"P10", "P5"}, soft={"P10"},
        marks={"P1", "P2", "P5", "P10"}, names={"P6": "收到开始说话"})


def test_interrupted_answer_does_not_throw_away_the_next_round():
    """用户 10-19："打断是正常用法，码表不能判错"。打断后的那一轮：补发的"开始说话"不是这一轮的，真正那次不再来，
    这一轮照样算，用到它的两段空着，到开始出声按首尾算；早得不多（起点前 0.44 秒）的那次也不当成这一轮的，不出负数段。"""
    rs = m.collect(INTERRUPT_LOG, interrupt_spec())
    ok = [r for r in rs if r.get("ok")]
    assert len(ok) == 3 and not [r for r in rs if not r.get("ok")]
    for r in ok:
        assert r["gap"] == ["收到开始说话"] and r["segs"]["P3-P6"] is None and r["segs"]["P6-P7"] is None
        # "最后一段录音发出"记最后一次、可能越过下一步（报告把两段合一段，见 judge.merge_overruns），不在这里查
        assert all(v is None or v >= 0 for k, v in r["segs"].items() if k not in ("P1-P2", "P2-P3"))
    assert [round(r["ends"][1], 3) for r in ok] == [1.49, 1.45, 1.46]  # 到开始出声 = 开始出声 - 你说完


# ---- 10-19 真测：试一次收齐后到正式实测开始读之间，设备回到聆听的那个结束标记没读到 ----
MISSED_RESET_LOG = """FSW,P3,12486453,0
FSW,P6,12501091,0
FSW,P7,12503571,0
FSW,P4,12940043,0
FSW,P5,12941039,0
FSW,P8,13502512,0
FSW,P9,13518251,0
FSW,P1,12138079,2
FSW,P2,12464467,66
FSW,P10,22758257,155
I (32683) SystemInfo: free sram: 114059 minimal sram: 33468
FSW,P3,32712117,0
FSW,P6,32747663,0
FSW,P7,32750760,0
FSW,P4,33058438,0
FSW,P5,33059364,0
FSW,P8,33457928,0
FSW,P9,33478312,0
FSW,P1,32387776,2
FSW,P2,32752186,164
FSW,P10,33958250,9
FSW,P10,36238257,4
FSW,P10,39598245,2
FSW,RESET,39758289,0""".splitlines()


def test_next_round_counts_when_its_reset_line_was_missed():
    """试一次那一轮收全后，下一句的结束标记没读到：以前下一句的点全被并进第 1 轮（只记第一次），这句白说，
    第 1 轮"声音放完"还取到了下一句的最后一声（27 秒）。现在按新起点分开：两轮都算，各是各的。"""
    rs = m.collect(MISSED_RESET_LOG, interrupt_spec())
    ok = [r for r in rs if r.get("ok")]
    assert len(ok) == 2, rs
    assert [round(r["ends"][2], 2) for r in ok] == [10.62, 7.21]
    assert round(ok[1]["ends"][1], 3) == round((33478312 - 32387776) / 1e6, 3)


def test_noise_start_again_in_the_same_round_is_not_a_new_round():
    """起点在同一轮里又报了一次（后面没有新一轮的点）：不分轮。"""
    log = MISSED_RESET_LOG[:10] + ["FSW,P1,23000000,1", "FSW,RESET,30000000,0"]
    assert len([r for r in m.collect(log, interrupt_spec()) if r.get("ok")]) == 1
