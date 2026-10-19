"""任务 16：判快慢、两把尺子、结论标签——全部按固定规则算，不用大模型。

核对：验证期两份模拟日志（对话、先出表情）的结论；每种标签、每种组合各有一组数据。
"""

import csv
import json
from pathlib import Path

import pytest

from fsw import judge as J
from fsw import measure as m

HERE = Path(__file__).parent
SCEN = HERE / "scenarios"


def load(name):
    return json.loads((SCEN / name).read_text(encoding="utf-8"))


def rounds_from_log(log, scenario):
    lines = (HERE / "fixtures" / log).read_text(encoding="utf-8").splitlines()
    return [r for r in m.collect(lines, m.Spec.from_scenario(scenario)) if r["ok"]]


# ---------- 和验证期的结论对得上 ----------
def test_dialogue_mock_log_matches_sample_report():
    """对话（验证 06 样例报告用的那份模拟日志 + 验证 03 的合理范围）。"""
    s = load("对话.json")
    j = J.judge(s, rounds_from_log("v07/mock_log.txt", s))
    line = j["lines"][0]
    assert line["med"] == pytest.approx(3.51, abs=0.01)
    assert line["exp"] == "差"                         # 体验标准：超过 1.5 秒算差
    assert line["verdict"] == "偏慢"                   # 超出合理范围 0.7～2.75；验证 03 没给总等待的"算慢"线
    assert line["combo"] == "先查哪段做坏了"            # 体验差 + 超出合理范围
    seg = {g["k"]: g for g in j["segments"]}
    assert seg["think"]["verdict"] == "超过慢阈值" and seg["think"]["med"] == pytest.approx(2.68, abs=0.01)
    assert seg["asr"]["verdict"] == "正常" and seg["net"]["verdict"] == "正常"
    assert seg["queue"]["verdict"] == "推算"            # 推算的段只标推算，不判快慢
    assert all(seg[k]["verdict"] == "正常" for k in ("decode", "speak", "state", "clear", "face"))
    assert seg["asr"]["outside"] and not seg["decode"]["outside"]  # 设备外段单独标
    assert j["biggest"] == "think"
    items = {x["k"]: x for x in j["labels"]}
    assert items["line:说完到开口"]["level"] == "必须改"
    assert items["problem:sw"]["level"] == "必须改" and items["problem:sw"]["rounds"] == [4, 11, 17]
    assert not j["few"]


def test_emotion_line_mock_log_matches_validation_09():
    """先出表情（验证 09 的模拟日志 + 它的合理范围）。"""
    s = load("表情线.json")
    rs = rounds_from_log("v09/mock_log_c3.txt", s)
    with open(HERE / "fixtures/v09/per_round_c3.csv", encoding="utf-8-sig") as f:
        want = list(csv.DictReader(f))
    assert [round(r["segs"]["dec"] * 1000, 1) for r in rs] == pytest.approx([float(w["决策模型选表情并发回"]) for w in want], abs=0.051)
    j = J.judge(s, rs)
    face, speak = j["lines"]
    assert speak["med"] == pytest.approx(2.17, abs=0.01) and speak["verdict"] == "正常" and speak["exp"] == "差"
    assert speak["combo"] == "每段都没做坏，是做法本身慢，要换做法才会快"
    assert face["med"] == pytest.approx(1.17, abs=0.01) and face["verdict"] == "正常" and face["exp"] is None
    assert face["over_slow"] == 2                          # 2 轮超过算慢线
    items = {x["k"]: x for x in j["labels"]}
    assert items["problem:rev"]["rounds"] == [6, 15] and items["problem:rev"]["level"] == "建议改"
    assert items["spiky:dec"]["level"] == "建议改"         # 决策模型偶尔特别慢
    assert items["line:说话线"]["level"] == "必须改"        # 体验差
    assert j["lead"]["med"] == pytest.approx(1.0, abs=0.01)  # 表情平均比说话早 1 秒
    assert [x["level"] for x in j["labels"]] == sorted((x["level"] for x in j["labels"]), key=J.LEVELS.index)


