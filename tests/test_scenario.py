import copy
import json
from pathlib import Path

import pytest

from fsw.scenario import ScenarioError, load_scenarios, to_page

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "scenarios"
DEMO = HERE.parent / "fsw" / "web" / "demo"


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,count,lines", [
    ("对话.json", 1, [1]),          # 验证 07：说完到开口
    ("表情线.json", 1, [2]),        # 验证 09：先出表情、再开口，两条线同一个起点
    ("拍照识图.json", 1, [2]),      # 验证 10：整轮 + 每次拍照（一轮里会重复的那段）
    ("设备端工具.json", 1, [3]),    # 验证 10：整轮 + 每次调用工具 + 调亮度
    ("按事件.json", 6, [1] * 6),    # 验证 11：六种事件，各自一个起点
    ("门禁刷卡.json", 1, [2]),
])
def test_five_kinds_of_scenario_fit_the_same_format(name, count, lines):
    scenarios = load_scenarios(read(EXAMPLES / name))
    assert len(scenarios) == count
    assert [len(s["lines"]) for s in scenarios] == lines
    for s in scenarios:
        assert s["recognized"] == "读代码认出来的"


def test_event_scenarios_carry_their_trigger():
    s = load_scenarios(read(EXAMPLES / "按事件.json"))
    touch = next(x for x in s if x["name"] == "摸头害羞")
    assert touch["mode"] == "event"
    assert touch["event"]["max_wait"] == 3.0
    assert touch["start"]["unseen"]["take"] == 0.01


def test_repeated_block_is_marked_each():
    s = load_scenarios(read(EXAMPLES / "拍照识图.json"))[0]
    each = [l for l in s["lines"] if l.get("each")]
    assert [l["name"] for l in each] == ["每次拍照"]


@pytest.fixture
def base():
    return read(EXAMPLES / "门禁刷卡.json")


def errors(data):
    with pytest.raises(ScenarioError) as e:
        load_scenarios(data)
    return str(e.value)


def test_more_than_four_lines_is_refused(base):
    base["lines"] = base["lines"] * 3
    assert "最多 4 条线" in errors(base)


def test_line_must_be_a_chain_from_the_start(base):
    base["lines"][1]["segments"] = ["read", "lock"]
    msg = errors(base)
    assert "接不上" in msg and "读卡号" in msg and "电磁锁动作" in msg


def test_line_must_start_at_the_starting_point(base):
    base["lines"][1]["segments"] = ["check", "lock"]
    assert "起点" in errors(base)


def test_unknown_segment(base):
    base["lines"][0]["segments"].append("nope")
    assert "没有这一段" in errors(base)


def test_side_must_be_inside_or_outside_device(base):
    base["segments"]["read"]["side"] = "cloud"
    assert "设备里" in errors(base)


def test_range_low_must_not_exceed_high(base):
    base["segments"]["read"]["range"] = [0.5, 0.1]
    assert "合理范围" in errors(base)


def test_experience_standard_must_point_to_a_line(base):
    base["experience"] = {"line": "不存在的线", "fair": 1.0, "source": "x"}
    assert "体验标准" in errors(base)


def test_event_mode_needs_event_info(base):
    base["mode"] = "event"
    assert "事件" in errors(base)


def test_problem_rule_types_are_checked(base):
    base["problems"].append({"key": "x", "name": "x", "level": "bad", "rule": {"type": "magic"}})
    assert "不认识的问题规则" in errors(base)


@pytest.mark.parametrize("sid", ["voice", "door"])
def test_demo_data_in_the_new_format_gives_the_page_exactly_what_it_had(sid):
    demo = read(DEMO / f"{sid}.json")
    before = read(HERE / "fixtures" / f"page_{sid}_before_task6.json")
    page = to_page(demo)
    assert page["S"] == before["S"]
    assert page["C"] == before["C"]


@pytest.mark.parametrize("sid", ["voice", "door"])
def test_demo_files_store_scenario_and_result_separately(sid):
    demo = read(DEMO / f"{sid}.json")
    assert "S" not in demo and "C" not in demo
    load_scenarios(demo["scenario"])
    assert set(demo["result"]["segments"]) == set(demo["scenario"]["segments"])
