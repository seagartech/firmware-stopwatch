"""后台任务出意外不能卡在"进行中"（定版深度排查 B 组）：大模型读回复超时、断连、坏行，行号不是整数，
烧录时抛异常，都要转成这个任务的失败状态、说一句怎么办、原始报错放进详情，并且能重试。

假模型、假编译、假设备，不联网、不真烧录。
"""

import http.client
import json
import subprocess
import time
import urllib.request

import pytest

from fsw import identify as idf
from fsw import instrument as ins
from fsw import llm as llms
from fsw import standards as stds
from fsw.config import load_config
from fsw.llm import ClaudeAPI, ClaudeCode, DeepSeek, FakeLLM, LLMError
from fsw.platforms.espidf import ToolEnvError
from tests.test_fsw_api import FakeDevice, run_to_try, setup, start_insert, wait  # noqa: F401（setup 是夹具）
from tests.test_instrument import STEPS, reply
from tests.test_speedup import ins_app, ins_body, prep_wait  # noqa: F401（ins_app 是夹具）

FAKE_KEY = "fake-key-for-tests-0001"


# ---------- B1：DeepSeek 读回复时出错，要归成大模型错误 ----------
class Resp:
    def __init__(self, exc=None, lines=()):
        self.exc, self.lines = exc, lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        raise self.exc

    def __iter__(self):
        yield from self.lines
        if self.exc:
            raise self.exc


@pytest.mark.parametrize("exc,kind", [
    (TimeoutError("The read operation timed out"), "slow"),
    (ConnectionResetError(104, "Connection reset by peer"), "dropped"),
    (http.client.IncompleteRead(b"{", 100), "dropped"),
])
def test_deepseek_investigate_read_errors_become_llm_errors(monkeypatch, tmp_path, exc, kind):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Resp(exc))
    with pytest.raises(LLMError) as e:
        DeepSeek(FAKE_KEY).investigate("x", tmp_path)
    assert e.value.kind == kind and "DeepSeek" in e.value.message and e.value.detail
    assert "离线" not in e.value.message and FAKE_KEY not in e.value.message


def test_deepseek_insert_read_timeout_is_a_plain_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Resp(TimeoutError("timed out")))
    r = ins.insert(DeepSeek(FAKE_KEY), tmp_path, [{"p": "P1", "n": "x", "path": "a.c", "l": 1}], tmp_path / "bk")
    assert not r["ok"] and r["kind"] == "slow" and "重新插入" in r["message"]


@pytest.mark.parametrize("lines,exc", [
    ([b'data: {"choices":[{"delta":{"content":"a"}}]}\n', b"data: {not json\n"], None),
    ([b'data: {"choices":[{"delta":{"content":"a"}}]}\n'], TimeoutError("timed out")),
    ([b'data: {"choices":[{"delta":{"content":"a"}}]}\n'], ConnectionResetError(104, "reset")),
])
def test_deepseek_stream_bad_line_or_drop_becomes_llm_error(monkeypatch, lines, exc):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Resp(exc, lines))
    with pytest.raises(LLMError):
        list(DeepSeek(FAKE_KEY).stream("x"))
    assert idf.suggest(DeepSeek(FAKE_KEY), "/nonexistent", {}) == []  # 推荐常用语照样不报错


def test_deepseek_connect_timeout_is_not_called_offline(monkeypatch):
    def slow(req, timeout=None):
        raise urllib.error.URLError(TimeoutError("timed out"))
    monkeypatch.setattr(urllib.request, "urlopen", slow)
    r = DeepSeek(FAKE_KEY).test()
    assert r["kind"] == "slow" and "连不上" not in r["message"]


def _net_error(name):
    """SDK 底下网络库（httpx2）的异常：只认模块名和类名，不依赖装的是哪个版本。"""
    return type(name, (Exception,), {"__module__": "httpx2._exceptions"})("peer closed connection")


def test_claude_api_timeout_is_not_called_offline():
    import anthropic
    m = ClaudeAPI(FAKE_KEY)
    with pytest.raises(LLMError) as e:
        with m._errors():
            raise anthropic.APITimeoutError(request=None)
    assert e.value.kind == "slow" and "连不上" not in e.value.message and e.value.detail
    with pytest.raises(LLMError) as e:  # 边答边传的途中超时、断开：SDK 原样抛网络库的异常
        with m._errors():
            raise _net_error("ReadTimeout")
    assert e.value.kind == "slow"
    with pytest.raises(LLMError) as e:
        with m._errors():
            raise _net_error("RemoteProtocolError")
    assert e.value.kind == "dropped"
    with pytest.raises(KeyError):  # 别的错照原样抛，不乱归
        with m._errors():
            raise KeyError("x")