# ---------- 每种组合、每种标签一组数据 ----------
def sc(line_range=(1.0, 2.0), slow=3.0, fair=None, seg_basis="measured", problems=()):
    return {"start": {"point": "P1", "name": "起点"},
            "segments": {"a": {"name": "前一段", "owner": "固件", "side": "device", "from": "P1", "to": "P2",
                               "range": [0.4, 0.8], "slow": 1.0, "basis": seg_basis},
                         "b": {"name": "后一段", "owner": "服务器", "side": "outside", "from": "P2", "to": "P3",
                               "range": [0.5, 1.2], "slow": 2.0, "basis": "measured"}},
            "lines": [{"name": "主线", "end": "终点", "segments": ["a", "b"], "range": list(line_range), "slow": slow}],
            "experience": {"line": "主线", "fair": fair} if fair else None, "problems": list(problems)}


def rounds(a, b, n=20, probs=None):
    a = a if isinstance(a, list) else [a] * n
    b = b if isinstance(b, list) else [b] * n
    return [{"n": i + 1, "segs": {"a": a[i], "b": b[i]}, "ends": [a[i] + b[i]],
             "probs": (probs or {}).get(i + 1, [])} for i in range(n)]


@pytest.mark.parametrize("a,b,fair,combo", [
    (0.6, 1.0, 1.5, "每段都没做坏，是做法本身慢，要换做法才会快"),  # 体验差 + 在合理范围内
    (0.6, 1.6, 1.5, "先查哪段做坏了"),                           # 体验差 + 超出合理
    (0.6, 1.6, 9.0, "可能有隐患"),                               # 体验可以 + 超出合理
    (0.6, 1.0, 9.0, "正常"),                                     # 都正常
])
def test_four_combinations(a, b, fair, combo):
    j = J.judge(sc(fair=fair), rounds(a, b))
    assert j["lines"][0]["combo"] == combo


@pytest.mark.parametrize("b,combo", [(1.0, "只按合理范围判：正常"), (1.6, "只按合理范围判：先查哪段做坏了")])
def test_no_experience_standard_only_uses_ranges(b, combo):
    j = J.judge(sc(fair=None), rounds(0.6, b))
    assert j["lines"][0]["exp"] is None and j["lines"][0]["combo"] == combo


@pytest.mark.parametrize("a,b,why,level", [
    (0.6, 2.6, "中位数超过慢阈值", "必须改"),
    ([0.6] * 17 + [0.6] * 3, [1.0] * 17 + [2.6] * 3, "10 次里最慢超过慢阈值", "必须改"),
    (0.6, 1.6, "超出合理范围但未超过慢阈值", "建议改"),
    (0.6, 1.0, "在合理范围内", "可以不改"),
])
def test_line_labels(a, b, why, level):
    j = J.judge(sc(fair=None), rounds(a, b))
    item = next(x for x in j["labels"] if x["k"] == "line:主线")
    assert item["level"] == level, why


def test_experience_bad_is_must_even_inside_ranges():
    j = J.judge(sc(fair=1.5), rounds(0.6, 1.0))
    assert next(x for x in j["labels"] if x["k"] == "line:主线")["level"] == "必须改"


def test_spiky_segment_should_or_must():
    b = [1.0] * 19 + [5.0]                                   # 偶尔一次特别慢，10 次最慢没超
    assert next(x for x in J.judge(sc(), rounds(0.6, b))["labels"] if x["k"] == "spiky:b")["level"] == "建议改"
    b2 = [1.0] * 17 + [5.0] * 3                              # 偶发但每 10 次就有 1 次以上让整条线超过算慢线
    assert next(x for x in J.judge(sc(), rounds(0.6, b2))["labels"] if x["k"] == "spiky:b")["level"] == "必须改"


