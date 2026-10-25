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


class WakeThenSpeakDevice(FakeDevice):
    """试一次：先只喊唤醒词（这一遍走不到"听到你停下"那一步），隔一会儿再说一句话，那一步才出现。"""
    def read(self, port, seconds, on_line, stop=None):
        first = ["boot ok", "FSW,RESET,1,0", "FSW,P2,20,0", "FSW,P5,30,0"]
        second = ["FSW,RESET,100,0", "FSW,P1,110,1", "FSW,P2,120,0", "FSW,P5,130,0"]
        out = []
        for l in first:
            on_line(l)
            out.append(l)
        time.sleep(0.6)  # 人还没开口：几秒没有新的计时点
        if stop and stop():
            return out
        for l in second:
            on_line(l)
            out.append(l)
        return out


def test_try_keeps_listening_when_wake_alone_misses_a_step(setup, monkeypatch):
    import fsw.web.app as appmod
    monkeypatch.setattr(appmod, "TRY_SETTLE", 0.1, raising=False)  # 以前：差一个、0.1 秒没新的就判失败
    c, r, *_ = setup(device=WakeThenSpeakDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j.get("phase") == "try" and j.get("missing"))  # 唤醒那一遍完了：页面说这一轮还差哪一步，接着听
    assert j["status"] == "running" and len(j["missing"]) == 1
    j = wait(c, lambda j: j["status"] in ("done", "failed"))
    assert j["status"] == "done", j.get("err")


class SplitAcrossRoundsDevice(FakeDevice):
    """每一轮都缺一个点、合起来才齐：不算试通（多轮实测每一轮都要全部收到）。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,RESET,100,0", "FSW,P1,110,1", "FSW,P5,130,0"]
        for l in out:
            on_line(l)
        return out


def test_try_needs_one_complete_round_not_points_from_different_rounds(setup):
    c, r, *_ = setup(device=SplitAcrossRoundsDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and j["err"]["kind"] == "try"


class WakeRoundNoStartDevice(FakeDevice):
    """10-18 真机：喊唤醒词那一轮没有起点（"设备听出你说完"），其余都齐；人接着说的那一轮起点有了，回答还没放完。
    10-25 起：起点之前的点不算（唤醒提示音会走回复那几个点），没有一轮起点之后收齐，不算试通。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,RESET,1,0", "FSW,P2,20,0", "FSW,P5,30,0", "FSW,RESET,100,0", "FSW,P1,110,1"]
        for l in out:
            on_line(l)
            if stop and stop():
                break
        return out


def test_try_wake_round_without_start_point_does_not_pass(setup):
    c, r, *_ = setup(device=WakeRoundNoStartDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and j["err"]["kind"] == "try"  # 唤醒那一轮没有起点，不算；说的那一轮还没收齐


class WakeOnlyDevice(FakeDevice):
    """10-19 真机：只喊了唤醒词，起点（"设备听出你说完"）一次都没出现过，其余都齐。
    10-25 起不算试通：说清楚是没收到起点，让人完整做一次。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,RESET,1,0", "FSW,P2,20,0", "FSW,P5,30,0", "FSW,RESET,100,0"]
        for l in out:
            on_line(l)
            if stop and stop():
                break
        return out


def test_try_wake_only_without_any_start_point_says_no_start(setup):
    c, r, *_ = setup(device=WakeOnlyDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and "起点" in j["err"]["message"] and "再试一次" in j["err"]["message"]


def test_new_run_stops_the_old_try_and_frees_the_port(setup):
    """10-17 真机：试一次还在听调试口时又点了编译烧录，旧的那趟一直占着口，烧录报"multiple access on port"。
    新的一趟开始，旧的试一次马上停下、让出调试口，也不再改页面状态。"""
    import threading

    class Holding(FakeDevice):
        def __init__(self):
            super().__init__()
            self.open, self.first_left = 0, threading.Event()
            self.calls = 0

        def read(self, port, seconds, on_line, stop=None):
            self.calls += 1
            if self.calls == 1:  # 第一趟：设备不出声，一直占着口，直到被叫停
                self.open += 1
                t = time.time()
                while time.time() - t < 5 and not (stop and stop()):
                    time.sleep(0.01)
                self.open -= 1
                self.first_left.set()
                return []
            return super().read(port, seconds, on_line, stop)
    dev = Holding()
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    wait(c, lambda j: j["phase"] == "try" and dev.open == 1)
    start_insert(c, r)                                                           # 另一个页面里又插了一次（新的一趟）
    assert dev.first_left.wait(2), "旧的试一次没让出调试口"


def test_stop_while_waiting_device_does_not_flash(setup):
    """测量页「停止测量」：等设备时停下，插上设备也不再烧录。"""
    c, r, dev, ports, *_ = setup(ports=())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    wait(c, lambda j: j["status"] == "waiting_device")
    assert c.post("/api/fsw/stop").json()["status"] == "stopped"
    ports.append("/dev/cu.usbmodem1101")
    time.sleep(0.3)
    j = c.get("/api/fsw").json()
    assert j["status"] == "stopped" and not dev.flashed


def test_try_reports_how_many_points_it_needs(setup):
    """页面上"已收到 x / 共几个"和后台判通过用同一个数。"""
    c, r, *_ = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["need"] == 3


class LateLastSoundDevice(FakeDevice):
    """亲测：唤醒后那一轮，最后一声比结束标记晚 0.08 秒才到。试测要算它收齐，不用人再说一遍。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,P5,6119818,18", "FSW,RESET,9921421,0", "FSW,P1,10000000,1", "FSW,P2,11000000,0",
               "FSW,RESET,13607192,0", "FSW,P5,13689834,3"]
        for l in out:
            on_line(l)
        return out


