"""设置页（任务 12）：保存真写进设置文件夹、密钥单独放且不回传、环境检查（Claude Code 登录、设备、编译工具）。

全部用临时文件夹，不碰真的 ~/.config/firmware-stopwatch/；不真调用大模型。
"""

import json
import stat

import pytest
from fastapi.testclient import TestClient

from fsw.config import load_config, save_config
from fsw.llm import FakeLLM, claude_code_status
from fsw.web.app import create_app
from tests.test_llm import fake_claude

FAKE_KEY = "fake-deepseek-key-for-tests-0003"


def idf_dir(root, version="6.1.0"):
    (root / "tools/cmake").mkdir(parents=True)
    (root / "tools/idf.py").write_text("", encoding="utf-8")
    ma, mi, pa = version.split(".")
    (root / "tools/cmake/version.cmake").write_text(
        f"set(IDF_VERSION_MAJOR {ma})\nset(IDF_VERSION_MINOR {mi})\nset(IDF_VERSION_PATCH {pa})\n", encoding="utf-8")
    return root


@pytest.fixture
def make(tmp_path):
    def _make(**kw):
        kw.setdefault("cc_status", lambda: {"found": True, "logged_in": True})
        kw.setdefault("port_finder", lambda: [])
        kw.setdefault("idf_finder", lambda: [])
        return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", **kw))
    return _make


# ---------- 写设置文件 ----------
def test_save_config_writes_settings_and_key_separately(tmp_path):
    save_config(tmp_path, provider="deepseek", key=FAKE_KEY, data_dir="~/fsw-data")

    cfg = load_config(config_dir=tmp_path, env={})
    assert cfg.llm_provider == "deepseek" and cfg.deepseek_api_key == FAKE_KEY
    assert FAKE_KEY not in (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert stat.S_IMODE((tmp_path / "deepseek_key.txt").stat().st_mode) == 0o600


def test_save_config_keeps_what_was_not_changed(tmp_path):
    save_config(tmp_path, provider="claude-api", key="k1", idf_path="/opt/idf", data_dir="/d")
    save_config(tmp_path, provider="claude-code")

    cfg = load_config(config_dir=tmp_path, env={})
    assert cfg.llm_provider == "claude-code"
    assert cfg.claude_api_key == "k1"
    assert cfg.idf_path == "/opt/idf" and str(cfg.data_dir) == "/d"


def test_save_config_handles_quotes_and_backslashes(tmp_path):
    save_config(tmp_path, data_dir='C:\\esp "data"')
    assert str(load_config(config_dir=tmp_path, env={}).data_dir) == 'C:\\esp "data"'


# ---------- 保存按钮 ----------
def test_settings_start_empty_then_save_really_writes(make, tmp_path):
    c = make()
    s = c.get("/api/state").json()["settings"]
    assert s["llm_provider"] is None and not s["has_key"]["deepseek"]

    r = c.post("/api/settings", json={"llm_provider": "deepseek", "key": FAKE_KEY}).json()

    assert r["settings"]["llm_provider"] == "deepseek" and r["settings"]["has_key"]["deepseek"]
    assert FAKE_KEY not in json.dumps(r)  # 密钥不回传给网页
    assert load_config(config_dir=tmp_path / "cfg", env={}).deepseek_api_key == FAKE_KEY
    # 重开码表还在
    assert make().get("/api/state").json()["settings"]["has_key"]["deepseek"]


def test_settings_accepts_page_names_for_models(make, tmp_path):
    make().post("/api/settings", json={"llm_provider": "cc"})
    assert load_config(config_dir=tmp_path / "cfg", env={}).llm_provider == "claude-code"


def test_settings_unknown_model_is_refused(make):
    r = make().post("/api/settings", json={"llm_provider": "gpt"})
    assert r.status_code == 400 and "大模型" in r.json()["detail"]


def test_manual_idf_location_must_really_have_the_tool(make, tmp_path):
    good = idf_dir(tmp_path / "my-idf")
    c = make(idf_finder=None)

    bad = c.post("/api/settings", json={"idf_path": str(tmp_path)})
    assert bad.status_code == 400 and "没找到乐鑫编译工具" in bad.json()["detail"]

    ok = c.post("/api/settings", json={"idf_path": str(good)}).json()
    assert ok["settings"]["idf_path"] == str(good)
    assert str(good.resolve()) in [i["path"] for i in c.get("/api/env").json()["idf"]]


def test_changing_result_folder_is_used_for_new_projects(make, tmp_path):
    from tests.test_inspect import idf_project
    c = make()
    c.post("/api/settings", json={"data_dir": str(tmp_path / "new-data")})
    r = c.post("/api/source/inspect", json={"path": str(idf_project(tmp_path / "door"))}).json()
    assert r["project_dir"].startswith(str(tmp_path / "new-data"))


def test_key_test_uses_injected_model_and_never_saves(make, tmp_path):
    fake = FakeLLM("好")
    c = make(llm_factory=lambda provider, key: fake)
    r = c.post("/api/llm/test", json={"provider": "deepseek", "key": FAKE_KEY}).json()
    assert r["ok"] and fake.prompts
    assert not (tmp_path / "cfg" / "deepseek_key.txt").exists()


def test_key_test_failure_is_plain(make):
    c = make(llm_factory=lambda provider, key: FakeLLM("", test_kind="no_balance"))
    r = c.post("/api/llm/test", json={"provider": "deepseek", "key": "x"}).json()
    assert not r["ok"] and r["kind"] == "no_balance" and "余额" in r["message"]


# ---------- 环境：Claude Code 登录、设备 ----------
def test_env_reports_claude_code_and_ports(make):
    c = make(cc_status=lambda: {"found": True, "logged_in": False}, port_finder=lambda: ["/dev/cu.usbmodem1101"])
    env = c.get("/api/env").json()
    assert env["claude_code"] == {"found": True, "logged_in": False}
    assert env["ports"] == ["/dev/cu.usbmodem1101"]
    assert c.get("/api/ports").json() == {"ports": ["/dev/cu.usbmodem1101"]}


def test_claude_code_status_reads_auth_status(tmp_path):
    exe = fake_claude(tmp_path, [{"loggedIn": True, "authMethod": "claude.ai", "email": "x@example.com"}])
    assert claude_code_status(exe) == {"found": True, "logged_in": True}


def test_claude_code_status_not_logged_in(tmp_path):
    exe = fake_claude(tmp_path, [{"loggedIn": False}], code=1)
    assert claude_code_status(exe) == {"found": True, "logged_in": False}


def test_claude_code_status_not_installed():
    assert claude_code_status(None) == {"found": False, "logged_in": False}


def test_quick_env_check_skips_claude_code(make):
    """每隔几秒查一次编译工具和设备时不跑 claude 命令（那个只在打开设置页、点"重新检查"时查）。"""
    calls = []
    c = make(cc_status=lambda: calls.append(1) or {"found": True, "logged_in": True},
             idf_finder=lambda: [{"path": "/x/esp-idf", "version": "6.1.0"}], port_finder=lambda: ["COM3"])
    env = c.get("/api/env?cc=0").json()
    assert env == {"idf": [{"path": "/x/esp-idf", "version": "6.1.0"}], "claude_code": None, "ports": ["COM3"]}
    assert calls == []
