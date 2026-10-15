"""任务 18 的网页后台：打开原始日志所在文件夹（只限码表自己的文件夹）；合理范围改过后报告提示用新范围更新（不用重测）。"""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw import standards as stds
from fsw.web.app import create_app
from tests.test_advise_api import real_log
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_standards import PAGE


@pytest.fixture
def app(tmp_path, monkeypatch):
    std_dir = tmp_path / "standards"
    shutil.copytree(stds.HERE, std_dir)
    monkeypatch.setattr(stds, "HERE", std_dir)
    opened = []
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                              cc_status=lambda: {"found": True, "logged_in": True},
                              port_finder=lambda: ["/dev/cu.usbmodem1101"], device=FakeSerialDevice(real_log()),
                              wait_step=0.01, opener=opened.append))
    return c, opened, std_dir, tmp_path


def measured(c, tmp_path):
    proj = tmp_path / "data" / "xiaozhi-1"
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(proj)})
    return wait(c, lambda j: j["status"] == "done")


def test_open_raw_log_folder_only_inside_our_folders(app):
    c, opened, _, tmp_path = app
    j = measured(c, tmp_path)
    assert c.post("/api/open-folder", json={"path": j["raw_log"]}).json()["ok"]
    assert opened == [j["raw_log"]]
    assert c.post("/api/open-folder", json={"path": "/etc/passwd"}).status_code == 403


def test_report_says_when_ranges_changed_and_refreshes_without_remeasuring(app):
    c, _, std_dir, tmp_path = app
    measured(c, tmp_path)
    r = c.get("/api/report").json()
    assert not r["stale"]  # 刚测完的不提示
    f = next(std_dir.glob("*.json"))
    s = json.loads(f.read_text(encoding="utf-8"))
    s["segments"]["think"]["range"] = [0.4, 1.2]  # 有人改了"服务器想回答"的合理范围
    f.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    r = c.get("/api/report").json()
    assert r["stale"] and r["stale_since"]
    before = {g["k"]: g for g in r["segments"]}["P2-P5"]
    assert before["range"] == [0.4, 1.5] and before["verdict"] == "正常"  # 还是按改之前的判
    r2 = c.post("/api/report/refresh").json()
    seg = {g["k"]: g for g in r2["segments"]}["P2-P5"]
    assert not r2["stale"] and seg["range"] == [0.4, 1.2] and seg["verdict"] == "偏慢"  # 按新范围判，用时不变
    assert seg["med"] == before["med"]


# ---- 导出：一个 HTML 文件，存在项目文件夹的 reports/，文件名"场景_测完时间.html"，断网能开 ----

PAGE_HTML = '<!doctype html><html><head><meta charset="utf-8"><style>body{}</style></head><body><h1>报告</h1><script>function sw(v){}</script></body></html>'


def test_export_saves_into_project_reports_folder(app):
    c, opened, _, tmp_path = app
    j = measured(c, tmp_path)
    r = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "说完话到开口"}).json()
    p = Path(r["path"])
    assert p.parent == tmp_path / "data" / "xiaozhi-1" / "reports"
    assert p.name == "说完话到开口_" + j["measured_at"].replace("-", "").replace(" ", "-").replace(":", "") + ".html"
    assert p.read_text(encoding="utf-8") == PAGE_HTML
    r2 = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "说完话到开口"}).json()
    assert r2["path"] != r["path"] and Path(r2["path"]).exists()  # 再导一次不覆盖上一份
    assert c.post("/api/open-folder", json={"path": r["path"]}).json()["ok"]  # 导出后能打开所在文件夹


def test_export_of_demo_goes_to_demo_reports(app):
    c, _, _, tmp_path = app
    r = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "刷卡/到开门", "demo": "door"}).json()
    p = Path(r["path"])
    assert p.parent == tmp_path / "data" / "demo-reports" and p.name.startswith("刷卡-到开门_")


@pytest.mark.parametrize("bad", [
    PAGE_HTML.replace("<h1>", '<h1>' + 'sk' + '-abcdefgh12345678'),                 # 像密钥
    PAGE_HTML.replace("<style>", '<link rel="stylesheet" href="https://x.com/a.css"><style>'),  # 外链样式
    PAGE_HTML.replace("<script>", '<script src="https://x.com/a.js"></script><script>'),       # 外链脚本
    PAGE_HTML.replace("<h1>", '<img src="http://x.com/a.png"><h1>'),        # 外链图片
    "不是网页",
])
def test_export_refuses_keys_and_outside_resources(app, bad):
    c, _, _, tmp_path = app
    r = c.post("/api/report/export", json={"html": bad, "what": "x", "demo": "door"})
    assert r.status_code == 400
    assert not (tmp_path / "data" / "demo-reports").exists() or not any((tmp_path / "data" / "demo-reports").iterdir())
