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
    """对话（验证 06 样例报告用的那份模拟日志）。合理范围清单 10-19 去掉了：第一次测只按体验标准判，段只记用时。"""
    s = load("对话.json")
    j = J.judge(s, rounds_from_log("v07/mock_log.txt", s))
    line = j["lines"][0]
    assert line["med"] == pytest.approx(3.51, abs=0.01)
    assert line["exp"] == "差"                         # 体验标准：超过 1.5 秒算差
    assert line["verdict"] == "已记录" and line["vs"] is None   # 第一次测：没有上次可比
    assert line["combo"].startswith("体验差：慢在「")   # 直接说慢在哪一段
    seg = {g["k"]: g for g in j["segments"]}
    assert seg["think"]["verdict"] == "已记录" and seg["think"]["med"] == pytest.approx(2.68, abs=0.01)
    assert seg["queue"]["verdict"] == "推算"            # 推算的段只标推算，不判快慢
    assert all(seg[k]["verdict"] == "已记录" for k in ("asr", "net", "decode", "speak", "state", "clear", "face"))
    assert seg["asr"]["outside"] and not seg["decode"]["outside"]  # 设备外段单独标
    assert j["biggest"] == "think"
    items = {x["k"]: x for x in j["labels"]}
    assert items["line:说完到开口"]["level"] == "必须改"
    assert items["problem:sw"]["level"] == "出错" and items["problem:sw"]["rounds"] == [4, 11, 17]
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
    assert speak["med"] == pytest.approx(2.17, abs=0.01) and speak["verdict"] == "已记录" and speak["exp"] == "差"
    assert speak["combo"].startswith("体验差")
    assert face["med"] == pytest.approx(1.17, abs=0.01) and face["verdict"] == "已记录" and face["exp"] is None
    items = {x["k"]: x for x in j["labels"]}
    assert items["problem:rev"]["rounds"] == [6, 15] and items["problem:rev"]["level"] == "出错"
    assert "spiky:dec" not in items                          # 偶发特别慢不再单独出标签（10-20 用户定）
    assert items["line:说话线"]["level"] == "必须改"        # 体验差
    assert j["lead"]["med"] == pytest.approx(1.0, abs=0.01)  # 表情平均比说话早 1 秒
    assert [x["level"] for x in j["labels"]] == sorted((x["level"] for x in j["labels"]), key=J.LEVELS.index)


# ---------- 每种组合、每种标签一组数据（10-19 去掉合理范围清单：体验标准 + 和上次比 + 忽快忽慢） ----------
def sc(fair=None, seg_basis="measured", problems=()):
    return {"start": {"point": "P1", "name": "起点"},
            "segments": {"a": {"name": "前一段", "owner": "固件", "side": "device", "from": "P1", "to": "P2", "basis": seg_basis},
                         "b": {"name": "后一段", "owner": "服务器", "side": "outside", "from": "P2", "to": "P3", "basis": "measured"}},
            "lines": [{"name": "主线", "end": "终点", "segments": ["a", "b"]}],
            "experience": {"line": "主线", "fair": fair} if fair else None, "problems": list(problems)}


def rounds(a, b, n=20, probs=None):
    a = a if isinstance(a, list) else [a] * n
    b = b if isinstance(b, list) else [b] * n
    return [{"n": i + 1, "segs": {"a": a[i], "b": b[i]}, "ends": [a[i] + b[i]],
             "probs": (probs or {}).get(i + 1, [])} for i in range(n)]


def prev(line, a=0.6, b=1.0, noise=0.0):
    """上次：各段、各条线的中位数和正常波动（网页后台"和上次比"给的样子）。"""
    return {"segs": {"a": a, "b": b}, "lines": {"主线": line}, "noise": {"a": noise, "b": noise}, "noise_lines": {"主线": noise}}


def label(j):
    return next(x for x in j["labels"] if x["k"] == "line:主线")


