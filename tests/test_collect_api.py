"""任务 15 的网页后台：多轮实测。开始 → 一轮轮收 → 停止 → 接着测（轮数和数据连得上）→ 从头重测；
没收全自动补测；设备中途断开自动暂停、插回接着测；设备原样输出存一份。全是假设备，不真读串口。"""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw import measure as m
from fsw.web.app import create_app


@pytest.fixture(autouse=True)
def _xiaozhi_standard(monkeypatch):
    """这些测试按"用户放进了小智那份标准"来跑：10-18 起码表不自带任何设备的标准，小智那份只当测试素材。"""
    from fsw import standards as _stds
    monkeypatch.setattr(_stds, "HERE", Path(__file__).parent / "fixtures")


DEMO = Path(__file__).resolve().parent.parent / "fsw/web/demo"


class FakeSerialDevice:
    """假设备：把一份日志一行行吐出来；break_at=k 时吐到第 k 行像拔了线一样断开（只断一次）。"""

    def __init__(self, lines, break_at=None, gap=0.002):
        self.lines, self.break_at, self.gap, self.pos, self.opened = list(lines), break_at, gap, 0, 0

    def stream(self, port, on_line, stop):
        self.opened += 1
        while self.pos < len(self.lines) and not stop():
            if self.break_at is not None and self.pos == self.break_at:
                self.break_at = None
                raise OSError("device disconnected")
            on_line(self.lines[self.pos])
            self.pos += 1
            time.sleep(self.gap)
        while not stop():
            time.sleep(0.01)


@pytest.fixture
def make(tmp_path):
    def _make(device=None, ports=("/dev/cu.usbmodem1101",)):
        port_list = list(ports)
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                  cc_status=lambda: {"found": True, "logged_in": True},
                                  port_finder=lambda: list(port_list), device=device, wait_step=0.01))
        return c, port_list
    return _make


def wait(c, until, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/collect").json()
        if until(j):
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def ok_rounds(j):
    return [r for r in j["rounds"] if r["ok"]]


def test_demo_rounds_are_parsed_from_replayed_output(make):
    c, _ = make()
    c.post("/api/collect/start", json={"demo": "door", "rounds": 20, "interval": 0})
    j = wait(c, lambda j: j["status"] == "done")
    rs = ok_rounds(j)
    door = json.loads((DEMO / "door.json").read_text(encoding="utf-8"))
    assert len(rs) == 20 and [r["n"] for r in rs if "to" in r["probs"]] == door["result"]["problems"]["to"]
    assert rs[0]["ends"][1] == pytest.approx(sum(door["result"]["segments"][k][0] for k in ("read", "check", "lock")), abs=1e-4)
    # 设备输出原样存了一份：第 20 轮收全后要等下一轮开始（第 21 个 RESET）才收尾
    assert Path(j["raw_log"]).read_text(encoding="utf-8").count("FSW,RESET") == 21


def test_incomplete_round_is_resampled_until_target(make):
    c, _ = make()
    c.post("/api/collect/start", json={"demo": "voice", "rounds": 20, "interval": 0, "drop_at": 9})
    j = wait(c, lambda j: j["status"] == "done")
    assert len(ok_rounds(j)) == 20 and len(j["rounds"]) == 21
    bad = [r for r in j["rounds"] if not r["ok"]]
    assert bad[0]["att"] == 9 and bad[0]["missing"]


def test_stop_then_continue_keeps_numbers_and_data(make):
    lines = list(m.demo_lines(json.loads((DEMO / "voice.json").read_text(encoding="utf-8")), 25))  # 停下时正在收的那轮会丢，多备几轮
    dev = FakeSerialDevice(lines, gap=0.01)
    c, _ = make(device=dev)
    c.post("/api/collect/start", json={"demo": "voice", "source": "device", "rounds": 20})
    wait(c, lambda j: len(ok_rounds(j)) >= 5)
    c.post("/api/collect/stop")
    j = wait(c, lambda j: j["status"] == "stopped")
    k = len(ok_rounds(j))
    assert 5 <= k < 20
    c.post("/api/collect/start", json={"demo": "voice", "source": "device", "rounds": 20, "resume": True})
    j = wait(c, lambda j: j["status"] == "done")
    rs = ok_rounds(j)
    assert [r["n"] for r in rs] == list(range(1, 21))  # 从第 k+1 轮接着，编号连着
    voice = json.loads((DEMO / "voice.json").read_text(encoding="utf-8"))
    assert [round(r["segs"]["think"], 4) for r in rs[:k]] == pytest.approx(voice["result"]["segments"]["think"][:k], abs=1e-4)
    assert rs[k]["att"] > rs[k - 1]["att"]  # 停之前测的留着，接着往后记


def test_more_rounds_after_done(make):
    c, _ = make()
    c.post("/api/collect/start", json={"demo": "door", "rounds": 10, "interval": 0})
    wait(c, lambda j: j["status"] == "done")
    c.post("/api/collect/start", json={"demo": "door", "rounds": 20, "interval": 0, "resume": True})  # "再测 10 轮"
    j = wait(c, lambda j: j["status"] == "done")
    assert [r["n"] for r in ok_rounds(j)] == list(range(1, 21))


def test_restart_from_scratch(make):
    c, _ = make()
    c.post("/api/collect/start", json={"demo": "door", "rounds": 10, "interval": 0})
    first = wait(c, lambda j: j["status"] == "done")
    c.post("/api/collect/start", json={"demo": "door", "rounds": 10, "interval": 0})
    j = wait(c, lambda j: j["status"] == "done" and j["raw_log"] != first["raw_log"])
    assert [r["n"] for r in ok_rounds(j)] == list(range(1, 11))


def test_device_unplugged_pauses_then_continues(make):
    lines = list(m.demo_lines(json.loads((DEMO / "door.json").read_text(encoding="utf-8")), 22))  # 断开时那一轮会丢
    dev = FakeSerialDevice(lines, break_at=40)
    c, ports = make(device=dev)
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 20})
    j = wait(c, lambda j: j["status"] == "done")
    assert len(ok_rounds(j)) == 20 and dev.opened == 2 and j["pauses"] == 1  # 断开过一次，自动接着测完