def test_problem_levels():
    warn = {"key": "rev", "name": "先后反了", "level": "warn", "rule": {"type": "line_order", "first": "主线", "second": "主线"}}
    bad = {"key": "sw", "name": "开头被吞", "level": "bad", "rule": {"type": "value_not", "point": "P2", "value": 6}}
    j = J.judge(sc(problems=[warn, bad]), rounds(0.6, 1.0, probs={3: ["rev", "sw"], 9: ["rev"]}))
    items = {x["k"]: x for x in j["labels"]}
    assert items["problem:rev"]["level"] == "建议改"          # 20 轮里 2 次，不到每 10 次 1 次以上
    assert items["problem:sw"]["level"] == "必须改"           # 对错问题，出现就必须改
    j2 = J.judge(sc(problems=[warn]), rounds(0.6, 1.0, probs={k: ["rev"] for k in (1, 5, 9)}))
    assert next(x for x in j2["labels"] if x["k"] == "problem:rev")["level"] == "必须改"  # 3/20 超过每 10 次 1 次


def test_segment_verdicts_and_estimated():
    j = J.judge(sc(seg_basis="estimated"), rounds(0.9, 1.5))
    seg = {g["k"]: g for g in j["segments"]}
    assert seg["a"]["verdict"] == "推算" and seg["b"]["verdict"] == "偏慢"
    assert J.judge(sc(), rounds(1.5, 1.0))["segments"][0]["verdict"] == "超过慢阈值"


def test_tiers_and_first_reaction():
    two = sc()
    two["lines"].insert(0, {"name": "反应线", "end": "亮灯", "segments": ["a"], "range": [0.4, 0.8], "slow": 1.0})
    rs = [{"n": i + 1, "segs": {"a": 0.5 + i / 100, "b": 1.0}, "ends": [0.5 + i / 100, 1.5 + i / 100], "probs": []} for i in range(20)]
    j = J.judge(two, rs)
    assert j["first"]["line"] == "反应线"
    t = j["lines"][1]
    assert t["med"] == pytest.approx(1.595) and t["p90"] == pytest.approx(1.67) and t["max"] == pytest.approx(1.69)


def test_fewer_than_20_rounds_is_reference_only():
    assert J.judge(sc(), rounds(0.6, 1.0, n=12))["few"] and not J.judge(sc(), rounds(0.6, 1.0))["few"]


def test_labels_sorted_must_first():
    warn = {"key": "rev", "name": "先后反了", "level": "warn", "rule": {"type": "line_order", "first": "主线", "second": "主线"}}
    j = J.judge(sc(problems=[warn]), rounds(0.6, 2.6, probs={3: ["rev"]}))
    assert [x["level"] for x in j["labels"]] == ["必须改", "建议改"]


def test_under_10_rounds_slowest_in_10_is_not_used():
    """少于 10 轮："10 次里最慢"没意义，不拿它判"必须改"。"""
    s = {"segments": {"a": {"name": "a", "from": "P1", "to": "P2", "range": [0, 1], "slow": 2.0}},
         "lines": [{"name": "线", "segments": ["a"], "range": [0, 1], "slow": 2.0}], "problems": []}
    rounds = [{"n": i + 1, "segs": {"a": v}, "ends": [v], "probs": []} for i, v in enumerate([0.5, 0.6, 2.5])]
    j = J.judge(s, rounds)
    lab = next(x for x in j["labels"] if x["kind"] == "line")
    assert lab["level"] != "必须改" or "10 次里最慢" not in lab["why"]