@pytest.mark.parametrize("a,b,fair,pv,level,why", [
    (0.6, 1.0, 1.5, None, "必须改", "达标率 0%，20 次里 20 次超过标准 1.5 秒"),                         # 每轮 1.6 都超过 1.5
    ([0.3] * 20, [0.6] * 17 + [3.0] * 3, 1.5, None, "必须改", "20 次里 3 次超过标准 1.5 秒的 1.5 倍"),  # 3.3 > 2.25 的占 15% > 10%
    ([0.3] * 10, [0.6] * 7 + [1.6] * 2 + [3.0], 1.5, None, "建议改", "达标率 70%，10 次里 3 次超过标准 1.5 秒"),  # 超过 1.5 倍的正好 10%：不 > 10%，不算必须改（10-21 真测第 8 轮）
    ([0.6] * 20, [0.6] * 17 + [1.6] * 3, 1.5, None, "建议改", "达标率 85%，20 次里 3 次超过标准 1.5 秒"),  # 2.2 超标但没到 2.25：达标率 85% < 90%
    ([0.3] * 20, [0.9] * 10 + [1.8] * 10, 1.5, None, "建议改", "达标率 50%，20 次里 10 次超过标准 1.5 秒"),  # 正好一半超标：中位数 1.65 超标，原来判必须改；达标率 50% 不 < 50%，现在判建议改（10-20 用户同意）
    (0.6, 1.0, 9.0, prev(1.4), "建议改", "比上次慢了 0.20 秒"),                            # 体验可以，比上次慢 0.2（大于 0.1 秒，也大于上次的 10%）
    (0.6, 1.0, 9.0, prev(1.59), "达标", '达到及格线（体验标准，≤9.0 秒），这个标准没给"好"的档；和上次比持平'),  # 慢 0.01：持平
    (0.6, 1.0, None, prev(1.8), "达标", "比上次快了 0.20 秒"),
    (0.6, 1.0, None, None, "未判", "没有体验标准，也没有上次可比，这次只记用时"),           # 第一次测，也没有体验标准
])
def test_line_labels(a, b, fair, pv, level, why):
    j = J.judge(sc(fair=fair), rounds(a, b, n=len(b) if isinstance(b, list) else 20), pv)
    assert (label(j)["level"], label(j)["why"]) == (level, why)


def test_compare_with_last_needs_both_over_0_1s_and_10_percent():
    flat = lambda now, last: J.judge(sc(), rounds(now - 1.0, 1.0), prev(last))["lines"][0]["vs"]["change"]
    assert flat(1.6, 1.4) == "慢了"      # 差 0.2：大于 0.1 秒，也大于 1.4 的 10%
    assert flat(1.08, 1.0) == "持平"     # 差 0.08：不到 0.1 秒
    assert flat(5.3, 5.0) == "持平"      # 差 0.3：大于 0.1 秒，但不到 5.0 的 10%
    assert flat(0.8, 1.0) == "快了"
    # 以前的"两次各自的正常波动"不再用：波动再大，差够了就算慢了
    assert J.judge(sc(), rounds(0.6, 1.0), prev(1.4, noise=0.3))["lines"][0]["vs"]["change"] == "慢了"


def test_compare_with_last_only_when_both_have_10_rounds():
    j = J.judge(sc(), rounds(0.6, 1.0, n=9), prev(1.4))
    assert j["lines"][0]["vs"] is None and label(j)["level"] == "未判"


def test_combo_sentence_says_experience_and_change():
    j = J.judge(sc(fair=9.0), rounds(0.6, 1.0), prev(1.4))
    assert j["lines"][0]["combo"] == '达到及格线（体验标准，≤9.0 秒），这个标准没给"好"的档；比上次慢了 0.20 秒'
    assert J.judge(sc(), rounds(0.6, 1.0))["lines"][0]["combo"].startswith("没有体验标准、也没有上次可比")


def test_experience_bad_is_must_even_when_not_slower_than_last():
    j = J.judge(sc(fair=1.5), rounds(0.6, 1.0), prev(1.6))
    assert label(j)["level"] == "必须改"


