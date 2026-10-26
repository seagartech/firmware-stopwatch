"""烧录失败按阶段说（10-26 8783 真测：esptool 报 "Failed to connect to ESP32-S3: No serial data received."，
连都没连上、什么都没写，页面却说"写到一半断了、设备上是写了一半的固件"）。

- 没连上（连接阶段）：自动再试（同样的复位方式一次、换 USB 复位一次），还不行才报，说清楚什么都没写、设备没动、怎么进下载模式；
- 已经开始写之后断了：才说可能写了一半，不自动重试；
- 别的程序开着调试口：烧之前查出来，点名是谁，不硬烧；
- 每次尝试的完整输出存进项目文件夹的日志；timeline.json 记烧录结束时间和结果。
不碰真设备、不真调乐鑫工具。
"""

import json

from fsw.platforms.espidf import EspIdf, download_mode_tip, flash_stage, port_holders
from tests.test_espidf import built
from tests.test_fsw_api import FakeDevice, run_to_try, setup, start_insert, wait  # noqa: F401  (setup 是 fixture)

CONNECT_FAIL = ["esptool v5.5.0", "Serial port /dev/cu.usbmodem2101:", "Connecting......................................",
                "A fatal error occurred: Failed to connect to ESP32-S3: No serial data received.",
                "For troubleshooting steps visit: https://docs.espressif.com/projects/esptool/en/latest/troubleshooting.html"]
MID_WRITE = ["Connected to ESP32-S3 on /dev/cu.usbmodem2101:", "Flash will be erased from 0x00000000 to 0x00005fff...",
             "Writing at 0x00020000 [====>     ] 30.0% 49152/101998 bytes...",
             "A fatal error occurred: The chip stopped responding."]
OK = ["Connected to ESP32-S3 on /dev/cu.usbmodem2101:", "Writing at 0x00020000 [==========] 100.0% 101998/101998 bytes...",
      "Hard resetting via RTS pin..."]


def seq_runner(results, calls):
    """每次调用按顺序给一段输出和退出码。"""
    it = iter(results)

    def run(cmd, cwd, env):
        calls.append(cmd)
        lines, code = next(it)
        yield from lines
        return code
    return run


def test_stage_of_esptool_failures():
    assert flash_stage(CONNECT_FAIL) == ("connect", False)
    assert flash_stage(["A fatal error occurred: Failed to connect to ESP32: Wrong boot mode detected (0x13)!"])[0] == "connect"
    assert flash_stage(["A fatal error occurred: Timed out waiting for packet header"])[0] == "connect"
    assert flash_stage(MID_WRITE) == ("write", True)
    assert flash_stage(["A fatal error occurred: Could not open /dev/cu.usbmodem2101, the port is busy or doesn't exist.",
                        "([Errno 16] could not open port /dev/cu.usbmodem2101: [Errno 16] Resource busy)"]) == ("busy", False)
    assert flash_stage(["A fatal error occurred: This chip is ESP32-S3, not ESP32-C3. Wrong chip argument?"])[0] == "chip"


def test_connect_failure_is_retried_with_another_reset_and_logged(tmp_path):
    src = built(tmp_path)
    EspIdf.save_firmware(src, tmp_path / "fw")
    calls, waits, tries = [], [], []
    idf = EspIdf(tmp_path / "idf", runner=seq_runner([(CONNECT_FAIL, 2), (CONNECT_FAIL, 2), (OK, 0)], calls), env={},
                 sleep=waits.append)
    log = tmp_path / "proj/flash-logs/x-fsw.log"
    r = idf.flash_saved(tmp_path / "fw", "/dev/cu.usbmodem2101", log=log, on_attempt=lambda n, b: tries.append((n, b)))
    assert r["ok"] and len(calls) == 3 and waits == [EspIdf.RETRY_WAIT] * 2
    befores = [c[c.index("--before") + 1] for c in calls]
    assert befores == ["default-reset", "default-reset", "usb-reset"] and tries == list(zip([1, 2, 3], befores))
    text = log.read_text(encoding="utf-8")
    assert text.count("===== 第") >= 6 and "No serial data received" in text and "Connecting....." in text  # 原话全留着
    assert [a["stage"] for a in r["attempts"]] == ["connect", "connect", None]


