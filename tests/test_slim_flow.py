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


def test_drive_speaks_wake_word_then_sentence_only_when_idle_and_waits_for_reaction():
    clk, said, marks = Clock(), [], {"n": 0}
    busy = {"until": 0.0}

    def speak(t):
        said.append(t)
        if t != "你好":  # 设备听到句子：收到计时点，忙 5 秒
            marks["n"] += 1
            busy["until"] = clk.t + 5
    stop = lambda: len([x for x in said if x != "你好"]) >= 3 and clk.t > busy["until"]
    r = voice.drive(speak, lambda: f"第{marks['n'] + 1}句", lambda: clk.t >= busy["until"], lambda: marks["n"], stop,
                    lambda t: None, wake="你好", sleep=clk.sleep, clock=clk)
    assert r == "stopped" and said == ["你好", "第1句", "你好", "第2句", "你好", "第3句"]


def test_drive_waits_for_device_to_finish_answering_wake_word():
    # 设备听到唤醒词先回应约 4 秒（调试口一直有输出），回应完才播句子
    clk, said = Clock(), []
    last = {"at": -100.0}

    def speak(t):
        said.append((t, clk.t))
        if t == "你好":
            last["at"] = clk.t + 4.0

    def quiet():
        if last["at"] - 4.0 <= clk.t <= last["at"]:
            return 0.0
        return clk.t - last["at"]
    voice.drive(speak, lambda: "一加一等于几", lambda: True, lambda: 0, lambda: len(said) >= 2, lambda t: None,
                wake="你好", sleep=clk.sleep, clock=clk, quiet=quiet)
    assert said[0][0] == "你好" and said[1][0] == "一加一等于几"
    assert said[1][1] - said[0][1] >= 4.0 + 2.0 - 0.01


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
