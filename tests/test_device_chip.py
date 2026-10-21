"""10-21 用户定：选错板子要早点、用大白话说出来。

插着设备时读一次芯片（设备会重启一下），记下来；芯片对不上的板子变灰、不让选。
没读到就照旧，烧录时乐鑫工具报"芯片不对"，码表翻成"板子选错了"。
"""

import json
from pathlib import Path

from fsw.platforms.espidf import EspIdf, chip_in, chip_name, port_key, wrong_chip
from tests.test_fsw_api import FakeDevice, run_to_try, setup, start_insert, wait  # noqa: F401  (setup 是 fixture)

JS = (Path(__file__).parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")


def test_chip_names_from_esptool_output():
    assert chip_in(["Connecting....", "Connected to ESP32-S3 on /dev/cu.usbmodem2101:"]) == "ESP32-S3"   # esptool 5
    assert chip_in(["Chip is ESP32-C3 (QFN32) (revision v0.4)"]) == "ESP32-C3"                           # esptool 4
    assert chip_in(["Detecting chip type... ESP32"]) == "ESP32"
    assert chip_in(["A fatal error occurred: Failed to connect to Espressif device: No serial data received."]) is None
    assert chip_name("esp32s3") == chip_name("ESP32-S3") == chip_name("ESP32S3") == "ESP32-S3"


def test_wrong_chip_from_esptool_output():
    out = ["A fatal error occurred: This chip is ESP32-S3, not ESP32-C3. Wrong chip argument?"]
    assert wrong_chip(out) == ("ESP32-S3", "ESP32-C3")
    assert wrong_chip(["A fatal error occurred: The chip stopped responding."]) is None


def test_port_key_uses_usb_serial_number():
    ports = lambda: [{"device": "/dev/cu.usbmodem2101", "vid": 0x303A, "serial": "F4:12:FA:00:11:22"},
                     {"device": "/dev/cu.usbserial-1", "vid": 0x1A86, "serial": None}]
    assert port_key("/dev/cu.usbmodem2101", ports) == "12346:F4:12:FA:00:11:22"
    assert port_key("/dev/cu.usbserial-1", ports) == "/dev/cu.usbserial-1"   # 没序列号：按口的名字


class ChipDevice(FakeDevice):
    def __init__(self, chip="ESP32-S3", **kw):
        super().__init__(**kw)
        self.chip, self.reads = chip, 0

    def read_chip(self, port):
        self.reads += 1
        return {"chip": self.chip} if self.chip else {"chip": None, "error": "Failed to connect"}


def test_read_chip_once_and_remember_it(setup, tmp_path):
    dev = ChipDevice("esp32s3")
    c, *_ = setup(device=dev)
    assert c.get("/api/device/chip").json() == {"port": "/dev/cu.usbmodem1101", "chip": None}   # 只问记下的，不碰设备
    assert dev.reads == 0
    assert c.post("/api/device/chip").json()["chip"] == "ESP32-S3"
    assert c.post("/api/device/chip").json()["chip"] == "ESP32-S3" and dev.reads == 1        # 记下了，不再读（不再让设备重启）
    assert c.get("/api/device/chip").json()["chip"] == "ESP32-S3"
    assert json.loads((tmp_path / "data" / "devices.json").read_text())["/dev/cu.usbmodem1101"] == "ESP32-S3"


def test_read_chip_fails_quietly(setup):
    dev = ChipDevice(None)
    c, *_ = setup(device=dev)
    r = c.post("/api/device/chip").json()
    assert r["chip"] is None and "Failed" in r["error"]   # 读不到就不筛，照旧能选


def test_no_chip_read_while_the_stopwatch_holds_the_port(setup):
    dev = ChipDevice()
    c, *_ = setup(device=dev)
    fake = object()
    EspIdf._OPEN[fake] = "test"
    try:
        assert c.post("/api/device/chip").json()["busy"] is True and dev.reads == 0   # 读芯片会让设备重启：测着的时候不读
    finally:
        EspIdf._OPEN.pop(fake, None)


def test_flash_wrong_chip_says_board_is_wrong(setup):
    class WrongChip(ChipDevice):
        def flash(self, fw, port, on_progress):
            self.flashed.append(str(fw))
            return {"ok": False, "error": "A fatal error occurred: This chip is ESP32-C3, not ESP32-S3. Wrong chip argument?",
                    "wrong_chip": {"device": "ESP32-C3", "firmware": "ESP32-S3"}}
    dev = WrongChip()
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and j["err"]["kind"] == "burn"
    assert j["err"]["wrong_chip"] == {"device": "ESP32-C3", "firmware": "ESP32-S3"}
    assert "板子选错了" in j["err"]["message"] and "ESP32-C3" in j["err"]["message"]
    assert c.get("/api/device/chip").json()["chip"] == "ESP32-C3"     # 顺便记下：回首页选板子时直接按它筛
    assert "if(E.wrong_chip)" in JS and "回首页换板子" in JS


def test_successful_flash_remembers_the_chip(setup):
    dev = ChipDevice()
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    assert c.get("/api/device/chip").json()["chip"] == "ESP32-S3" and dev.reads == 0   # 按固件的芯片记，不用另外读


def test_home_greys_out_boards_with_another_chip():
    assert "const chipOff=b=>" in JS and "aria-disabled=\"true\"" in JS and "不能选" in JS
    assert "const list=boardOk(hBoardQ)" in JS                      # 上下键、回车只在能选的里面走
    assert "换成 ${DEVCHIP.chip} 的板子" in JS                # 已选的板子对不上：开始测量点不了，写明还差什么
    assert "正在读设备上的芯片，设备会重启一下" in JS
