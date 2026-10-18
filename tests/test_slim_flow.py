"""精简流程（决策 158）：第 1 轮收齐后自动校验计时点位置；经过网络或服务器的一段短得不合常理就停下，
不让后面白测；人看过后可以忽略、接着测。全是假设备，不真读串口。"""

import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw import measure as m
from fsw.web.app import create_app
from tests.test_collect_api import FakeSerialDevice

DEMO = Path(__file__).resolve().parent.parent / "fsw/web/demo"
DOOR = json.loads((DEMO / "door.json").read_text(encoding="utf-8"))


def door_lines(check_first, rounds=12):
    d = copy.deepcopy(DOOR)
    d["result"]["segments"]["check"][0] = check_first  # 第 1 轮"服务器校验并返回"多久
    return list(m.demo_lines(d, rounds))


def test_reversed_segment_is_suspect_but_a_very_short_server_segment_is_not():
    sc = DOOR["scenario"]
    assert m.suspects({"segs": {"check": -0.2}}, sc) == [
        {"segment": "check", "name": "服务器校验并返回", "seconds": -0.2, "why": "这段终点比起点先记到，两个计时点的先后对不上"}]
    assert m.suspects({"segs": {"check": 0.001}}, sc) == []  # 服务器连着发两条消息：真的只有一两毫秒


def test_device_segments_of_a_few_ms_are_fine():
    # "亮灯响一声"在设备里，几毫秒很正常：不校验
    assert m.suspects({"segs": {"check": 0.4, "beep": 0.004, "read": None}}, DOOR["scenario"]) == []


@pytest.fixture
def make(tmp_path):
    def _make(lines):
        dev = FakeSerialDevice(lines)
        return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                     cc_status=lambda: {"found": True, "logged_in": True},
                                     port_finder=lambda: ["/dev/cu.usbmodem1101"], device=dev, wait_step=0.01))
    return _make


def wait(c, until, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/collect").json()
        if until(j):
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def test_first_round_check_stops_measuring(make):
    c = make(door_lines(-0.2))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    j = wait(c, lambda j: j["status"] not in ("running", "waiting_device"))
    assert j["status"] == "suspect" and j["suspect"][0]["name"] == "服务器校验并返回"
    assert len([r for r in j["rounds"] if r["ok"]]) == 1  # 第 1 轮收齐就停，没接着白测


def test_ignore_and_resume_does_not_check_again(make):
    c = make(door_lines(-0.2))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    wait(c, lambda j: j["status"] == "suspect")
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10, "resume": True})
    j = wait(c, lambda j: j["status"] == "done")
    assert j["suspect"] is None and len([r for r in j["rounds"] if r["ok"]]) == 10


def test_normal_first_round_runs_through(make):
    c = make(door_lines(0.4))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    j = wait(c, lambda j: j["status"] not in ("running", "waiting_device"))
    assert j["status"] == "done" and not j.get("suspect")


def test_home_page_has_no_what_input_and_starts_with_one_button():
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert 'id="h-what"' not in js and 'data-autostart="1"' in js
    assert "let colVar='b',colN=AUTO_ROUNDS" in js and "const AUTO_ROUNDS=10" in js