def test_try_counts_the_last_sound_that_ends_just_after_the_round_marker(setup):
    c, r, *_ = setup(device=LateLastSoundDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "done" and j["got"] == [1, 2, 5]


class SpokeThenStream(FakeDevice):
    """试通那一刻这一轮还在进行（起点已到）：多轮实测接着收这一轮后面的点。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,P5,30,0"]
        for l in out:
            on_line(l)
        return out

    def stream(self, port, on_line, stop):
        for l in ["FSW,RESET,100,0", "FSW,P1,110,1", "FSW,P2,150,0", "FSW,P5,160,0", "FSW,RESET,300,0",
                  "FSW,P1,310,1", "FSW,P2,350,0", "FSW,P5,360,0", "FSW,RESET,500,0"]:
            if stop():
                return
            on_line(l)
        while not stop():
            time.sleep(0.01)


class WakeThenStream(WakeRoundNoStartDevice):
    """试一次时先喊唤醒词（没有起点，不算），再说一句（起点之后收齐，读到结束标记才停）：
    唤醒那一轮不算一轮、也不报开头没收到；说的那一句交给多轮实测算第 1 轮。"""
    def read(self, port, seconds, on_line, stop=None):
        out = []
        for l in ["FSW,RESET,1,0", "FSW,P2,20,0", "FSW,P5,30,0", "FSW,RESET,100,0", "FSW,P1,110,1", "FSW,P2,120,0",
                  "FSW,P5,130,0", "FSW,RESET,140,0", "FSW,P2,150,0"]:
            if stop and stop():
                break
            on_line(l)
            out.append(l)
        return out

    def stream(self, port, on_line, stop):
        for l in ["FSW,RESET,300,0", "FSW,P1,310,1", "FSW,P2,350,0", "FSW,P5,360,0", "FSW,RESET,500,0",
                  "FSW,P1,510,1", "FSW,P2,550,0", "FSW,P5,560,0", "FSW,RESET,700,0",
                  "FSW,P1,710,1", "FSW,P2,750,0", "FSW,P5,760,0", "FSW,RESET,900,0"]:
            if stop():
                return
            on_line(l)
        while not stop():
            time.sleep(0.01)


def _collect_after_try(setup, dev, n):
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    page = {"steps": [{"p": s["p"], "n": s["n"], "line": s["line"]} for s in STEPS],
            "segs": [{"a": "P1", "b": "P2", "t": "读卡号"}, {"a": "P2", "b": "P5", "t": "开锁"}], "fsegs": [], "ln": {"talk": "开锁这条"}}
    res = c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": n})
    assert res.status_code == 200, res.text
    t = time.time()
    while time.time() - t < 10:
        j = c.get("/api/collect").json()
        if j["status"] in ("done", "stopped"):
            break
        time.sleep(0.02)
    return j


def test_round_running_when_try_passes_counts_in_measurement(setup):
    # 真测第 29 次：唤醒后说的那句，试通时这一轮正在进行，转进多轮实测，这句不能被算"开头没收到"白说了
    j = _collect_after_try(setup, SpokeThenStream(), 3)
    assert j["status"] == "done" and not j.get("half")
    assert [x["ok"] for x in j["rounds"]] == [True, True, True]


def test_wake_round_after_try_is_skipped_silently(setup):
    # 10-19：喊完唤醒词就试通，唤醒回应剩下的点不算一轮，不报开头没收到、也不算没记到起点
    j = _collect_after_try(setup, WakeThenStream(), 3)
    assert j["status"] == "done" and not j.get("half") and not j.get("nostart")
    assert [x["ok"] for x in j["rounds"]] == [True, True, True]
    assert round(j["rounds"][0]["segs"]["P1-P2"] * 1e6) == 10                     # 第 1 轮是试一次里说的那句（110 → 120）


class TrialDoneThenQuiet(FakeDevice):
    """试一次那一轮收全（含"取最后一次"的最后一声），结束标记在换调试口时过去了；正式实测开头设备一会儿没输出。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,P5,30,0"]
        for l in out:
            on_line(l)
        return out

    def stream(self, port, on_line, stop):
        t = time.time()
        while time.time() - t < 1.5 and not stop():
            time.sleep(0.01)
        for l in ["FSW,P1,310,1", "FSW,P2,350,0", "FSW,P5,360,0", "FSW,RESET,500,0",
                  "FSW,P1,510,1", "FSW,P2,550,0", "FSW,P5,560,0", "FSW,RESET,700,0"]:
            if stop():
                return
            on_line(l)
        while not stop():
            time.sleep(0.01)


def test_carried_round_turns_green_as_soon_as_measuring_starts(setup):
    # 10-19 用户："说完今天星期几格子不变色，说完恐龙一下子绿两个"：试一次带过来的那一轮已经收全，开测就算第 1 轮
    c, r, *_ = setup(device=TrialDoneThenQuiet())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    steps = [{"p": s["p"], "n": s["n"], "line": s["line"], **({"once": "last"} if s["p"] == "P5" else {})} for s in STEPS]
    page = {"steps": steps, "segs": [{"a": "P1", "b": "P2", "t": "读卡号"}, {"a": "P2", "b": "P5", "t": "开锁"}], "fsegs": [], "ln": {"talk": "开锁这条"}}
    res = c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3})
    assert res.status_code == 200, res.text
    time.sleep(0.5)  # 设备还没新输出
    j = c.get("/api/collect").json()
    assert [x["ok"] for x in j["rounds"]] == [True], j
    t = time.time()
    while time.time() - t < 10 and j["status"] not in ("done", "stopped"):
        time.sleep(0.05)
        j = c.get("/api/collect").json()
    assert j["status"] == "done" and [x["ok"] for x in j["rounds"]] == [True, True, True]