def test_tail_should_or_must_by_how_far_over_the_standard():
    """达标率 < 90% 算建议改，超过标准 1.5 倍的轮次 > 10% 算必须改；不再单独给"偶发特别慢"出标签。"""
    lv = lambda b, **kw: label(J.judge(sc(**kw), rounds(0.6, b)))["level"]
    assert lv([1.0] * 17 + [1.9] * 3, fair=2.0) == "建议改"   # 3 轮 2.5 超标没到 3.0：达标率 85% < 90%
    assert lv([1.0] * 17 + [5.0] * 3, fair=2.0) == "必须改"   # 3 轮 5.6 超过 3.0：占 15% > 10%
    assert lv([1.0] * 19 + [5.0], fair=2.0) == "达标"         # 只有 1 轮特别慢，不到每 10 次 1 次
    assert lv([1.0] * 17 + [5.0] * 3) == "未判"               # 没有体验标准，也没有上次可比
    assert not any(x["kind"] == "spiky" for x in J.judge(sc(fair=2.0), rounds(0.6, [1.0] * 17 + [5.0] * 3))["labels"])


def test_problem_levels():
    warn = {"key": "rev", "name": "先后反了", "level": "warn", "rule": {"type": "line_order", "first": "主线", "second": "主线"}}
    bad = {"key": "sw", "name": "开头被吞", "level": "bad", "rule": {"type": "value_not", "point": "P2", "value": 6}}
    j = J.judge(sc(problems=[warn, bad]), rounds(0.6, 1.0, probs={3: ["rev", "sw"], 9: ["rev"]}))
    items = {x["k"]: x for x in j["labels"]}
    assert items["problem:rev"]["level"] == "出错"            # 对错问题单独一块，不分必须改 / 建议改
    assert items["problem:sw"]["level"] == "出错"
    j2 = J.judge(sc(problems=[warn]), rounds(0.6, 1.0, probs={k: ["rev"] for k in (1, 5, 9)}))
    assert next(x for x in j2["labels"] if x["k"] == "problem:rev")["rounds"] == [1, 5, 9]


def test_segment_verdicts_and_estimated():
    j = J.judge(sc(seg_basis="estimated"), rounds(0.9, 1.5))
    seg = {g["k"]: g for g in j["segments"]}
    assert seg["a"]["verdict"] == "推算" and seg["b"]["verdict"] == "已记录"      # 第一次测：只记用时
    seg2 = {g["k"]: g for g in J.judge(sc(), rounds(0.9, 1.0), prev(1.6, a=0.6, b=1.0))["segments"]}
    assert seg2["a"]["verdict"] == "慢了" and seg2["a"]["vs"]["d"] == pytest.approx(0.3) and seg2["b"]["verdict"] == "持平"


def test_tiers_and_first_reaction():
    two = sc()
    two["lines"].insert(0, {"name": "反应线", "end": "亮灯", "segments": ["a"]})
    rs = [{"n": i + 1, "segs": {"a": 0.5 + i / 100, "b": 1.0}, "ends": [0.5 + i / 100, 1.5 + i / 100], "probs": []} for i in range(20)]
    j = J.judge(two, rs)
    assert j["first"]["line"] == "反应线"
    t = j["lines"][1]
    assert t["med"] == pytest.approx(1.595) and t["p90"] == pytest.approx(1.67) and t["max"] == pytest.approx(1.69)


def test_fewer_than_20_rounds_is_reference_only():
    assert J.judge(sc(), rounds(0.6, 1.0, n=12))["few"] and not J.judge(sc(), rounds(0.6, 1.0))["few"]


def test_labels_sorted_must_first():
    warn = {"key": "rev", "name": "先后反了", "level": "warn", "rule": {"type": "line_order", "first": "主线", "second": "主线"}}
    j = J.judge(sc(fair=1.5, problems=[warn]), rounds(0.6, 2.6, probs={3: ["rev"]}))
    assert [x["level"] for x in j["labels"]] == ["出错", "必须改"]   # 出错（功能坏了）排在必须改前面


def test_errors_first_and_heavier_first_within_a_level():
    """出错（功能坏了）排在必须改前面；同一级里出错轮次多的在前。"""
    a = {"key": "a", "name": "甲", "level": "bad", "rule": {"type": "value_not", "point": "P2", "value": 6}}
    b = {"key": "b", "name": "乙", "level": "bad", "rule": {"type": "value_not", "point": "P2", "value": 6}}
    j = J.judge(sc(fair=1.5, problems=[a, b]), rounds(0.6, 2.6, probs={1: ["a"], 2: ["b"], 3: ["b"]}))
    assert [x["k"] for x in j["labels"]] == ["problem:b", "problem:a", "line:主线"]


