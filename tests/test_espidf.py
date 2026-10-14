import json
from pathlib import Path

import pytest

from fsw.platforms import get_platform
from fsw.platforms.espidf import (EspIdf, find_idf, find_ports, flash_progress, ninja_progress,
                                  pick_idf, read_lines, version_tuple)
from tests.test_inspect import CMAKE, write


def idf_install(root, major, minor, patch=0):
    write(root, {"tools/cmake/version.cmake":
                 f"set(IDF_VERSION_MAJOR {major})\nset(IDF_VERSION_MINOR {minor})\nset(IDF_VERSION_PATCH {patch})\n",
                 "tools/idf.py": "", "export.sh": ""})
    return root


def test_platform_registry_has_espidf_and_leaves_room_for_others():
    assert get_platform("ESP-IDF") is EspIdf
    with pytest.raises(KeyError):
        get_platform("STM32")


def test_finds_installed_idf_and_reads_version(tmp_path):
    idf_install(tmp_path / "esp/esp-idf-v6.1", 6, 1)
    idf_install(tmp_path / "esp/esp-idf", 5, 3)
    found = find_idf(home=tmp_path, env={})
    assert sorted((f["version"], Path(f["path"]).name) for f in found) == [("5.3.0", "esp-idf"), ("6.1.0", "esp-idf-v6.1")]


def test_idf_path_environment_variable_is_used(tmp_path):
    idf_install(tmp_path / "custom", 6, 0, 1)
    found = find_idf(home=tmp_path / "nohome", env={"IDF_PATH": str(tmp_path / "custom")})
    assert [f["version"] for f in found] == ["6.0.1"]


def test_espressif_installer_record_is_used(tmp_path):
    idf_install(tmp_path / "elsewhere/idf", 6, 1)
    (tmp_path / ".espressif").mkdir()
    (tmp_path / ".espressif/idf-env.json").write_text(json.dumps(
        {"idfInstalled": {"x": {"version": "6.1", "path": str(tmp_path / "elsewhere/idf")}}}))
    assert [f["version"] for f in find_idf(home=tmp_path, env={})] == ["6.1.0"]


def test_pick_idf_meets_project_requirement(tmp_path):
    installs = [{"path": "a", "version": "5.3.0"}, {"path": "b", "version": "6.1.0"}]
    assert pick_idf(installs, "6.0.1")["path"] == "b"
    assert pick_idf(installs, "6.2") is None
    assert pick_idf(installs, None)["path"] == "b"
    assert version_tuple("6.0.1") < version_tuple("6.1")


def test_ninja_progress_lines():
    assert ninja_progress("[12/1630] Building CXX object esp-idf/main/x.obj") == (12, 1630)
    assert ninja_progress("-- Configuring done") is None


def test_flash_progress_lines():
    assert flash_progress("Writing at 0x0012c000 [====>     ] 48.2% 49152/101998 bytes...") == 48
    assert flash_progress("Writing at 0x00010000... (37 %)") == 37
    assert flash_progress("Hash of data verified.") is None


def fake_runner(lines, code=0, calls=None):
    def run(cmd, cwd, env):
        if calls is not None:
            calls.append(cmd)
        yield from lines
        return code
    return run


def board_project(tmp_path):
    return write(tmp_path / "src", {
        "CMakeLists.txt": CMAKE,
        "sdkconfig.defaults": "", "sdkconfig.defaults.esp32s3": "",
        "main/boards/m5stack/core-s3/config.json": json.dumps({"type": "m5stack-core-s3", "target": "esp32s3",
            "builds": [{"name": "m5stack-core-s3", "sdkconfig_append": ["CONFIG_SPIRAM_MODE_QUAD=y"]}]}),
    })