def test_connect_failure_gives_up_after_retries_and_says_nothing_written(tmp_path):
    src = built(tmp_path)
    EspIdf.save_firmware(src, tmp_path / "fw")
    calls = []
    idf = EspIdf(tmp_path / "idf", runner=seq_runner([(CONNECT_FAIL, 2)] * 5, calls), env={}, sleep=lambda s: None)
    r = idf.flash_saved(tmp_path / "fw", "/dev/cu.usbmodem2101", log=tmp_path / "l.log")
    assert not r["ok"] and r["stage"] == "connect" and r["written"] is False and len(calls) == 1 + EspIdf.CONNECT_RETRIES
    assert "No serial data received" in r["error"] and r["log"].endswith("l.log")


def test_mid_write_failure_is_not_retried(tmp_path):
    src = built(tmp_path)
    EspIdf.save_firmware(src, tmp_path / "fw")
    calls = []
    idf = EspIdf(tmp_path / "idf", runner=seq_runner([(MID_WRITE, 2), (OK, 0)], calls), env={}, sleep=lambda s: None)
    r = idf.flash_saved(tmp_path / "fw", "/dev/cu.usbmodem2101")
    assert not r["ok"] and r["stage"] == "write" and r["written"] and len(calls) == 1


def test_old_esptool_spelling_uses_underscore_usb_reset(tmp_path):
    src = built(tmp_path)
    args = json.loads((src / "build/flasher_args.json").read_text())
    args["extra_esptool_args"]["before"] = "default_reset"
    (src / "build/flasher_args.json").write_text(json.dumps(args))
    EspIdf.save_firmware(src, tmp_path / "fw")
    calls = []
    idf = EspIdf(tmp_path / "idf", runner=seq_runner([(CONNECT_FAIL, 2)] * 3, calls), env={}, sleep=lambda s: None)
    idf.flash_saved(tmp_path / "fw", "/dev/x")
    assert [c[c.index("--before") + 1] for c in calls] == ["default_reset", "default_reset", "usb_reset"]


def test_port_holders_names_other_programs_not_ourselves():
    out = "p111\ncscreen\np222\ncPython\n"
    assert port_holders("/dev/x", own_pid=222, runner=lambda p: out) == [{"pid": 111, "name": "screen"}]
    assert port_holders("/dev/x", own_pid=1, runner=lambda p: "") == []

    def broken(p):
        raise OSError("no lsof")
    assert port_holders("/dev/x", own_pid=1, runner=broken) == []


def test_download_mode_tip_is_generic_unless_board_known():
    g = download_mode_tip(None)
    assert "BOOT" in g and "CoreS3" not in g
    assert "CoreS3" in download_mode_tip("m5stack/core-s3") and "绿灯" in download_mode_tip("m5stack/core-s3")
    assert "CoreS3" not in download_mode_tip("esp-box-3")


class ConnectFailDevice(FakeDevice):
    def flash(self, fw, port, on_progress, log=None, on_attempt=lambda n, b: None):
        self.log = log
        for n, b in ((1, "default-reset"), (2, "default-reset"), (3, "usb-reset")):
            on_attempt(n, b)
        from pathlib import Path
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text("\n".join(CONNECT_FAIL), encoding="utf-8")
        return {"ok": False, "stage": "connect", "written": False, "log": str(log), "seconds": 9.0,
                "error": CONNECT_FAIL[3],
                "attempts": [{"n": n, "before": b, "ok": False, "stage": "connect", "seconds": 3.0}
                             for n, b in ((1, "default-reset"), (2, "default-reset"), (3, "usb-reset"))]}