def test_no_device_waits(make):
    lines = list(m.demo_lines(json.loads((DEMO / "door.json").read_text(encoding="utf-8")), 10))
    c, ports = make(device=FakeSerialDevice(lines), ports=())
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    wait(c, lambda j: j["status"] == "waiting_device")
    ports.append("/dev/cu.usbmodem1101")
    assert len(ok_rounds(wait(c, lambda j: j["status"] == "done"))) == 10


def test_real_steps_spec_from_page(make, tmp_path):
    """真找完的步骤（编号 P1～P9）：按结果页的段算，计时点行格式和任务 14 插的一样。"""
    page = {"segs": [{"a": "P1", "b": "P2"}, {"a": "P2", "b": "P5"}, {"a": "P5", "b": "P9"}],
            "fsegs": [{"a": "P2", "b": "P3"}, {"a": "P3", "b": "P4"}], "ln": {"talk": "开口这条", "face": "表情这条"},
            "steps": [{"p": f"P{i}", "n": f"第{i}步"} for i in range(1, 10)]}
    log = []
    for r in range(3):
        b = 1_000_000 * (r + 1) * 10
        log += [f"FSW,RESET,{b},0", f"FSW,P3,{b + 300},0", f"FSW,P4,{b + 400},0", f"FSW,P2,{b + 200},0",
                f"FSW,P1,{b + 100},2", f"FSW,P5,{b + 500},0", f"FSW,P9,{b + 900},0"]
    c, _ = make(device=FakeSerialDevice(log))
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "proj")})
    j = wait(c, lambda j: j["status"] == "done")
    rs = ok_rounds(j)
    assert len(rs) == 3 and rs[0]["ends"] == [pytest.approx(0.0003), pytest.approx(0.0008)]  # 表情这条也从起点算
    assert Path(j["raw_log"]).parent.parent == tmp_path / "proj" / "runs"