class StreamingDevice(FakeDevice):
    """多轮实测读调试口时一直开着（直到让它停）；烧录时记下调试口是不是还被读着。"""

    def __init__(self):
        super().__init__()
        self.reading, self.busy_at_flash = False, None

    def stream(self, port, on_line, stop):
        self.reading = True
        try:
            while not stop():
                time.sleep(0.01)
        finally:
            self.reading = False

    def flash(self, fw, port, on_progress):
        self.busy_at_flash = self.reading
        return super().flash(fw, port, on_progress)


def test_running_measurement_is_stopped_before_insert_and_flash(setup):
    """10-20 真测：多轮实测还开着调试口，又点了"改完了，再测一次"，烧录和它抢同一个口失败、设备停在等烧录。
    开始插点 / 编译烧录前，先停掉还在跑的多轮实测、让出调试口；已收的轮照常留着。"""
    dev = StreamingDevice()
    c, r, *_ = setup(device=dev)
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    t = time.time()
    while not dev.reading and time.time() - t < 5:
        time.sleep(0.02)
    assert dev.reading and c.get("/api/collect").json()["status"] in ("running", "waiting_device")
    start_insert(c, r)
    assert c.get("/api/collect").json()["status"] == "stopped" and not dev.reading   # 先停、调试口放开
    wait(c, lambda j: j["status"] in ("inserted", "failed"))
    run_to_try(c)
    assert dev.busy_at_flash is False                                               # 烧录时没人在读调试口


def test_insert_without_measurement_behaves_as_before(setup):
    c, r, *_ = setup()
    assert c.get("/api/collect").json()["status"] == "idle"
    start_insert(c, r)
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"


