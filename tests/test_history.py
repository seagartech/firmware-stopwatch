"""真测修复 4：首页"以前测过的"只列真测过的记录（结果文件夹里的项目），不混演示数据。"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw.web.app import create_app
from tests.test_advise_api import real_log
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_standards import PAGE


def project(data, name="xiaozhi-1", runs=1, with_page=True, export=None):
    proj = data / name
    (proj / "identify").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"name": "xiaozhi", "board": "m5stack/core-s3",
                                                   "source": "/x/firmware-demo/xiaozhi-esp32"}), encoding="utf-8")
    page = {**PAGE, "mapWhat": "从我说完话到设备回话", "measure": "你说完 → 设备出声"}
    if with_page:
        (proj / "identify" / "a.json").write_text(json.dumps({"what": "从我说完话到设备回话", "page": page}), encoding="utf-8")
    for i in range(runs):
        run = proj / "runs" / f"20261015-15{30 + i}00"
        run.mkdir(parents=True)
        (run / "raw.log").write_text("\n".join(real_log()), encoding="utf-8")
    if export:
        (proj / "reports").mkdir()
        (proj / "reports" / export).write_text("<!doctype html>", encoding="utf-8")
    return proj


@pytest.fixture
def client(tmp_path):
    opened = []
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [],
                              opener=opened.append))
    return c, tmp_path / "data", opened


def test_history_lists_only_real_measured_projects(client):
    c, data, _ = client
    project(data, runs=2, export="从我说完话到设备回话_20261015-1533.html")
    project(data, "door-9", runs=0)                       # 没测过的不列
    (data / "demo-reports").mkdir(parents=True)            # 演示数据导出的不算
    h = c.get("/api/history").json()
    assert len(h) == 1
    x = h[0]
    assert x["name"] == "xiaozhi（m5stack/core-s3）" and x["what"] == "从我说完话到设备回话"
    assert x["rounds"] == 20 and x["metric"] == "设备出声" and x["now"] == pytest.approx(2.175, abs=0.01)
    assert x["prev"] == pytest.approx(x["now"]) and x["path"] == "/x/firmware-demo/xiaozhi-esp32"
    assert x["report"].endswith("从我说完话到设备回话_20261015-1533.html")


def test_history_empty_when_nothing_measured(client):
    c, _, _ = client
    assert c.get("/api/history").json() == []


def test_open_latest_report_of_a_record(client):
    c, data, opened = client
    proj = project(data, export="r.html")
    assert c.post("/api/history/open", json={"path": str(proj)}).json()["ok"]
    assert opened[-1].endswith("r.html")


def test_exports_are_remembered_for_the_record(client, tmp_path):
    """导出到源码旁边的报告也记在项目里，首页点这条能打开。"""
    c, data, opened = client
    proj = data / "xiaozhi-1"
    proj.mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({"name": "xiaozhi", "source": str(tmp_path / "src")}), encoding="utf-8")
    (tmp_path / "src").mkdir()
    c2 = TestClient(create_app(data_dir=data, config_dir=tmp_path / "cfg2", idf_finder=lambda: [],
                               cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/x"],
                               device=FakeSerialDevice(real_log()), wait_step=0.01, opener=opened.append))
    c2.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(proj),
                                           "what": "从我说完话到设备回话"})
    wait(c2, lambda j: j["status"] == "done")
    r = c2.post("/api/report/export", json={"html": "<!doctype html><p>x</p>", "what": "从我说完话到设备回话"}).json()
    h = c2.get("/api/history").json()
    assert h[0]["report"] == r["path"] and h[0]["what"] == "从我说完话到设备回话"
