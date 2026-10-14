import threading
import time

from fastapi.testclient import TestClient

from fsw.web.app import create_app


def wait_for(c, state, secs=5):
    t = time.time()
    while time.time() - t < secs:
        s = c.get("/api/bgbuild").json()
        if s["state"] == state:
            return s
        time.sleep(0.02)
    raise AssertionError(f"没等到 {state}，现在是 {s}")


def make(tmp_path, builder, idf=True):
    return TestClient(create_app(data_dir=tmp_path / "d", bg_builder=builder,
                                 idf_finder=lambda: [{"path": "/x", "version": "6.1.0"}] if idf else []))


def test_original_code_builds_in_background_with_progress(tmp_path):
    gate = threading.Event()

    def builder(work, target, board, idf_path, on_progress):
        on_progress(40, "[900/2241] Building")
        gate.wait(2)
        on_progress(100, "编译完成")
        return {"ok": True, "seconds": 118.0, "error": None}

    c = make(tmp_path, builder)
    r = c.post("/api/bgbuild/start", json={"work": str(tmp_path), "chip": "ESP32-S3", "board": "m5stack/core-s3", "idf_min": "6.0.1"}).json()
    assert r["state"] == "running"
    s = wait_for(c, "running")
    assert s["pct"] in (0, 40)
    gate.set()
    s = wait_for(c, "done")
    assert s["pct"] == 100 and s["seconds"] == 118.0


def test_starting_again_for_the_same_code_does_not_build_twice(tmp_path):
    calls = []

    def builder(*a, **k):
        calls.append(1)
        time.sleep(0.2)
        return {"ok": True, "seconds": 1.0, "error": None}

    c = make(tmp_path, builder)
    body = {"work": str(tmp_path), "chip": "ESP32-S3", "board": None, "idf_min": None}
    c.post("/api/bgbuild/start", json=body)
    c.post("/api/bgbuild/start", json=body)
    wait_for(c, "done")
    c.post("/api/bgbuild/start", json=body)
    assert len(calls) == 1


def test_original_that_does_not_build_is_reported(tmp_path):
    c = make(tmp_path, lambda *a, **k: {"ok": False, "seconds": 9.0, "error": "main.c:3: error: x"})
    c.post("/api/bgbuild/start", json={"work": str(tmp_path), "chip": "ESP32-S3"})
    s = wait_for(c, "failed")
    assert "error" in s["error"]
    assert "不是码表改的" in s["message"]


def test_waits_for_compiler_when_not_installed(tmp_path):
    c = make(tmp_path, lambda *a, **k: {"ok": True}, idf=False)
    r = c.post("/api/bgbuild/start", json={"work": str(tmp_path), "chip": "ESP32-S3", "idf_min": "6.0.1"}).json()
    assert r["state"] == "waiting_tool"


def test_chip_name_becomes_build_target(tmp_path):
    got = []
    c = make(tmp_path, lambda work, target, board, idf_path, on_progress: got.append(target) or {"ok": True, "seconds": 1, "error": None})
    c.post("/api/bgbuild/start", json={"work": str(tmp_path), "chip": "ESP32-C3"})
    wait_for(c, "done")
    assert got == ["esp32c3"]
