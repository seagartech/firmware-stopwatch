"""10-26 编译工具没准备好（服务从精简的 PATH 起，找不到 cmake）：
1. 报错里"cmake must be available on the PATH""command not found""No such file or directory: 'ninja'"等算"编译工具没准备好（缺 X），不是你的源码问题"，
   工具报一行就正常退出的情况也算；真正的编译报错照旧。
2. 编译前自检：cmake、ninja、idf.py、esptool 找不找得到，缺了点名；/api/env 带回去，首页、设置页能看到；后台编原版前就报，不等编到一半。
3. 编译、烧录的子进程 PATH 自动补常见工具目录和乐鑫自带工具目录：只补真有的、不重复、原来的顺序不动。"""

import json
import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw.platforms import espidf as E
from fsw.web.app import create_app

JS = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
posix = pytest.mark.skipif(os.name == "nt", reason="Mac / Linux 的目录")


def _exe(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


# ---------- 1. 认出"工具没找到" ----------

@pytest.mark.parametrize("text,names", [
    ('ESP-IDF v6.1\n"cmake" must be available on the PATH to use idf.py\nExecuting action: all (aliases: build)', ["cmake"]),
    ("bash: ninja: command not found", ["ninja"]),
    ("sh: 1: cmake: not found", ["cmake"]),
    ("FileNotFoundError: [Errno 2] No such file or directory: '/opt/x/bin/ninja'", ["ninja"]),
    ("python: can't open file '/x/esp-idf/tools/idf.py': [Errno 2] No such file or directory", ["idf.py"]),
    ("/x/python: No module named esptool", ["esptool"]),
    ("'cmake' is not recognized as an internal or external command,", ["cmake"]),
])
def test_missing_tool_lines_are_recognised(text, names):
    assert E.missing_tools(text) == names


@pytest.mark.parametrize("text", [
    "main/app.cc:12:10: fatal error: foo.h: No such file or directory",
    "FileNotFoundError: [Errno 2] No such file or directory: 'sdkconfig'",
    "CMake Error at main/CMakeLists.txt:3 (idf_component_register): component not found",
    "main/app.cc:5: error: 'x' was not declared in this scope",
])
def test_real_compile_errors_are_not_tool_problems(text):
    assert E.missing_tools(text) == []


def _idf(tmp_path, lines, code=0):
    calls = []

    def runner(cmd, cwd, env):
        calls.append(cmd)
        for l in lines:
            yield l
        return code
    env = {"IDF_PYTHON_ENV_PATH": str(tmp_path / "nope"), "PATH": "/usr/bin:/bin"}
    return E.EspIdf(tmp_path / "idf", runner=runner, env=env), calls


def test_build_one_line_then_normal_exit_is_a_tool_problem(tmp_path):
    (tmp_path / "src").mkdir()
    idf, _ = _idf(tmp_path, ["ESP-IDF v6.1", '"cmake" must be available on the PATH to use idf.py',
                             "Executing action: all (aliases: build)"], code=2)
    r = idf.build(tmp_path / "src", "esp32s3")
    assert not r["ok"] and r["cause"] == "tool" and r["missing"] == ["cmake"]
    assert r["message"].startswith("编译工具没准备好（缺 cmake），不是你的源码问题") and "设置页" in r["message"]
    assert "源码本身编不过" not in r["message"]


def test_build_real_error_keeps_old_path(tmp_path):
    (tmp_path / "src").mkdir()
    idf, _ = _idf(tmp_path, ["main/a.cc:3:1: error: expected ';' before 'x'"], code=1)
    r = idf.build(tmp_path / "src", "esp32s3")
    assert not r["ok"] and not r.get("cause") and "expected" in r["error"]


def test_program_itself_missing_is_reported_by_name(tmp_path):
    (tmp_path / "src").mkdir()

    def runner(cmd, cwd, env):
        raise FileNotFoundError(2, "No such file or directory", "/x/bin/ninja")
        yield  # noqa
    idf = E.EspIdf(tmp_path / "idf", runner=runner, env={"IDF_PYTHON_ENV_PATH": "/nope"})
    r = idf.build(tmp_path / "src", "esp32s3")
    assert r["cause"] == "tool" and r["missing"] == ["ninja"]


def test_flash_without_esptool_is_a_tool_problem_and_not_retried(tmp_path):
    fw = tmp_path / "fw"
    fw.mkdir()
    (fw / "flasher_args.json").write_text(json.dumps({"flash_files": {"0x0": "a.bin"}, "write_flash_args": [],
                                                       "extra_esptool_args": {"before": "default-reset"}}), encoding="utf-8")
    idf, calls = _idf(tmp_path, ["/x/python: No module named esptool"], code=1)
    idf.sleep = lambda s: None
    r = idf.flash_saved(fw, "/dev/cu.x")
    assert r["cause"] == "tool" and r["missing"] == ["esptool"] and len(calls) == 1


# ---------- 3. PATH 自动补目录 ----------

@posix
def test_with_tool_dirs_adds_existing_dirs_once_and_keeps_order(tmp_path, monkeypatch):
    brew, local, gone = tmp_path / "brew/bin", tmp_path / "local/bin", tmp_path / "gone"
    brew.mkdir(parents=True)
    local.mkdir(parents=True)
    monkeypatch.setattr(E, "COMMON_TOOL_DIRS", [str(brew), str(local), str(gone), "/usr/bin"])
    env = {"IDF_TOOLS_PATH": str(tmp_path / "none")}
    out = E.with_tool_dirs(f"/usr/bin:{local}:/bin", env).split(":")
    assert out[:3] == ["/usr/bin", str(local), "/bin"]  # 原来的顺序不动
    assert out.count(str(local)) == 1 and out.count("/usr/bin") == 1 and str(brew) in out and str(gone) not in out


@posix
def test_idf_own_cmake_and_ninja_dirs_are_added(tmp_path, monkeypatch):
    tools = tmp_path / "espressif"
    _exe(tools / "tools/cmake/3.30.2/CMake.app/Contents/bin/cmake")
    _exe(tools / "tools/ninja/1.12.1/ninja")
    monkeypatch.setattr(E, "COMMON_TOOL_DIRS", [])
    p = E.with_tool_dirs("/usr/bin", {"IDF_TOOLS_PATH": str(tools)})
    assert str((tools / "tools/cmake/3.30.2/CMake.app/Contents/bin").resolve()) in p.split(":")
    assert str((tools / "tools/ninja/1.12.1").resolve()) in p.split(":")


@posix
def test_export_env_fills_path_when_service_started_with_short_path(tmp_path, monkeypatch):
    brew = tmp_path / "brew/bin"
    _exe(brew / "cmake")
    monkeypatch.setattr(E, "COMMON_TOOL_DIRS", [str(brew)])
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("IDF_TOOLS_PATH", str(tmp_path / "none"))
    env = E.EspIdf(tmp_path / "idf").export_env()
    assert env["PATH"].split(":")[-1] == str(brew)


# ---------- 2. 编译前自检 ----------

@posix
def test_check_tools_names_what_is_missing(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    _exe(bin_ / "cmake")
    monkeypatch.setattr(E, "COMMON_TOOL_DIRS", [])
    idf = tmp_path / "esp-idf"
    (idf / "tools").mkdir(parents=True)
    r = E.check_tools(idf, {"PATH": str(bin_), "IDF_TOOLS_PATH": str(tmp_path / "none")})
    names = [m["name"] for m in r["missing"]]
    assert not r["ok"] and "ninja" in names and "idf.py" in names and "cmake" not in names
    assert r["found"]["cmake"] == str(bin_ / "cmake")
    assert all(m["why"] for m in r["missing"])


@posix
def test_check_tools_finds_cmake_through_added_dirs(tmp_path, monkeypatch):
    brew = tmp_path / "brew/bin"
    _exe(brew / "cmake")
    _exe(brew / "ninja")
    monkeypatch.setattr(E, "COMMON_TOOL_DIRS", [str(brew)])
    r = E.check_tools(None, {"PATH": "/usr/bin:/bin", "IDF_TOOLS_PATH": str(tmp_path / "none")})
    assert r["ok"] and r["found"]["cmake"] == str(brew / "cmake")


def _app(tmp_path, check, builder=None):
    return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                                 idf_finder=lambda: [{"path": "/x/esp-idf-v6.1", "version": "6.1.0"}],
                                 cc_status=lambda: {"found": False, "logged_in": False}, port_finder=lambda: [],
                                 bg_builder=builder or (lambda *a: {"ok": True, "seconds": 0}), tool_check=check))