def _page_with_touch_start():
    # 点屏幕开始（P1）只在点屏幕时才走；语音一轮从"你说完"（P2）起
    steps = [{"p": "P1", "n": "点屏幕开始", "line": "talk", "opt": True},
             {"p": "P2", "n": "你说完", "line": "talk"},
             {"p": "P3", "n": "收到回答", "line": "talk"},
             {"p": "P4", "n": "开口", "line": "talk"}]
    segs = [{"a": "P1", "b": "P2", "t": "听你说"}, {"a": "P2", "b": "P3", "t": "服务器", "out": True},
            {"a": "P3", "b": "P4", "t": "放声音"}]
    return {"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "开口这条"}}


def test_trigger_only_point_is_not_on_the_line_and_not_required():
    from fsw import standards as stds
    page = stds.inserted_page(_page_with_touch_start())
    assert [g["a"] for g in page["segs"]] == ["P2", "P3"]  # 线从每轮都经过的那一步起
    sc = stds.real_scenario(page, None)
    assert sc["start"]["point"] == "P2" and sc["optional"] == ["P1"]
    spec = m.Spec.from_scenario(sc)
    assert "P1" not in spec.need and {"P2", "P3", "P4"} <= set(spec.need)


def test_identify_marks_trigger_only_steps_optional():
    from fsw import identify
    assert '"always"' in identify.PROMPT
    r = {"steps": [{"id": "P1", "name": "点屏幕", "file": "a.c", "line": 1, "line_of": "main", "why": "", "sure": "高",
                    "code": "", "loc_ok": True, "where": "", "once": "", "func": "", "doubt": "", "always": False},
                   {"id": "P2", "name": "你说完", "file": "a.c", "line": 2, "line_of": "main", "why": "", "sure": "高",
                    "code": "", "loc_ok": True, "where": "", "once": "", "func": "", "doubt": "", "always": True}],
         "segments": [{"from": "P1", "to": "P2", "name": "x"}]}
    steps = identify.to_page(r, "全程")["steps"]
    assert steps[0].get("opt") is True and "opt" not in steps[1]



def _talk_spec():
    sc = {"start": {"point": "P5", "name": "你说完"},
          "segments": {"a": {"from": "P5", "to": "P6", "name": "服务器"}, "b": {"from": "P6", "to": "P7", "name": "放声音"}},
          "lines": [{"name": "开口这条", "segments": ["a", "b"]}]}
    return m.Spec.from_scenario(sc)


def test_last_sound_ending_after_the_round_marker_counts_for_its_own_round():
    # 实测顺序：…P6 → RESET → P5（下一轮）→ P7（上一轮最后一声放完，晚 0.13 秒）→ P6 …
    us = lambda s: f"{int(s * 1e6)}"
    lines = ["FSW,RESET," + us(0) + ",0",
             "FSW,P5," + us(1.0) + ",0", "FSW,P6," + us(2.0) + ",0", "FSW,RESET," + us(4.0) + ",0",
             "FSW,P5," + us(4.05) + ",0", "FSW,P7," + us(4.13) + ",0", "FSW,P6," + us(5.0) + ",0",
             "FSW,RESET," + us(7.0) + ",0", "FSW,P7," + us(7.1) + ",0", "FSW,RESET," + us(9.0) + ",0"]
    c = m.Collector(_talk_spec())
    out = []
    for l in lines:
        out += c.feed(l)
    ok = [r for r in out if r.get("ok")]
    assert len(ok) == 2
    assert [round(x, 2) for x in ok[0]["ends"]] == [3.13] and [round(x, 2) for x in ok[1]["ends"]] == [3.05]


def test_leftover_end_of_a_round_already_judged_is_not_taken_by_the_next_round():
    # 唤醒回应那一轮没有起点（不算一轮），它最后一声放完晚到：不能被下一轮当成自己的终点
    us = lambda s: f"{int(s * 1e6)}"
    lines = ["FSW,RESET," + us(0) + ",0", "FSW,P6," + us(1.0) + ",0", "FSW,RESET," + us(3.0) + ",0",
             "FSW,P5," + us(3.05) + ",0", "FSW,P7," + us(3.1) + ",0", "FSW,P6," + us(4.0) + ",0", "FSW,P7," + us(5.0) + ",0",
             "FSW,RESET," + us(6.0) + ",0"]
    out = []
    c = m.Collector(_talk_spec())
    for l in lines:
        out += c.feed(l)
    ok = [r for r in out if r.get("ok")]
    assert len(ok) == 1 and round(ok[0]["ends"][0], 2) == 1.95
