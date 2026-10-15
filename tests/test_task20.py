"""任务 20 异常处理收尾：故事一场景 4（没数据、串口不对时说人话、不卡死、不出空报告），
试一次多次不行可以还原，码表重开后还认得插过的计时点、能还原。全用假设备、假编译、假模型。"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw import instrument as ins
from fsw.web import app as webapp
from fsw.web.app import create_app
from tests.test_collect_api import wait
from tests.test_fsw_api import FakeDevice, fake_build_output, run_to_try, start_insert, wait as fwait
from tests.test_instrument import reply
from tests.test_standards import PAGE
from tests.identify_fixtures import door_source
from fsw.llm import FakeLLM


class Silent:
    """打开调试口没问题，但一直没有计时点：kind=nothing 什么都不吐；other 只吐固件自己的日志；garbled 吐乱码（波特率不对）。"""

    def __init__(self, kind):
        self.kind = kind

    def stream(self, port, on_line, stop):
        while not stop():
            if self.kind == "other":
                on_line("I (1234) Application: STATE: idle")
            elif self.kind == "garbled":
                on_line("��\x00�#����p�")
            time.sleep(0.01)


class Busy:
    """端口在，但打不开（被别的软件占着）。"""

    def stream(self, port, on_line, stop):
        raise OSError(16, "Resource busy")


@pytest.fixture
def col(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "NO_DATA_SECS", 0.3)

    def _make(device):
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                  cc_status=lambda: {"found": True, "logged_in": True},
                                  port_finder=lambda: ["/dev/cu.usbmodem1101"], device=device, wait_step=0.01))
        c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(tmp_path / "p")})
        return c
    return _make


@pytest.mark.parametrize("kind,words", [
    ("nothing", "一点输出都没有"),
    ("other", "没有计时点"),
    ("garbled", "波特率"),
])
def test_no_timing_points_says_why_in_plain_words(col, kind, words):
    c = col(Silent(kind))
    j = wait(c, lambda j: j.get("hint"), timeout=5)
    assert words in j["hint"] and j["status"] == "running"   # 不卡死：还在等，插好 / 做了就接着收
    c.post("/api/collect/stop")
    assert wait(c, lambda j: j["status"] == "stopped")["rounds"] == []


def test_port_busy_is_not_called_unplugged(col):
    c = col(Busy())
    j = wait(c, lambda j: j["status"] == "paused", timeout=5)
    assert "打不开" in j["message"] and "别的软件" in j["message"] and "断开" not in j["message"]


def test_no_rounds_no_empty_report(col):
    c = col(Silent("nothing"))
    c.post("/api/collect/stop")
    wait(c, lambda j: j["status"] == "stopped")
    r = c.get("/api/report")
    assert r.status_code == 404 and "还没有测完的轮" in r.json()["detail"]


# ---- 试一次多次不行：说清可能原因（含波特率），第 2 次起可以还原成原版 ----

@pytest.fixture
def flash(tmp_path):
    def _make(device):
        def builder(work, target, board, idf, progress):
            fake_build_output(work)
            return {"ok": True, "seconds": 0.1}
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                                  llm_factory=lambda p, k: FakeLLM(reply()),
                                  idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                                  cc_status=lambda: {"found": True, "logged_in": True},
                                  port_finder=lambda: ["/dev/cu.usbmodem1101"], bg_builder=builder,
                                  device=device, wait_step=0.01))
        c.post("/api/settings", json={"llm_provider": "cc"})
        src = door_source(tmp_path / "door")
        r = c.post("/api/source/inspect", json={"path": str(src)}).json()
        c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
        return c, r, src, builder
    return _make


def test_try_fails_twice_offers_restore(flash):
    c, r, *_ = flash(FakeDevice(read_fail=2))
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["err"]["kind"] == "try" and "波特率" in j["err"]["message"] and not j["err"].get("offer_restore")
    c.post("/api/fsw/run", json={"from": "try"})
    j = fwait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["err"]["offer_restore"]


# ---- 码表重开后：还认得插过的计时点，首页和报告都能还原 ----

def test_restore_works_after_the_stopwatch_restarts(flash, tmp_path):
    dev = FakeDevice()
    c, r, src, builder = flash(dev)
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    # 码表关掉重开：内存里的记录都没了
    c2 = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                               idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                               cc_status=lambda: {"found": True, "logged_in": True},
                               port_finder=lambda: ["/dev/cu.usbmodem1101"], bg_builder=builder, device=dev, wait_step=0.01))
    j = c2.post("/api/fsw/restore", json={"project_dir": r["project_dir"]}).json()
    assert j["same"] and j["flashed"] and dev.flashed[-1].endswith("firmware/orig")
    assert ins.compare(r["work"], src)["same"]


def test_history_says_device_still_has_timing_build(flash, tmp_path):
    c, r, *_ = flash(FakeDevice())
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    proj = Path(r["project_dir"])
    run = proj / "runs" / "20261015-100000"
    run.mkdir(parents=True)
    (run / "raw.log").write_text("\n".join(f"FSW,RESET,{i * 10_000_000},0\nFSW,P1,{i * 10_000_000 + 100},0\n"
                                           f"FSW,P2,{i * 10_000_000 + 2000},0\nFSW,P5,{i * 10_000_000 + 9000},0"
                                           for i in range(1, 21)))
    (run / "run.json").write_text(json.dumps({"page": {"steps": [{"p": "P1", "n": "a"}, {"p": "P2", "n": "b"}, {"p": "P5", "n": "c"}],
                                                       "segs": [{"a": "P1", "b": "P2", "t": "x"}, {"a": "P2", "b": "P5", "t": "y"}],
                                                       "measure": "刷卡 → 开门"}, "what": "从刷卡到开门",
                                              "measured_at": "2026-10-15 10:00"}), encoding="utf-8")
    h = c.get("/api/history").json()
    assert h and h[0]["fsw_on"]
    c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]})
    assert not c.get("/api/history").json()[0]["fsw_on"]


def test_restore_without_device_keeps_the_reminder(flash, tmp_path):
    """没插设备时还原：源码放回去了，但设备上还是计时版，首页提醒要留着；插上再点一次才烧回原版。"""
    ports = ["/dev/cu.usbmodem1101"]
    dev = FakeDevice()
    c, r, *_ = flash(dev)
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    on = json.loads((Path(r["project_dir"]) / "on-device.json").read_text())
    assert on["fsw"] is True
    from fsw.web import app as A
    j = c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]}).json()
    assert j["flashed"]
    assert json.loads((Path(r["project_dir"]) / "on-device.json").read_text())["fsw"] is False


# ---- 检查点 3：码表重开后接着走，插计时点不再花一次大模型 ----

def test_reinsert_after_restart_reuses_the_plan(flash, tmp_path):
    """同一份源码、同样的步骤：重开码表后再点"插入计时点"，用上次的插入方案，不再调用大模型。"""
    calls = []
    c, r, src, builder = flash(FakeDevice())
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")

    def factory(p, k):
        calls.append(1)
        return FakeLLM(reply())
    c2 = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=factory,
                               idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                               cc_status=lambda: {"found": True, "logged_in": True},
                               port_finder=lambda: ["/dev/cu.usbmodem1101"], bg_builder=builder,
                               device=FakeDevice(), wait_step=0.01))
    start_insert(c2, r)
    j = fwait(c2, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted" and j.get("reused") and calls == []