# ---------- 顺带：密钥文件带 BOM、Claude Code 输出按 UTF-8 解码 ----------
def test_key_file_with_bom_is_stripped(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "deepseek"\n', encoding="utf-8")
    (tmp_path / "deepseek_key.txt").write_text("﻿" + FAKE_KEY + "\r\n", encoding="utf-8")
    assert load_config(config_dir=tmp_path, env={}).deepseek_api_key == FAKE_KEY


def test_claude_code_output_decoded_as_utf8(monkeypatch, tmp_path):
    seen = {}
    real = subprocess.Popen

    def popen(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)
    monkeypatch.setattr(llms.subprocess, "Popen", popen)
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\necho '{\"type\":\"result\",\"is_error\":false,\"result\":\"好\"}'\n", encoding="utf-8")
    exe.chmod(0o755)
    assert "".join(ClaudeCode(exe).stream("x")) == "好"
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"  # Windows 默认按 GBK 解码，中文会出错


# ---------- B2：AI 给的行号不是整数 ----------
def _bad_line_reply(root):
    (root / "main").mkdir(parents=True)
    (root / "main/app.cc").write_text("\n".join(f"// {i}" for i in range(1, 800)) + "\n")
    res = {"start_found": True, "end_found": True, "start": {"name": "你说完"}, "main": {"name": "开口", "end": "开口"},
           "steps": [{"id": "P1", "name": "起点", "file": "main/app.cc", "line": 10, "line_of": "start", "sure": "高"},
                     {"id": "P2", "name": "收到文字", "file": "main/app.cc", "line": "655-660", "line_of": "main", "sure": "高"},
                     {"id": "P3", "name": "开始说话", "file": "main/app.cc", "line": None, "line_of": "main", "sure": "高"},
                     {"id": "P4", "name": "说完", "file": "main/app.cc", "line": "700", "line_of": "main", "sure": "高"}],
           "segments": [{"from": "P1", "to": "P2", "name": "a"}, {"from": "P2", "to": "P3", "name": "b"}]}
    return "```json\n" + json.dumps(res, ensure_ascii=False) + "\n```"


def test_identify_normalizes_non_integer_lines(tmp_path):
    r = idf.parse(_bad_line_reply(tmp_path), tmp_path)
    by = {s["id"]: s for s in r["steps"]}
    assert all(isinstance(s["line"], int) for s in r["steps"])
    assert by["P2"]["line"] == 655 and by["P2"]["sure"] == "低" and "655-660" in by["P2"]["doubt"]
    assert by["P3"]["line"] == 0 and by["P3"]["sure"] == "低" and not by["P3"]["loc_ok"]
    assert by["P4"]["line"] == 700 and by["P4"]["sure"] == "高"  # 写成字符串的整数照常
    page = idf.to_page(r, "从说完话到开口")
    stds.find(page["steps"])  # 不再抛异常


def test_standards_find_tolerates_bad_lines():
    steps = [{"p": "P1", "path": "main/application.cc", "l": "655-660"}, {"p": "P2", "path": "main/a.cc", "l": None},
             {"p": "P3", "path": "main/a.cc", "l": 12}]
    assert stds.find(steps) is None


def test_prepare_with_bad_line_does_not_hang_insert(ins_app):
    c, r, made = ins_app
    bad = [dict(STEPS[0], l="88-90")] + STEPS[1:]
    real_find = stds.find

    def boom(steps):
        raise ValueError("invalid literal for int() with base 10: '88-90'")
    stds.find = boom  # 照旧代码的样子：对标准时出错
    try:
        c.post("/api/fsw/prepare", json=ins_body(r, bad))
        p = prep_wait(c)
    finally:
        stds.find = real_find
    assert p["status"] == "failed" and "重新生成" in p["message"] and "ValueError" in p["error"]
    c.post("/api/fsw/insert", json=ins_body(r, bad))
    j = wait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted"  # 提前准备没成就重新生成，不会一直等它


# ---------- 插计时点：大模型抛的不是 LLMError 也不能卡住 ----------
class Raising(FakeLLM):
    def __init__(self, exc):
        super().__init__(reply())
        self.exc = exc

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        raise self.exc


def test_insert_crash_becomes_failure_and_can_retry(tmp_path):
    from fastapi.testclient import TestClient
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_fsw_api import fake_build_output
    models = [Raising(TimeoutError("The read operation timed out")), FakeLLM(reply())]

    def builder(work, target, board, idf_, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: models.pop(0),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/x"],
                              bg_builder=builder, device=FakeDevice(), wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    j = wait(c, lambda j: j["status"] != "running")
    assert j["status"] == "failed" and j["err"]["kind"] == "insert" and "重新插入" in j["err"]["message"]
    assert "TimeoutError" in j["err"]["error"]
    start_insert(c, r)  # 点"重新插入"
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"


# ---------- B3 和烧录：device.flash 抛异常 ----------
class RaisingDevice(FakeDevice):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def flash(self, fw, port, on_progress):
        if self.exc:
            raise self.exc
        return super().flash(fw, port, on_progress)


def test_flash_crash_fails_and_reburn_works(setup):
    dev = RaisingDevice(RuntimeError("加载乐鑫编译工具的环境失败"))
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and j["err"]["kind"] == "burn" and "重新烧录" in j["err"]["message"]
    assert "RuntimeError" in j["err"]["error"]
    dev.exc = None
    assert c.post("/api/fsw/run", json={"from": "flash"}).status_code == 200  # 不被"正在做"挡住
    assert wait(c, lambda j: j["status"] in ("done", "failed"))["status"] == "done"


def test_flash_tool_error_raised_still_says_tool(setup):
    c, r, *_ = setup(device=RaisingDevice(ToolEnvError("export.sh: not found")))
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["err"]["kind"] == "burn" and j["err"]["cause"] == "tool" and j["err"]["error"] == "export.sh: not found"


def test_restore_flash_crash_is_not_500(setup):
    dev = RaisingDevice(None)
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    dev.exc = RuntimeError("加载乐鑫编译工具的环境失败")
    res = c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]})
    assert res.status_code == 200
    out = res.json()
    assert not out["flashed"] and "源码已还原" in out["say"] and "再点一次" in out["say"] and "RuntimeError" in out["detail"]
    dev.exc = None  # 再点一次就能烧回去
    assert c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]}).json()["flashed"]


def test_restore_waiting_for_device_then_flash_crash_is_reported(setup):
    dev = RaisingDevice(RuntimeError("boom"))
    c, r, _, ports, *_ = setup(device=dev)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    ports.clear()
    out = c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]}).json()
    assert out["waiting"]
    ports.append("/dev/cu.usbmodem1101")
    j = wait(c, lambda j: not (j.get("restore") or {}).get("waiting"))
    assert not j["restore"]["flashed"] and "再点一次" in j["restore"]["say"]