def test_under_10_rounds_slowest_in_10_is_not_used():
    """少于 10 轮：只按达标率 < 50% 判，超过 1.5 倍的轮次占比不拿来判"必须改"。"""
    s = {"segments": {"a": {"name": "a", "from": "P1", "to": "P2"}},
         "lines": [{"name": "线", "segments": ["a"]}], "experience": {"line": "线", "fair": 2.0}, "problems": []}
    rounds = [{"n": i + 1, "segs": {"a": v}, "ends": [v], "probs": []} for i, v in enumerate([0.5, 0.6, 2.5])]
    j = J.judge(s, rounds)
    lab = next(x for x in j["labels"] if x["kind"] == "line")
    assert lab["level"] != "必须改"   # 3 轮里 1 轮 2.5 超标：达标率 67%，不 < 50%


def test_line_with_fewer_than_10_values_does_not_use_10_slowest():
    """10 轮里有 2 轮没走到这条线：只剩 8 个数，不满 10 个，一轮异常不该判"必须改"。"""
    rs = rounds([0.6] * 10, [1.0] * 7 + [2.6] + [1.0] * 2, n=10)
    for r in rs[:2]:
        r["ends"] = [None]
        r["segs"] = {}
    j = J.judge(sc(fair=2.0), rs)
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
    assert "range" not in sc["segments"]["a"] and "slow" not in sc["segments"]["a"]   # 合理范围不再用（10-19）
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
    assert sc["segments"] == {"P1-P3": {"name": "你说完到收到听写", "from": "P1", "to": "P3"}}
    assert sc["lines"][0]["segments"] == ["P1-P3"] and rs[0]["segs"] == {"P1-P3": 0.39}
    assert notes[0]["segment"] == "P1-P3" and notes[0]["names"] == ["发完剩下的录音", "上传听写"]


# ---------- 体验标准分档（10-19 用户定）：标准给了"好"才分三档，只有及格线的不编"好"的数 ----------
def sc_std(fair, good=None, short="某标准"):
    s = sc()
    s["experience"] = {"line": "主线", "fair": fair, "good": good, "short": short}
    return s


@pytest.mark.parametrize("fair,good,want_exp,want_why", [
    (2.0, 0.8, "可以", "可以，离好（0.8 秒）还差 0.80 秒"),                      # 语音对话那样：有"好"的档
    (2.0, 2.0, "好", "体验好（某标准：≤2.0 秒算好）"),
    (1.0, 0.1, "差", "达标率 0%，20 次里 20 次超过标准 1.0 秒"),                                         # 尼尔森通用三档：1 秒以上算差
    (2.0, None, "可以", '达到及格线（某标准，≤2.0 秒），这个标准没给"好"的档'),  # 门禁那样：只有及格线
])
def test_experience_tiers(fair, good, want_exp, want_why):
    j = J.judge(sc_std(fair, good), rounds(0.6, 1.0))
    assert j["lines"][0]["exp"] == want_exp
    assert label(j)["why"].replace("体验可以，离好", "可以，离好") == want_why
    assert label(j)["level"] == ("必须改" if want_exp == "差" else "达标")   # 好 / 可以都不影响必须改、建议改


def test_good_tier_does_not_change_slower_than_last_time():
    j = J.judge(sc_std(2.0, 0.8), rounds(0.6, 1.0), prev(1.4))
    assert label(j)["level"] == "建议改" and label(j)["why"] == "比上次慢了 0.20 秒"


def test_best_fix_is_the_biggest_segment_the_device_code_can_change():
    s = sc()
    s["segments"]["c"] = {"name": "设备解码", "owner": "固件", "side": "device", "from": "P3", "to": "P4", "basis": "measured"}
    s["segments"]["d"] = {"name": "估计的一段", "owner": "固件", "side": "device", "from": "P4", "to": "P5", "basis": "estimated"}
    s["lines"][0]["segments"] = ["a", "b", "c", "d"]
    rs = [{"n": i + 1, "segs": {"a": 0.2, "b": 1.0, "c": 0.3, "d": 0.9}, "ends": [2.4], "probs": []} for i in range(10)]
    bf = J.judge(s, rs)["best_fix"]
    assert bf["k"] == "c" and bf["share"] == 12.5          # 服务器那段（设备外）、推算的段不算；只写占多少
    assert bf["device_share"] == 20.8                      # 设备上能改的段合计：a 0.2 + c 0.3 = 0.5，占 2.4 的 20.8%


