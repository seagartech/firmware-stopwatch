import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from fsw.workspace.inspect import inspect_source

CMAKE = """cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
set(PROJECT_VER "1.3.0")
project(door-ctrl)
"""


def write(root, files):
    for name, text in files.items():
        p = Path(root) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return Path(root)


def idf_project(root, target="esp32s3", idf=">=5.3"):
    return write(root, {
        "CMakeLists.txt": CMAKE,
        "main/CMakeLists.txt": "idf_component_register(SRCS main.c)\n",
        "main/main.c": "void app_main(void) {}\n",
        "main/idf_component.yml": f"dependencies:\n  idf:\n    version: '{idf}'\n",
        "sdkconfig.defaults": f'CONFIG_IDF_TARGET="{target}"\n',
    })


def test_recognizes_an_esp_idf_project(tmp_path):
    r = inspect_source(idf_project(tmp_path / "door"))

    assert r["ok"]
    assert r["platform"] == "ESP-IDF"
    assert r["name"] == "door-ctrl"
    assert r["chip"] == "ESP32-S3"
    assert r["idf_min"] == "5.3"
    assert r["root"] == str(tmp_path / "door")


def test_idf_version_from_simple_form(tmp_path):
    root = idf_project(tmp_path / "p")
    (root / "main/idf_component.yml").write_text("dependencies:\n  idf: '>=6.0.1'\n", encoding="utf-8")

    assert inspect_source(root)["idf_min"] == "6.0.1"


def test_chip_from_a_previous_build_wins(tmp_path):
    root = idf_project(tmp_path / "p", target="esp32")
    (root / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32c6"\n', encoding="utf-8")

    assert inspect_source(root)["chip"] == "ESP32-C6"


def test_project_with_many_boards_lists_them_when_never_built(tmp_path):
    root = write(tmp_path / "multi", {
        "CMakeLists.txt": CMAKE.replace("door-ctrl", "voice"),
        "main/boards/acme/s3-box/config.json": '{"target": "esp32s3", "type": "acme-s3-box"}',
        "main/boards/acme/c3-mini/config.json": '{"target": "esp32c3", "type": "acme-c3-mini"}',
        "main/boards/other-board/config.json": '{"target": "esp32", "type": "other-board"}',
    })

    r = inspect_source(root)

    assert r["ok"]
    assert r["chip"] is None
    assert r["needs_board"]
    assert [(b["id"], b["chip"]) for b in r["boards"]] == [
        ("acme/c3-mini", "ESP32-C3"), ("acme/s3-box", "ESP32-S3"), ("other-board", "ESP32"),
    ]


def test_name_falls_back_to_folder_name(tmp_path):
    root = idf_project(tmp_path / "my-fw")
    (root / "CMakeLists.txt").write_text(CMAKE.replace("project(door-ctrl)", "project(${NAME})"), encoding="utf-8")

    r = inspect_source(root)

    assert r["name"] == "my-fw"
    assert "文件夹" in r["name_why"]


def test_java_project_is_not_firmware(tmp_path):
    root = write(tmp_path / "java", {"pom.xml": "<project/>", **{f"src/A{i}.java": "class A{}" for i in range(5)}})

    r = inspect_source(root)

    assert not r["ok"]
    assert r["kind"] == "not_firmware"
    assert "Java 项目" in r["message"] and "pom.xml" in r["message"] and "5 个 .java" in r["message"]


def test_stm32_is_recognized_but_not_supported(tmp_path):
    root = write(tmp_path / "stm", {"board.ioc": "Mcu.Family=STM32F4", "Core/Startup/startup_stm32f407xx.s": ""})

    r = inspect_source(root)

    assert r["kind"] == "unsupported"
    assert "STM32" in r["message"] and "乐鑫" in r["message"]


def test_empty_folder(tmp_path):
    (tmp_path / "empty").mkdir()

    r = inspect_source(tmp_path / "empty")

    assert r["kind"] == "empty"
    assert "空的" in r["message"]


def test_missing_path(tmp_path):
    r = inspect_source(tmp_path / "nope")

    assert r["kind"] == "missing"
    assert "找不到" in r["message"]


def test_single_file_is_refused_with_reason(tmp_path):
    f = write(tmp_path, {"application.cc": "int x;"}) / "application.cc"

    r = inspect_source(f)

    assert r["kind"] == "single_file"
    assert "单个文件" in r["message"]


def test_bad_archive(tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"not a zip at all")

    r = inspect_source(bad, unpack_to=tmp_path / "out")

    assert r["kind"] == "bad_archive"
    assert "解不开" in r["message"]


@pytest.mark.parametrize("fmt", ["zip", "tar.gz"])
def test_archive_is_unpacked_then_inspected(tmp_path, fmt):
    src = idf_project(tmp_path / "door-ctrl")
    arc = tmp_path / f"door.{fmt}"
    if fmt == "zip":
        with zipfile.ZipFile(arc, "w") as z:
            for p in src.rglob("*"):
                z.write(p, "door-ctrl-main/" + str(p.relative_to(src)))
    else:
        with tarfile.open(arc, "w:gz") as t:
            t.add(src, arcname="door-ctrl-main")

    r = inspect_source(arc, unpack_to=tmp_path / "out")

    assert r["ok"] and r["name"] == "door-ctrl"
    assert r["archive"] == str(arc)
    assert Path(r["root"]).is_relative_to(tmp_path / "out")


def test_several_projects_in_one_folder_are_listed(tmp_path):
    idf_project(tmp_path / "all/door")
    other = idf_project(tmp_path / "all/lamp")
    (other / "CMakeLists.txt").write_text(CMAKE.replace("door-ctrl", "lamp"), encoding="utf-8")

    r = inspect_source(tmp_path / "all")

    assert r["kind"] == "multiple"
    assert sorted(p["name"] for p in r["projects"]) == ["door-ctrl", "lamp"]


def test_build_and_component_folders_are_not_extra_projects(tmp_path):
    root = idf_project(tmp_path / "p")
    write(root, {"managed_components/x/CMakeLists.txt": CMAKE, "build/y/CMakeLists.txt": CMAKE})

    assert inspect_source(root)["ok"]


def test_git_remote_and_commit_are_read(tmp_path):
    import subprocess
    root = idf_project(tmp_path / "p")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/door-ctrl.git"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], cwd=root, check=True)

    r = inspect_source(root)

    assert r["repo"] == "github.com/acme/door-ctrl"
    assert len(r["commit"]) == 7


REAL = Path.home() / "esp" / "xiaozhi-blind"


@pytest.mark.skipif(not REAL.exists(), reason="本机没有原版小智源码")
def test_real_xiaozhi_source():
    r = inspect_source(REAL)

    assert r["ok"] and r["platform"] == "ESP-IDF"
    assert r["name"] == "xiaozhi"
    assert r["idf_min"] == "6.0.1"
    assert r["repo"] == "github.com/78/xiaozhi-esp32"
    assert r["commit"] == "c0fe42a"
    assert r["needs_board"]
    core = [b for b in r["boards"] if b["id"] == "m5stack/core-s3"]
    assert core and core[0]["chip"] == "ESP32-S3"
