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


def test_outside_segment_too_short_is_suspect():
    sc = DOOR["scenario"]
    assert m.suspects({"segs": {"check": 0.003, "beep": 0.004}}, sc) == [
        {"segment": "check", "name": "服务器校验并返回", "seconds": 0.003, "why": "这段要经过网络或服务器，正常不会短于 0.05 秒"}]
    assert m.suspects({"segs": {"check": -0.2}}, sc)[0]["why"].startswith("这段终点比起点先记到")


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
    c = make(door_lines(0.003))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    j = wait(c, lambda j: j["status"] not in ("running", "waiting_device"))
    assert j["status"] == "suspect" and j["suspect"][0]["name"] == "服务器校验并返回"
    assert len([r for r in j["rounds"] if r["ok"]]) == 1  # 第 1 轮收齐就停，没接着白测


def test_ignore_and_resume_does_not_check_again(make):
    c = make(door_lines(0.003))
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


# ===== 电脑朗读代替人说话 =====
from fsw import speech as voice


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def __call__(self):
        return self.t


def test_drive_wakes_once_waits_for_reply_to_end_then_asks_each_round():
    # 设备听到唤醒词先回应约 4 秒：期间日志会停（不能看安静），说完才打一轮结束标记；之后接着听，不用再唤醒
    clk, said, st = Clock(), [], {"marks": 0, "resets": 0, "busy": 0.0, "end": None}

    def speak(t):
        said.append((t, clk.t))
        st["marks"] += 1
        st["busy"] = clk.t + 4.0
        st["end"] = clk.t + 4.0

    def resets():
        if st["end"] is not None and clk.t >= st["end"]:
            st["resets"] += 1
            st["end"] = None
        return st["resets"]
    idle = lambda: resets() is not None and clk.t >= st["busy"] + 3
    stop = lambda: len(said) >= 4 and clk.t > st["busy"]
    r = voice.drive(speak, lambda: "一加一等于几", idle, lambda: st["marks"], stop, lambda t: None,
                    wake="你好", sleep=clk.sleep, clock=clk, resets=resets)
    assert r == "stopped" and [t for t, _ in said] == ["你好", "一加一等于几", "一加一等于几", "一加一等于几"]
    assert said[1][1] - said[0][1] >= 4.0  # 等唤醒回应说完才问


def test_drive_wakes_again_after_device_did_not_react():
    clk, said, hints = Clock(), [], []
    voice.drive(lambda t: said.append(t), lambda: "一加一等于几", lambda: True, lambda: 0, lambda: False, hints.append,
                wake="你好", sleep=clk.sleep, clock=clk, resets=lambda: 0)
    assert said == ["你好", "一加一等于几"] * 3


def test_drive_gives_up_after_three_silent_tries_with_volume_hint():
    clk, hints = Clock(), []
    r = voice.drive(lambda t: None, lambda: "一加一等于几", lambda: True, lambda: 0, lambda: False, hints.append,
                    sleep=clk.sleep, clock=clk)
    assert r == "gave_up" and "音量" in hints[0] and "已停止语音播放" in hints[-1]


def test_collect_with_computer_speaking(tmp_path):
    said = []
    lines = door_lines(0.4)

    class Dev(FakeSerialDevice):  # 电脑播一句，设备才吐一轮
        def stream(self, port, on_line, stop):
            heard = 0
            while not stop():
                if len(said) > heard and self.pos < len(lines):  # 吐完这一轮（到下一个 RESET 之前）
                    heard += 1
                    on_line(lines[self.pos]); self.pos += 1
                    while self.pos < len(lines) and "RESET" not in lines[self.pos]:
                        on_line(lines[self.pos]); self.pos += 1
                time.sleep(0.001)
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/x"],
                              device=Dev(lines), wait_step=0.01, speaker=said.append))
    assert c.post("/api/speak", json={"on": True, "items": ["一加一等于几"]}).json()["on"] is True
    voice_idle = voice.IDLE_SECS
    voice.IDLE_SECS = 0.05
    try:
        c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 3})
        j = wait(c, lambda j: j["status"] == "done", timeout=20)
    finally:
        voice.IDLE_SECS = voice_idle
    assert j["speak"] is True and said and set(said) == {"一加一等于几"}


def test_give_up_hint_names_the_button_of_the_page():
    clk, hints = Clock(), []
    voice.drive(lambda t: None, lambda: "一加一等于几", lambda: True, lambda: 0, lambda: False, hints.append,
                sleep=clk.sleep, clock=clk, again="再试一次")
    assert "「再试一次」" in hints[-1]


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
