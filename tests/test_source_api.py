import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw.web.app import create_app
from tests.test_inspect import CMAKE, idf_project, write


@pytest.fixture
def make(tmp_path):
    def _make(picker=None):
        return TestClient(create_app(data_dir=tmp_path / "data", picker=picker))
    return _make


def test_choosing_a_real_firmware_folder(make, tmp_path):
    src = idf_project(tmp_path / "door")
    r = make().post("/api/source/inspect", json={"path": str(src)}).json()

    assert r["ok"] and r["status"] == "new"
    assert r["info"]["name"] == "door-ctrl"
    assert r["info"]["chip"] == "ESP32-S3"
    assert Path(r["work"]).is_dir()


def test_not_firmware_gives_plain_message(make, tmp_path):
    src = write(tmp_path / "web", {"package.json": "{}", "a.js": ""})
    r = make().post("/api/source/inspect", json={"path": str(src)}).json()

    assert not r["ok"] and r["kind"] == "not_firmware"
    assert "换一个文件夹" in r["message"]


def test_project_with_many_boards_asks_which_one_then_accepts_it(make, tmp_path):
    src = write(tmp_path / "multi", {
        "CMakeLists.txt": CMAKE.replace("door-ctrl", "voice"),
        "main/boards/acme/s3-box/config.json": '{"target": "esp32s3"}',
        "main/boards/acme/c3-mini/config.json": '{"target": "esp32c3"}',
    })
    c = make()
    first = c.post("/api/source/inspect", json={"path": str(src)}).json()
    assert first["status"] == "needs_board"
    assert len(first["info"]["boards"]) == 2

    second = c.post("/api/source/inspect", json={"path": str(src), "board": "acme/c3-mini"}).json()
    assert second["status"] == "new"
    assert second["info"]["chip"] == "ESP32-C3"


def test_native_folder_picker_returns_the_path(make, tmp_path):
    c = make(picker=lambda kind: str(tmp_path / f"picked-{kind}"))
    assert c.post("/api/source/pick", json={"kind": "dir"}).json() == {"path": str(tmp_path / "picked-dir")}


def test_cancelled_picker(make):
    c = make(picker=lambda kind: None)
    assert c.post("/api/source/pick", json={"kind": "zip"}).json() == {"path": None}


def test_dragged_folder_is_uploaded_file_by_file_then_inspected(make, tmp_path):
    c = make()
    files = {"door-ctrl/CMakeLists.txt": CMAKE, "door-ctrl/main/main.c": "void app_main(void){}\n",
             "door-ctrl/sdkconfig.defaults": 'CONFIG_IDF_TARGET="esp32c6"\n'}
    for rel, text in files.items():
        assert c.put(f"/api/source/upload/abc123/{rel}", content=text.encode()).status_code == 200
    r = c.post("/api/source/inspect", json={"upload": "abc123"}).json()

    assert r["ok"] and r["info"]["name"] == "door-ctrl" and r["info"]["chip"] == "ESP32-C6"


def test_dragged_zip_is_uploaded_and_unpacked(make, tmp_path):
    src = idf_project(tmp_path / "door")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in src.rglob("*"):
            z.write(p, "door/" + str(p.relative_to(src)))
    c = make()
    c.put("/api/source/upload/zz9/door.zip", content=buf.getvalue())
    r = c.post("/api/source/inspect", json={"upload": "zz9"}).json()

    assert r["ok"] and r["info"]["name"] == "door-ctrl"


def test_upload_cannot_escape_its_folder(make):
    r = make().put("/api/source/upload/abc/..%2F..%2Fevil.txt", content=b"x")
    assert r.status_code in (400, 404)


def test_bad_upload_token_is_refused(make):
    assert make().put("/api/source/upload/..%2Fx/a.txt", content=b"x").status_code in (400, 404)


def test_key_test_button_uses_the_chosen_model(tmp_path):
    from fsw.llm import FakeLLM
    made = []

    def factory(provider, key):
        made.append((provider, key))
        return FakeLLM("", test_kind="bad_key")
    c = TestClient(create_app(data_dir=tmp_path / "data", llm_factory=factory))
    r = c.post("/api/llm/test", json={"provider": "deepseek", "key": "fake-typed-key"}).json()
    assert r["kind"] == "bad_key" and "密钥不对" in r["message"]
    assert made == [("deepseek", "fake-typed-key")]


def test_environment_reports_installed_idf(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                                 idf_finder=lambda: [{"path": "/x/esp-idf-v6.1", "version": "6.1.0"}],
                                 cc_status=lambda: {"found": False, "logged_in": False}, port_finder=lambda: []))
    assert c.get("/api/env").json()["idf"] == [{"path": "/x/esp-idf-v6.1", "version": "6.1.0"}]


def test_install_job_reports_steps_and_can_retry(tmp_path):
    runs = []

    def factory(version, dest, tools=None):
        class Fake:
            def run(self, on_event):
                runs.append(version)
                on_event({"state": "running", "step": 2, "pct": 45, "log": "Downloading xtensa 45%"})
                if len(runs) == 1:
                    return {"ok": False, "step": 2, "message": "第 3 步「下载编译器等工具」没装成：连不上乐鑫的服务器", "log": "timed out"}
                return {"ok": True, "path": str(dest)}
        return Fake()

    import time
    c = TestClient(create_app(data_dir=tmp_path / "d", installer_factory=factory, idf_finder=lambda: []))
    r = c.post("/api/install/start", json={"idf_min": "6.0.1"}).json()
    assert r["version"] == "v6.1"
    for _ in range(100):
        s = c.get("/api/install").json()
        if s["state"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert s["state"] == "failed" and s["step"] == 2 and "连不上" in s["message"]
    c.post("/api/install/retry")
    for _ in range(100):
        s = c.get("/api/install").json()
        if s["state"] == "done":
            break
        time.sleep(0.02)
    assert s["state"] == "done" and len(runs) == 2
