"""任务 14 的网页后台：插计时点 → 确认 → 编译 → 烧录（没插设备就等）→ 试一次；撤掉一处重编；还原。

假模型、假编译、假设备，不真调用大模型、不真烧录。
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from fsw import instrument as ins
from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.identify_fixtures import door_source
from tests.test_instrument import PLAN, STEPS, reply
from tests.test_espidf import FLASHER
from pathlib import Path


def fake_build_output(work):
    """假编译也照乐鑫的样子留下烧录要用的文件。"""
    b = Path(work) / "build"
    (b / "bootloader").mkdir(parents=True, exist_ok=True)
    (b / "bootloader/bootloader.bin").write_text("B")
    (b / "app.bin").write_text("APP" + str(len(list(Path(work).rglob("*.c")))))
    (b / "flasher_args.json").write_text(json.dumps(FLASHER))


class FakeDevice:
    def __init__(self, lines=None, flash_fail=0, read_fail=0):
        self.lines = lines if lines is not None else ["boot ok", "FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,P5,30,0"]
        self.flash_fail, self.read_fail, self.flashed = flash_fail, read_fail, []

    def flash(self, fw, port, on_progress):
        on_progress(50, "Writing at 0x00020000... (50 %)")
        self.flashed.append(str(fw))
        if self.flash_fail:
            self.flash_fail -= 1
            return {"ok": False, "error": "A fatal error occurred: The chip stopped responding."}
        on_progress(100, "烧录完成")
        return {"ok": True, "seconds": 0.1}

    def read(self, port, seconds, on_line, stop=None):
        self.read_secs = seconds
        if self.read_fail:
            self.read_fail -= 1
            return ["boot ok"]
        for l in self.lines:
            on_line(l)
        return list(self.lines)


@pytest.fixture
def setup(tmp_path):
    def _setup(builder=None, device=None, ports=("/dev/cu.usbmodem1101",), llm_reply=None, start_bg=True):
        port_list = list(ports)
        dev = device or FakeDevice()
        builds = []

        def default_builder(work, target, board, idf, progress):
            builds.append(work)
            progress(50, "[2/4] Building C object")
            fake_build_output(work)
            return {"ok": True, "seconds": 0.1, "error": None}
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                                  llm_factory=lambda p, k: FakeLLM(llm_reply or reply()),
                                  idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                                  cc_status=lambda: {"found": True, "logged_in": True},
                                  port_finder=lambda: list(port_list), bg_builder=builder or default_builder,
                                  device=dev, wait_step=0.01))
        c.post("/api/settings", json={"llm_provider": "cc"})
        src = door_source(tmp_path / "door")
        r = c.post("/api/source/inspect", json={"path": str(src)}).json()
        if start_bg:
            c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
        return c, r, dev, port_list, builds, src
    return _setup


def wait(c, until, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/fsw").json()
        if until(j):
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def start_insert(c, r):
    return c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": STEPS,
                                           "chip": "ESP32-S3"}).json()


def test_insert_shows_changes_and_waits_for_confirm(setup):
    c, r, *_ = setup()
    start_insert(c, r)
    j = wait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted" and j["view"]["deleted"] == 0 and j["view"]["added"] > 0
    assert sorted(j["view"]["steps"]) == ["P1", "P2", "P5"]


def run_to_try(c, start="build"):
    """编译、烧录完直接开始试一次（决策 107：不再停下等人点"准备好了"，码表自己盯着设备开机）。"""
    c.post("/api/fsw/run", json={"from": start})
    return wait(c, lambda j: j["status"] in ("done", "failed"))


def test_confirm_runs_build_flash_try_to_done(setup):
    c, r, dev, _, builds, src = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "done" and j["got"] == [1, 2, 5] and j["total"] == 3
    assert j["port"] == "/dev/cu.usbmodem1101" and dev.flashed[-1].endswith("firmware/fsw")
    assert len(builds) == 2  # 原版一次（后台），计时版一次


def test_no_device_waits_then_continues(setup):
    c, r, dev, ports, *_ = setup(ports=())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] == "waiting_device")
    assert j["phase"] == "flash"
    ports.append("/dev/cu.usbmodem1101")
    j = wait(c, lambda j: j["status"] == "done")
    assert j["port"] == "/dev/cu.usbmodem1101" and j["got"] == [1, 2, 5]


def test_build_error_points_to_the_step_and_drop_rebuilds(setup):
    state = {"n": 0}

    def builder(work, target, board, idf, progress):
        state["n"] += 1
        if state["n"] == 2:  # 第 1 次是后台编原版；第 2 次编计时版时出错，报在 P2 那两行
            return {"ok": False, "seconds": 0.1, "error": f"{work}/main/rfid_task.c:137:5: error: expected ';'"}
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c, r, *_ = setup(builder=builder)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["status"] == "failed" and j["err"]["kind"] == "build" and j["err"]["step"] == "P2"
    assert j["err"]["step_name"] == "卡号读完" and "rfid_task.c" in j["err"]["where"]
    assert j["err"]["error"].startswith("main/rfid_task.c:137")  # 报错里的路径只留项目里的那段
    c.post("/api/fsw/drop", json={"step": "P2"})
    j = wait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["status"] == "done" and "P2" not in j["view"]["steps"]


def test_burn_interrupted_then_reburn(setup):
    c, r, dev, *_ = setup(device=FakeDevice(flash_fail=1))
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["err"]["kind"] == "burn" and "stopped responding" in j["err"]["error"]
    assert run_to_try(c, "flash")["status"] == "done"


def test_try_got_nothing_then_retry(setup):
    c, r, dev, *_ = setup(device=FakeDevice(read_fail=1))
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["err"]["kind"] == "try"
    assert dev.read_secs >= 120  # 等得够久：设备烧完会重启、连网，人还要做一次要测的事
    assert "再试一次" in j["err"]["message"] and "做一次要测的事" in j["err"]["message"]  # 一句话说清怎么重来
    assert len(j["err"]["message"]) < 60 and j["err"]["why"]  # 原因放进"详情"，不堆在提示里
    c.post("/api/fsw/run", json={"from": "try"})
    assert wait(c, lambda j: j["status"] in ("failed", "done"))["status"] == "done"


def test_flash_then_tries_right_away_and_watches_the_device_boot(setup):
    """决策 107：烧完不再停下等人判断"准备好了"：直接开始听，设备一有输出就记下（页面从"设备正在重启"换成"现在做一次"）。"""
    c, r, dev, *_ = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["status"] == "done" and j["phase"] == "try" and j["dev_seen"] and j["try_started"]


def test_restore_puts_source_back_byte_for_byte_and_flashes_original(setup):
    c, r, dev, _, _, src = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    j = c.post("/api/fsw/restore").json()
    assert j["same"] and j["flashed"] and dev.flashed[-1].endswith("firmware/orig")
    assert ins.compare(r["work"], src)["same"]


def test_bad_plan_changes_nothing(setup):
    c, r, _, _, _, src = setup(llm_reply="没给清单")
    start_insert(c, r)
    j = wait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "failed" and j["err"]["kind"] == "insert" and "一个文件都没改" in j["err"]["message"]
    assert ins.compare(r["work"], src)["same"]


def test_insert_starts_original_build_when_missing(setup):
    """码表重开过、后台编原版的记录没了：插之前自己把原版再编一次、存好。"""
    c, r, dev, _, builds, src = setup(start_bg=False)
    start_insert(c, r)
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"
    assert len(builds) == 1 and (Path(r["project_dir"]) / "firmware/orig/flasher_args.json").exists()


def test_restore_while_waiting_for_device_cancels_flashing(setup):
    c, r, dev, ports, _, src = setup(ports=())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    wait(c, lambda j: j["status"] == "waiting_device")
    j = c.post("/api/fsw/restore").json()
    assert j["same"] and not j["flashed"]
    ports.append("/dev/cu.usbmodem1101")  # 还原以后才插上：不能再把计时版烧进去
    time.sleep(0.2)
    assert not any(f.endswith("firmware/fsw") for f in dev.flashed)
    assert c.get("/api/fsw").json()["status"] == "restored"


class ToolBrokenDevice(FakeDevice):
    def flash(self, fw, port, on_progress):
        return {"ok": False, "cause": "tool", "message": "乐鑫编译工具没加载起来，不是源码的问题。", "error": "export.sh 没跑通"}


def test_burn_with_compiler_not_loading_is_not_called_unplugged(setup):
    c, r, *_ = setup(device=ToolBrokenDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["err"]["kind"] == "burn" and j["err"]["cause"] == "tool" and "不是源码的问题" in j["err"]["message"]


class SlowSecondReadDevice(FakeDevice):
    """第一次试一次没收到；再试一次时设备一直开着，过一会儿才有输出。"""
    def read(self, port, seconds, on_line, stop=None):
        if self.read_fail:
            return super().read(port, seconds, on_line, stop)
        time.sleep(0.5)
        return super().read(port, seconds, on_line, stop)


def test_retry_try_does_not_say_device_is_rebooting(setup):
    c, r, dev, *_ = setup(device=SlowSecondReadDevice(read_fail=1))
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["err"]["kind"] == "try"
    c.post("/api/fsw/run", json={"from": "try"})  # 没重新烧录：设备不会重启
    j = wait(c, lambda j: j["status"] == "running" and j.get("phase") == "try")
    assert j["dev_seen"]  # 页面不再显示"设备刚烧完，正在重启"
    assert wait(c, lambda j: j["status"] in ("failed", "done"))["status"] == "done"