MISS = lambda p: {"ok": False, "found": {"ninja": "/opt/homebrew/bin/ninja"},
                  "missing": [{"name": "cmake", "why": "生成编译清单用（CMake）"}]}


def test_api_env_carries_the_self_check(tmp_path):
    seen = []
    c = _app(tmp_path, lambda p: (seen.append(p), MISS(p))[1])
    t = c.get("/api/env?cc=0").json()["tools"]
    assert seen == ["/x/esp-idf-v6.1"] and t["missing"][0]["name"] == "cmake"


def test_bg_build_stops_before_compiling_and_names_the_tool(tmp_path):
    built = []
    c = _app(tmp_path, MISS, builder=lambda *a: built.append(a) or {"ok": True})
    (tmp_path / "w").mkdir()
    j = c.post("/api/bgbuild/start", json={"work": str(tmp_path / "w"), "chip": "ESP32-S3"}).json()
    assert j["state"] == "failed" and j["cause"] == "tool" and j["missing"] == ["cmake"]
    assert "编译工具没准备好（缺 cmake），不是你的源码问题" in j["message"] and not built


def test_bg_build_tool_failure_keeps_missing(tmp_path):
    ok = lambda p: {"ok": True, "missing": [], "found": {}}
    c = _app(tmp_path, ok, builder=lambda *a: {"ok": False, "seconds": 1, "cause": "tool", "missing": ["cmake"],
                                                "message": E.tool_missing_message(["cmake"]), "error": "x"})
    (tmp_path / "w").mkdir()
    c.post("/api/bgbuild/start", json={"work": str(tmp_path / "w"), "chip": "ESP32-S3"})
    for _ in range(100):
        j = c.get("/api/bgbuild").json()
        if j["state"] != "running":
            break
        time.sleep(0.02)
    assert j["state"] == "failed" and j["cause"] == "tool" and j["missing"] == ["cmake"]


def test_page_names_the_missing_tool_and_points_to_settings():
    assert "编译工具没准备好（缺 ${m.map(htmlEsc).join('、')}），不是你的源码问题" in JS
    assert "TOOLT(E.cause,E.missing)" in JS and "TOOLT(BG.cause,BG.missing)" in JS
    assert "function toolsBox(" in JS and "${toolsBox()}" in JS  # 设置页的自检
    assert "编译工具缺 ${toolMiss()" in JS  # 首页的灯