def test_page_says_nothing_written_and_timeline_records_flash_end(setup):
    c, r, dev, *_ = setup(device=ConnectFailDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    e = j["err"]
    assert e["kind"] == "burn" and e["stage"] == "connect" and e["written"] is False
    assert "什么都没写" in e["title"] and "设备里原来的东西没动" in e["message"] and "写了一半" not in e["message"]
    assert "下载模式" in e["message"] and "重新烧录" in e["message"]
    assert "第 3 次（复位方式 usb-reset）：没连上" in e["summary"] and e["log"].endswith("-fsw.log")
    from pathlib import Path
    assert Path(e["log"]).is_file() and Path(e["log"]).parent.name == "flash-logs"
    assert Path(e["log"]).parent.parent == Path(r["project_dir"])
    tl = json.loads((Path(r["project_dir"]) / "timeline.json").read_text(encoding="utf-8"))["stages"]["flash"]
    assert tl["end"] and tl["result"] == "failed" and tl["stage"] == "connect" and tl["attempts"] == 3


class MidWriteDevice(FakeDevice):
    def flash(self, fw, port, on_progress):  # 老接口（不收日志参数）照样能用
        return {"ok": False, "stage": "write", "written": True, "error": MID_WRITE[-1]}


def test_mid_write_failure_still_says_maybe_half_written(setup):
    c, r, *_ = setup(device=MidWriteDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    e = run_to_try(c)["err"]
    assert e["stage"] == "write" and "写了一半" in e["message"] and "写到一半" in e["title"]


class BusyPortDevice(FakeDevice):
    def port_holders(self, port):
        return [{"pid": 4321, "name": "screen"}]


def test_port_held_by_another_program_is_named_and_not_flashed(setup):
    c, r, dev, *_ = setup(device=BusyPortDevice())
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    e = run_to_try(c)["err"]
    assert e["stage"] == "busy" and "screen（进程号 4321）" in e["message"] and "什么都没写" in e["title"]
    assert not any(f.endswith("firmware/fsw") for f in dev.flashed)


def test_success_records_flash_result_in_timeline(setup):
    c, r, *_ = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    from pathlib import Path
    tl = json.loads((Path(r["project_dir"]) / "timeline.json").read_text(encoding="utf-8"))["stages"]["flash"]
    assert tl["result"] == "ok" and tl["end"] >= tl["start"]


def _page_err(err):
    """用 node 跑 app.js 里的 flErrReal()：烧录失败那一块页面上写了什么。"""
    import shutil
    import subprocess
    import pytest
    if not shutil.which("node"):
        pytest.skip("没有 node")
    from tests.test_timeline_tip import grab
    stubs = ("const CC=()=>({}),FF={},scene='x',fullLogBtn=()=>'',fullLog=()=>'',TOOLT=x=>x,toolSay=x=>x,REAL={};"
             f"const FSWJ={json.dumps({'err': err}, ensure_ascii=False)};")
    code = stubs + grab("htmlEsc") + "\n" + grab("flErrReal") + "\nconsole.log(flErrReal());"
    return subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout


def test_page_box_uses_stage_title_and_shows_attempt_summary():
    html = _page_err({"kind": "burn", "stage": "connect", "written": False, "title": "烧录没连上设备：什么都没写进去",
                      "message": "设备里原来的东西没动", "summary": "第 1 次（复位方式 default-reset）：没连上，用了 3.0 秒",
                      "log": "/p/flash-logs/x-fsw.log", "error": "Failed to connect"})
    assert "什么都没写进去" in html and "写了一半" not in html and "设备断开了" not in html
    assert "烧录记录：第 1 次" in html and "x-fsw.log" in html and 'data-flx="reburn"' in html
    old = _page_err({"kind": "burn", "message": "烧录中断", "error": "Failed to connect"})  # 老存档没有阶段：不再一口咬定写了一半
    assert "写了一半" not in old and "设备断开了" not in old