class InterruptedInTry(FakeDevice):
    """试一次时唤醒后说的第一句被打断：这一轮（带起点、收全）在试一次里就结束了（结束标记也读到了）。"""
    def read(self, port, seconds, on_line, stop=None):
        out = ["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,P5,30,0", "FSW,RESET,40,0"]
        for l in out:
            on_line(l)
        return out

    def stream(self, port, on_line, stop):
        for l in ["FSW,P1,310,1", "FSW,P2,350,0", "FSW,P5,360,0", "FSW,RESET,500,0",
                  "FSW,P1,510,1", "FSW,P2,550,0", "FSW,P5,560,0", "FSW,RESET,700,0",
                  "FSW,P1,710,1", "FSW,P2,750,0", "FSW,P5,760,0", "FSW,RESET,900,0"]:
            if stop():
                return
            on_line(l)
        while not stop():
            time.sleep(0.01)


def test_question_finished_inside_try_counts_as_round_1(setup):
    # 10-20 用户：唤醒后第一句一打断，这一轮在试一次里就收完了，不算、得多说一句。现在交给多轮实测算第 1 轮
    j = _collect_after_try(setup, InterruptedInTry(), 3)
    assert j["status"] == "done" and [x["ok"] for x in j["rounds"]] == [True, True, True]
    assert round(j["rounds"][0]["segs"]["P1-P2"] * 1e6) == 10                     # 第 1 轮就是试一次里那句（10 → 20）


def test_round_finished_inside_try_too_long_ago_is_not_carried(setup, monkeypatch):
    import fsw.web.app as web
    monkeypatch.setattr(web, "CARRY_SECS", -1)                                     # 试通前太久收完的那一轮：不带
    j = _collect_after_try(setup, InterruptedInTry(), 3)
    assert j["status"] == "done" and round(j["rounds"][0]["segs"]["P1-P2"] * 1e6) == 40   # 第 1 轮是开测后的第一句


class BeepBeforeStartThenQuestion(FakeDevice):
    """10-25 真机：唤醒提示音在起点之前走了回复那几个点（P5），然后起点、P2；这一轮回复的 P5 在结束时才补打（LATE），
    紧跟着结束标记。试一次：起点之前那次不算；读到结束标记才停，补打的那行一起交给多轮实测。"""
    def __init__(self):
        super().__init__()
        self.after_stop = 0

    def read(self, port, seconds, on_line, stop=None):
        out = []
        for l in ["FSW,RESET,1,0", "FSW,P5,5,1", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,LATE,P5,80,3", "FSW,RESET,90,0", "boot ok"]:
            if stop and stop():
                self.after_stop += 1
                break
            on_line(l)
            out.append(l)
        return out

    def stream(self, port, on_line, stop):
        while not stop():
            time.sleep(0.01)


def test_try_ignores_points_before_start_and_reads_to_round_end(setup):
    dev = BeepBeforeStartThenQuestion()
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "done" and j["got"] == [1, 2, 5]
    assert dev.after_stop == 1  # 停在结束标记之后，不是收全（LATE,P5）那一下
    steps = [{"p": s["p"], "n": s["n"], "line": s["line"], **({"once": "last"} if s["p"] == "P5" else {})} for s in STEPS]
    page = {"steps": steps, "segs": [{"a": "P1", "b": "P2", "t": "读卡号"}, {"a": "P2", "b": "P5", "t": "开锁"}], "fsegs": [], "ln": {"talk": "开锁这条"}}
    assert c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3}).status_code == 200
    t = time.time()
    while time.time() - t < 5:
        j = c.get("/api/collect").json()
        if j["rounds"]:
            break
        time.sleep(0.02)
    assert j["rounds"][0]["ok"] and round(j["rounds"][0]["segs"]["P2-P5"] * 1e6) == 60   # 20 → 80（补打的那次），不是起点之前的 5
    c.post("/api/collect/stop")


def test_try_rejects_a_round_whose_points_all_came_before_start(setup):
    class Dev(FakeDevice):
        def read(self, port, seconds, on_line, stop=None):
            out = ["FSW,RESET,1,0", "FSW,P2,5,0", "FSW,P5,6,0", "FSW,P1,10,1", "FSW,RESET,90,0"]
            for l in out:
                on_line(l)
            return out
    c, r, *_ = setup(device=Dev())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "failed"