def test_line_with_fewer_than_10_values_does_not_use_10_slowest():
    """10 轮里有 2 轮没走到这条线：只剩 8 个数，"10 次里最慢"就是最慢那一个，一轮异常不该判"必须改"。"""
    rs = rounds([0.6] * 10, [1.0] * 7 + [2.6] + [1.0] * 2, n=10)
    for r in rs[:2]:
        r["ends"] = [None]
        r["segs"] = {}
    j = J.judge(sc(fair=None), rs)
    assert j["lines"][0]["count"] == 8
    assert next(x for x in j["labels"] if x["k"] == "line:主线")["level"] != "必须改"
    assert all(x["level"] != "必须改" for x in j["labels"] if x["kind"] == "spiky")


def test_keep_last_middle_point_that_overruns_the_next_step_merges_two_segments():
    """真测第 41 次：「最后一段录音发出」是记最后一次的中间点，服务器回了听写后设备还会再发几包，10 轮 7 轮比「收到听写」晚，
    「上传听写」那段成了负数。只要有一轮越过下一步，就把前后两段合成一段，第 1 轮校验也不再为它暂停。"""
    s = {"start": {"point": "P1", "name": "你说完"}, "last_points": ["P2"], "point_names": {"P2": "最后一段录音发出"},
         "segments": {"a": {"name": "发剩下的录音", "from": "P1", "to": "P2", "range": [0, 0.5], "slow": 1.0},
                      "b": {"name": "上传听写", "from": "P2", "to": "P3", "range": [0, 0.5], "slow": 1.0},
                      "c": {"name": "想回答", "from": "P3", "to": "P4"}},
         "lines": [{"name": "到开口", "segments": ["a", "b", "c"]}], "problems": []}
    rounds = [{"n": i + 1, "segs": {"a": 0.4, "b": -0.011 if i % 2 else 0.02, "c": 0.9}, "ends": [1.3], "probs": []} for i in range(10)]
    sc, rs, notes = J.merge_overruns(s, rounds)
    assert list(sc["segments"]) == ["a", "c"] and sc["segments"]["a"]["to"] == "P3" and sc["lines"][0]["segments"] == ["a", "c"]
    assert sc["segments"]["a"]["range"] == [0, 1.0] and sc["segments"]["a"]["slow"] == 2.0
    assert {r["segs"]["a"] for r in rs} == {0.389, 0.42} and "b" not in rs[0]["segs"]
    assert notes[0]["names"] == ["发剩下的录音", "上传听写"] and "最后一段录音发出" in notes[0]["why"]
    assert J.judge(sc, rs)["segments"][0]["name"] == "发剩下的录音、上传听写"
    assert m.suspects(rounds[1], s) == []
    assert J.merge_overruns(s, [{**r, "segs": {**r["segs"], "b": 0.02}} for r in rounds])[2] == []  # 没越过就不合


def test_merged_segment_gets_new_code_and_a_short_name():
    """合并后的段：编号是"起点-终点"写法的跟着改（P1-P2 → P1-P3），名字写"前一步到下一步"，原来两段的名字在合并说明里。"""
    s = {"start": {"point": "P1", "name": "你说完"}, "last_points": ["P2"],
         "point_names": {"P1": "你说完", "P2": "最后一段录音发出", "P3": "收到听写"},
         "segments": {"P1-P2": {"name": "发完剩下的录音", "from": "P1", "to": "P2"}, "P2-P3": {"name": "上传听写", "from": "P2", "to": "P3"}},
         "lines": [{"name": "到听写", "segments": ["P1-P2", "P2-P3"]}], "problems": []}
    rounds = [{"n": 1, "segs": {"P1-P2": 0.4, "P2-P3": -0.01}, "ends": [0.39], "probs": []}]
    sc, rs, notes = J.merge_overruns(s, rounds)
    assert sc["segments"] == {"P1-P3": {"name": "你说完到收到听写", "from": "P1", "to": "P3", "range": None, "slow": None}}
    assert sc["lines"][0]["segments"] == ["P1-P3"] and rs[0]["segs"] == {"P1-P3": 0.39}
    assert notes[0]["segment"] == "P1-P3" and notes[0]["names"] == ["发完剩下的录音", "上传听写"]