def test_best_fix_skips_answer_length_segments():
    """语音那样：到第一声那条线是要看的；放回答那几段长短看回答多长，就算是设备上的段也不算"最值得优化"。"""
    s = {"start": {"point": "P1", "name": "起点"},
         "segments": {"a": {"name": "服务器想回答", "side": "outside", "from": "P1", "to": "P2"},
                      "b": {"name": "解码送喇叭", "side": "device", "from": "P2", "to": "P3"},
                      "c": {"name": "边收边放", "side": "device", "from": "P3", "to": "P4"}},
         "lines": [{"name": "到第一声", "segments": ["a", "b"], "first": True}, {"name": "到放完", "segments": ["a", "b", "c"]}],
         "problems": []}
    rs = [{"n": i + 1, "segs": {"a": 1.0, "b": 0.02, "c": 3.0 + i * 0.5}, "ends": [1.02, 4.02 + i * 0.5], "probs": []} for i in range(10)]
    bf = J.judge(s, rs)["best_fix"]
    assert bf["k"] == "b" and bf["line"] == "到第一声"


def test_best_fix_uses_the_first_felt_line_even_when_a_side_line_is_faster():
    """真测 22:49 回放：表情线比开口线快，"最值得优化的一段"照样在开口那条线上找（不是用时最短的那条）。"""
    s = {"start": {"point": "P1", "name": "起点"},
         "segments": {"f": {"name": "设备换表情", "side": "device", "from": "P1", "to": "P5"},
                      "a": {"name": "服务器", "side": "outside", "from": "P1", "to": "P2"},
                      "b": {"name": "解码送喇叭", "side": "device", "from": "P2", "to": "P3"},
                      "c": {"name": "边收边放", "side": "device", "from": "P3", "to": "P4"}},
         "lines": [{"name": "表情线", "segments": ["f"]}, {"name": "到第一声", "segments": ["a", "b"], "first": True},
                   {"name": "到放完", "segments": ["a", "b", "c"]}], "problems": []}
    rs = [{"n": i + 1, "segs": {"f": 0.5, "a": 1.0, "b": 0.02, "c": 2.0}, "ends": [0.5, 1.02, 3.02], "probs": []} for i in range(10)]
    assert J.judge(s, rs)["best_fix"]["line"] == "到第一声"


def test_biggest_is_on_the_main_line_not_the_answer_length_line():
    """主线和报告一样不算"看回答长短"的线：大头那一段要在主线自己里找（10-20 修）。"""
    s = {"segments": {"a": {"name": "想", "from": "P1", "to": "P2"}, "b": {"name": "放", "from": "P2", "to": "P3"}},
         "lines": [{"name": "开口", "segments": ["a"], "first": True}, {"name": "放完", "segments": ["a", "b"]}],
         "experience": {"line": "开口", "fair": 2.0}, "problems": []}
    rs = [{"n": i + 1, "segs": {"a": 1.0, "b": 2.0 + i * 0.3}, "ends": [1.0, 3.0 + i * 0.3], "probs": []} for i in range(10)]
    j = J.judge(s, rs)
    assert j["lines"][1]["verdict"] == J.ANSWER and j["biggest"] == "a"


def test_must_by_big_rounds_names_the_rounds_for_the_round_chart():
    # 10-21 真测：必须改写的是"超过标准 1.5 倍"，逐轮图却没标是哪几轮
    j = J.judge(sc(fair=1.5), rounds([0.3] * 20, [0.6] * 17 + [3.0] * 3))
    assert (label(j)["rounds"], label(j)["tail"]) == ([18, 19, 20], 1.5)
    assert "rounds" not in label(J.judge(sc(fair=1.5), rounds(0.6, 1.0)))   # 达标率低的必须改不点名轮次：看红虚线
