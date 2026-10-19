"""任务 16：读合理范围。真找到的步骤按代码位置对上标准里的计时点，接上那一段、那条线的合理范围。"""

import json
from pathlib import Path

import pytest

from fsw import judge as J
from fsw import standards as std


@pytest.fixture(autouse=True)
def _xiaozhi_standard(monkeypatch):
    """这些测试按"用户放进了小智那份标准"来跑：10-18 起码表不自带任何设备的标准，小智那份只当测试素材。"""
    from fsw import standards as _stds
    monkeypatch.setattr(_stds, "HERE", Path(__file__).parent / "fixtures")


# 任务 13 第二次真识别、任务 14 真插的 9 个计时点（文件、行号）
REAL_STEPS = [
    {"p": "P1", "n": "本地听到说完", "path": "main/audio/engines/afe_audio_engine.cc", "l": 483, "line": "start"},
    {"p": "P2", "n": "收到听写文字", "path": "main/application.cc", "l": 656, "line": "talk"},
    {"p": "P3", "n": "收到表情指令", "path": "main/application.cc", "l": 671, "line": "face"},
    {"p": "P4", "n": "表情真正换上", "path": "main/application.cc", "l": 674, "line": "face"},
    {"p": "P5", "n": "收到开始说话", "path": "main/application.cc", "l": 620, "line": "talk"},
    {"p": "P6", "n": "切到说话状态", "path": "main/application.cc", "l": 1054, "line": "talk"},
    {"p": "P7", "n": "收到首包声音", "path": "main/application.cc", "l": 555, "line": "talk"},
    {"p": "P8", "n": "首段声音解码好", "path": "main/audio/audio_service.cc", "l": 453, "line": "talk"},
    {"p": "P9", "n": "首段声音进喇叭", "path": "main/audio/audio_service.cc", "l": 351, "line": "talk"},
]
PAGE = {"steps": REAL_STEPS, "ln": {"start": "起点", "talk": "开口这条", "face": "表情这条"},
        "segs": [{"a": "P1", "b": "P2", "t": "服务器听写", "who": "服务器", "out": True},
                 {"a": "P2", "b": "P5", "t": "服务器想回答", "who": "服务器", "out": True},
                 {"a": "P5", "b": "P6", "t": "设备切换状态", "who": "固件"},
                 {"a": "P6", "b": "P7", "t": "等第一包声音", "who": "网络", "out": True},
                 {"a": "P7", "b": "P8", "t": "解压第一包", "who": "固件"},
                 {"a": "P8", "b": "P9", "t": "送进喇叭", "who": "固件"}],
        "fsegs": [{"a": "P2", "b": "P3", "t": "服务器发来表情", "who": "服务器", "out": True},
                  {"a": "P3", "b": "P4", "t": "设备换上表情", "who": "固件"}]}


def test_xiaozhi_standard_is_found_and_points_match_by_location():
    s = std.find(REAL_STEPS)
    assert s and s["applies"]["repo"] == "github.com/78/xiaozhi-esp32"
    mp = std.match_points(REAL_STEPS, s)
    assert mp == {"P1": "P2", "P2": "P4", "P3": "P11", "P4": "P12", "P5": "P5", "P6": "P13",
                  "P7": "P7", "P8": "P9", "P9": "P10"}


def test_no_standard_for_unknown_code():
    assert std.find([{"p": "P1", "path": "main/door.c", "l": 10}, {"p": "P2", "path": "main/door.c", "l": 20}]) is None


def test_real_scenario_takes_ranges_from_the_standard():
    sc = std.real_scenario(PAGE, std.find(REAL_STEPS))
    segs = sc["segments"]
    assert segs["P1-P2"]["range"] == [0.3, 1.0] and segs["P1-P2"]["slow"] == 1.2        # 服务器判断你说完 + 听写
    assert segs["P2-P5"]["range"] == [0.4, 1.5]                                          # 大模型想回答
    assert segs["P5-P6"]["range"] == pytest.approx([0.002, 0.025])                       # 切状态 + 清空播放器，两段加起来
    assert segs["P6-P7"]["range"] is None                                                # 标准里没有这一段
    assert segs["P7-P8"]["basis"] == "estimated"                                         # 含推算的那段：只标推算
    assert segs["P2-P3"]["range"] == [0.2, 0.6] and segs["P3-P4"]["range"] == [0.001, 0.02]
    face, talk = sc["lines"]
    assert talk["name"] == "开口这条" and talk["range"] == [0.7, 2.75]
    assert face["name"] == "表情这条" and face["range"] == [0.5, 1.6] and face["slow"] == 2.0
    assert face["segments"][0] == "P1-P2"                                                # 表情这条也从起点算
    [e] = sc["experiences"]  # 体验标准每条线一条：标准里只有说话线有，挂到对上的真线
    assert (e["line"], e["fair"], e["good"], e["short"]) == ("开口这条", 1.5, 0.8, "参考值") and "不是国家或行业标准" in e["source"]  # 决策 137
    assert [p["key"] for p in sc["problems"]] == ["rev"]  # 附加值的规则对不上真插的计时点，不用


def test_page_without_standard_has_no_ranges_but_still_judges():
    sc = std.real_scenario(PAGE, None)
    assert all(g["range"] is None for g in sc["segments"].values()) and sc["experiences"] == []
    rounds = [{"n": i + 1, "segs": {k: 0.1 for k in sc["segments"]}, "ends": [0.3, 0.6], "probs": []} for i in range(20)]
    j = J.judge(sc, rounds)
    assert j["lines"][-1]["verdict"] == J.RECORDED and j["labels"][0]["level"] == "未判"  # 没体验标准、也没上次可比：明说没判


# ---- 附加值：标准里看附加值的问题规则，对上真插的计时点，插的时候顺带记下；记上了才判 ----

def test_needed_values_follow_the_rules_of_the_matched_standard():
    s = std.find(REAL_STEPS)
    need = std.needed_values(REAL_STEPS, s)
    assert list(need) == ["P7"]  # 标准里的"第一包回答声音到达"就是真插的 P7
    assert need["P7"]["what"] == "设备当时的状态" and need["P7"]["value"] == 6 and need["P7"]["ok"] == "说话中"
    assert "回答开头被吞" in need["P7"]["for"]


def test_no_standard_no_values():
    assert std.needed_values(REAL_STEPS, None) == {}


def test_value_rule_kept_only_when_the_point_really_records_the_value():
    s = std.find(REAL_STEPS)
    assert [p["key"] for p in std.real_scenario(PAGE, s)["problems"]] == ["rev"]  # 没记附加值：查不了吞字
    sc = std.real_scenario(PAGE, s, values={"P7": {"expr": "(int)GetDeviceState()", "expect": 6}})
    sw = next(p for p in sc["problems"] if p["key"] == "sw")
    assert sw["rule"] == {"type": "value_not", "point": "P7", "value": 6,
                          "value_of": sw["rule"]["value_of"]}  # 真插里没有"排进队列"那个点，另一条规则不用


def test_value_number_comes_from_the_code_when_ai_read_a_different_one():
    sc = std.real_scenario(PAGE, std.find(REAL_STEPS), values={"P7": {"expr": "x", "expect": 4}})
    assert next(p for p in sc["problems"] if p["key"] == "sw")["rule"]["value"] == 4