def test_build_reports_progress_from_0_to_100(tmp_path):
    src = board_project(tmp_path)
    seen, calls = [], []
    out = ["-- Configuring done", "[1/4] Building C object a.obj", "[2/4] Building C object b.obj",
           "[4/4] Linking CXX executable app.elf", "Project build complete."]
    idf = EspIdf(tmp_path / "idf", runner=fake_runner(out, calls=calls), env={"X": "1"})
    r = idf.build(src, target="esp32s3", board="m5stack/core-s3", on_progress=lambda p, msg: seen.append(p))
    assert r["ok"]
    assert seen[0] == 0 and seen[-1] == 100 and seen == sorted(seen)
    frag = (src / "sdkconfig.fsw-board").read_text()
    assert "CONFIG_BOARD_TYPE_M5STACK_CORE_S3=y" in frag and "CONFIG_SPIRAM_MODE_QUAD=y" in frag
    cmd = " ".join(calls[0])
    assert "-DIDF_TARGET=esp32s3" in cmd
    assert "sdkconfig.defaults;sdkconfig.defaults.esp32s3;sdkconfig.fsw-board" in cmd
    assert calls[0][-1] == "build"


def test_build_failure_keeps_the_error_lines(tmp_path):
    src = board_project(tmp_path)
    out = ["[1/4] Building C object a.obj", "../main/application.cc:670: error: expected ';' before 'FSW_MARK'", "ninja: build stopped"]
    r = EspIdf(tmp_path / "idf", runner=fake_runner(out, code=1), env={}).build(src, target="esp32s3")
    assert not r["ok"]
    assert "application.cc:670: error" in r["error"]


def test_second_build_keeps_build_folder_for_incremental(tmp_path):
    src = board_project(tmp_path)
    (src / "build").mkdir()
    (src / "build/keep.o").write_text("x")
    EspIdf(tmp_path / "idf", runner=fake_runner(["Project build complete."]), env={}).build(src, target="esp32s3")
    assert (src / "build/keep.o").exists()


def test_flash_waits_for_device_then_continues(tmp_path):
    src = board_project(tmp_path)
    lists = iter([[], [], [{"device": "/dev/cu.usbmodem1101", "vid": 0x303A, "description": "USB JTAG/serial debug unit"}]])
    waits, seen, calls = [], [], []
    out = ["Connecting....", "Writing at 0x00010000... (37 %)", "Writing at 0x00020000... (100 %)", "Hard resetting via RTS pin..."]
    idf = EspIdf(tmp_path / "idf", runner=fake_runner(out, calls=calls), env={},
                 list_ports=lambda: next(lists), sleep=lambda s: None)
    r = idf.flash(src, on_waiting=lambda: waits.append(1), on_progress=lambda p, m: seen.append(p))
    assert r["ok"] and r["port"] == "/dev/cu.usbmodem1101"
    assert len(waits) == 2
    assert seen[-1] == 100
    assert calls[0][-3:] == ["-p", "/dev/cu.usbmodem1101", "flash"]


def test_flash_interruption_is_reported(tmp_path):
    src = board_project(tmp_path)
    out = ["Writing at 0x00010000... (48 %)", "A fatal error occurred: The chip stopped responding."]
    idf = EspIdf(tmp_path / "idf", runner=fake_runner(out, code=2), env={},
                 list_ports=lambda: [{"device": "COM3", "vid": 0x1A86, "description": "USB-SERIAL CH340"}])
    r = idf.flash(src)
    assert not r["ok"] and "stopped responding" in r["error"]


def test_find_ports_only_keeps_usb_serial_devices():
    ports = [{"device": "/dev/cu.Bluetooth-Incoming-Port", "vid": None, "description": "n/a"},
             {"device": "/dev/tty.usbmodem1101", "vid": 0x303A, "description": "USB JTAG"},
             {"device": "/dev/cu.usbmodem1101", "vid": 0x303A, "description": "USB JTAG"},
             {"device": "COM5", "vid": 0x10C4, "description": "CP210x"}]
    assert find_ports(lambda: ports) == ["/dev/cu.usbmodem1101", "COM5"]


def test_read_lines_from_debug_port():
    import serial
    s = serial.serial_for_url("loop://", timeout=0.2)
    s.write(b"I (12) boot: ok\r\nFSW,P2,18342210,0\r\nFSW,RESET,1,0\r\n")
    got = []
    for line in read_lines(s):
        got.append(line)
        if len(got) == 3:
            break
    assert got == ["I (12) boot: ok", "FSW,P2,18342210,0", "FSW,RESET,1,0"]