def test_unplugged_message_stays_while_waiting(make):
    lines = list(m.demo_lines(json.loads((DEMO / "door.json").read_text(encoding="utf-8")), 22))
    dev = FakeSerialDevice(lines, break_at=40)
    c, ports = make(device=dev)
    orig = dev.stream

    def unplug(port, on_line, stop):
        try:
            orig(port, on_line, stop)
        except OSError:
            ports.clear()  # 拔了线：设备也不在了
            raise
    dev.stream = unplug
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 20})
    j = wait(c, lambda j: j["status"] == "paused")
    assert "断开" in j["message"] and "留着" in j["message"]
    ports.append("/dev/cu.usbmodem1101")
    assert len(ok_rounds(wait(c, lambda j: j["status"] == "done"))) == 20


def test_report_judges_this_run_with_fixed_rules(make):
    c, _ = make()
    assert c.get("/api/report").status_code == 404  # 还没测
    c.post("/api/collect/start", json={"demo": "voice", "rounds": 20, "interval": 0})
    wait(c, lambda j: j["status"] == "done")
    r = c.get("/api/report").json()
    assert r["n"] == 20 and not r["few"]
    speak = r["lines"][-1]
    assert speak["exp"] == "差" and speak["combo"].startswith("体验差：慢在「")
    assert {x["k"]: x["level"] for x in r["labels"]}["problem:sw"] == "必须改"
    assert len(r["rounds"]) == 20 and r["scenario"]["lines"][0]["name"] == "表情线"


def test_report_for_real_steps_reads_the_xiaozhi_standard(make, tmp_path):
    from tests.test_standards import PAGE
    log = []
    for r in range(9):
        b = 10_000_000 * (r + 1)
        t = {"P1": 0, "P2": 0.75, "P3": 1.15, "P4": 1.162, "P5": 2.1, "P6": 2.11, "P7": 2.16, "P8": 2.17, "P9": 2.175}
        log += [f"FSW,RESET,{b},0"] + [f"FSW,{p},{b + 100_000 + int(v * 1e6)},0" for p, v in t.items()]
    c, _ = make(device=FakeSerialDevice(log))
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 9, "project_dir": str(tmp_path / "p")})
    wait(c, lambda j: j["status"] == "done")
    r = c.get("/api/report").json()
    assert r["few"] and r["scenario"]["standard"].startswith("小智")  # 按代码位置对上码表自带的标准（体验标准、问题规则从它来）
    assert not (tmp_path / "p" / "合理范围清单.md").exists()           # 合理范围清单 10-19 去掉了：不建
    seg = {g["k"]: g for g in r["segments"]}
    assert seg["P2-P5"]["verdict"] == "已记录" and seg["P2-P5"]["med"] == pytest.approx(1.35)  # 第一次测：只记用时
    talk = r["lines"][-1]
    assert talk["name"] == "开口这条" and talk["exp"] == "差" and talk["verdict"] == "已记录"


def test_report_for_demo_data_without_a_run(make):
    """没测过时，报告用演示数据的数，也按同一套固定规则判。"""
    c, _ = make()
    r = c.get("/api/report?demo=door").json()
    door = json.loads((DEMO / "door.json").read_text(encoding="utf-8"))
    assert r["n"] == 20 and r["lines"][-1]["name"] == "开门线"
    assert {x["k"]: x["rounds"] for x in r["labels"] if x["kind"] == "problem"}["problem:to"] == door["result"]["problems"]["to"]
    assert c.get("/api/report?demo=nope").status_code == 404


@pytest.mark.parametrize("n,ok", [(3, True), (100, True), (2, False), (101, False)])
def test_round_count_can_be_3_to_100(make, n, ok):
    """测几轮：3～100 轮都行（快捷按钮 10 / 20 / 30 之外可以自己填）；超出范围说人话。"""
    c, _ = make()
    r = c.post("/api/collect/start", json={"demo": "door", "rounds": n, "interval": 0})
    assert (r.status_code == 200) == ok
    if not ok:
        assert "3～100" in r.json()["detail"]


def test_three_rounds_give_a_report(make):
    c, _ = make()
    c.post("/api/collect/start", json={"demo": "door", "rounds": 3, "interval": 0})
    j = wait(c, lambda j: j["status"] == "done")
    assert len([x for x in j["rounds"] if x["ok"]]) == 3
    assert c.get("/api/report", params={"demo": "door"}).json()["n"] == 3
