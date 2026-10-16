"""存下来的原版固件要和现在的源码对得上（决策 134）。

1. 改了源码再测（测→判→改→验的"验"）：以前原版固件只在第一次存，之后不再更新，
   还原时会把改之前的旧版本烧回设备。改成：原版固件记下是哪份源码、哪块板子编的，对不上就按这次编的重新存。
2. 源码和板子都没变（再测一次）：上次编好的原版能直接用，不用等后台再编一遍原版（插计时点不再等十几秒）。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.identify_fixtures import door_source
from tests.test_fsw_api import FakeDevice, fake_build_output, run_to_try, start_insert, wait
from tests.test_bgbuild import wait_for
from tests.test_instrument import reply


def app(tmp_path, builds, dev):
    def builder(work, target, board, idf, progress):
        builds.append(work)
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                              llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True},
                              port_finder=lambda: ["/dev/cu.usbmodem1101"], bg_builder=builder,
                              device=dev, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    return c


def one_run(c, src):
    r = c.post("/api/source/inspect", json={"path": str(src)}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"]})
    start_insert(c, r)
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"
    assert run_to_try(c)["status"] == "done"
    j = c.post("/api/fsw/restore").json()
    return r, j


def test_changed_source_refreshes_the_saved_original(tmp_path):
    builds, dev = [], FakeDevice()
    c = app(tmp_path, builds, dev)
    src = door_source(tmp_path / "door")
    r, _ = one_run(c, src)
    orig = Path(r["project_dir"]) / "firmware/orig/app.bin"
    before = orig.read_text()
    (src / "main" / "fix.c").write_text("int fix(void){return 1;}\n")  # 按建议改了源码，再测
    r2, j = one_run(c, src)
    assert orig.read_text() != before  # 原版固件换成了改过的源码编的
    assert j["flashed"] and dev.flashed[-1].endswith("firmware/orig")


def test_unchanged_source_reuses_the_saved_original(tmp_path):
    builds, dev = [], FakeDevice()
    src = door_source(tmp_path / "door")
    one_run(app(tmp_path, builds, dev), src)
    n = len(builds)
    c = app(tmp_path, builds, dev)  # 码表重开，同一份源码再测
    r = c.post("/api/source/inspect", json={"path": str(src)}).json()
    s = c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"]}).json()
    assert s["state"] == "done" and s.get("reused")
    assert len(builds) == n  # 没再编原版
    start_insert(c, r)
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"
    assert run_to_try(c)["status"] == "done"
    assert c.post("/api/fsw/restore").json()["flashed"]


def test_old_original_without_a_record_is_not_reused(tmp_path):
    """早先存的原版没记是哪份源码编的：不直接用，照旧在后台编一次。"""
    builds, dev = [], FakeDevice()
    src = door_source(tmp_path / "door")
    r, _ = one_run(app(tmp_path, builds, dev), src)
    (Path(r["project_dir"]) / "firmware/orig/fsw-orig.json").unlink()
    c = app(tmp_path, builds, dev)
    r = c.post("/api/source/inspect", json={"path": str(src)}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"]})
    s = wait_for(c, "done")
    assert not s.get("reused")


def test_switching_board_builds_the_original_again(tmp_path):
    """同一份源码换了板子：原版要按新板子再编一次，不能拿旧板子编的当原版。"""
    builds, dev = [], FakeDevice()
    c = app(tmp_path, builds, dev)
    src = door_source(tmp_path / "door")
    r = c.post("/api/source/inspect", json={"path": str(src)}).json()
    body = {"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"], "board": "a"}
    c.post("/api/bgbuild/start", json=body)
    wait_for(c, "done")
    c.post("/api/bgbuild/start", json={**body, "board": "b"})
    assert wait_for(c, "done")["board"] == "b" and len(builds) == 2
