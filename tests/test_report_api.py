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


def test_first_full_run_only_records_and_old_range_list_is_ignored(app):
    """合理范围清单 10-19 去掉了：测满 10 轮也不建清单；项目文件夹里留着的老清单（哪怕写坏了）不读、不报错、不删。"""
    c, _, _, tmp_path = app
    proj = tmp_path / "data" / "xiaozhi-1"
    proj.mkdir(parents=True)
    (proj / "合理范围清单.md").write_text("# 合理范围清单：老的\n| 坏 |", encoding="utf-8")
    measured(c, tmp_path)
    r = c.get("/api/report").json()
    seg = {g["k"]: g for g in r["segments"]}["P2-P5"]
    assert seg["verdict"] == "已记录" and seg["vs"] is None and r["scenario"]["first_run"]  # 第一次测：只记用时
    assert "list_filled" not in r and "range_list" not in r["scenario"]
    assert (proj / "合理范围清单.md").read_text(encoding="utf-8") == "# 合理范围清单：老的\n| 坏 |"   # 不动用户的文件
    assert c.get("/api/rangelist").status_code == 404 and c.get("/api/rangelists").status_code == 404


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


# ---- 真测修复：导出默认放被测源码的同级目录，也能改；文件名用你自己写的场景说法 ----

def with_source(tmp_path):
    src = tmp_path / "firmware-demo" / "xiaozhi-esp32"
    src.mkdir(parents=True)
    proj = tmp_path / "data" / "xiaozhi-1"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.json").write_text(json.dumps({"source": str(src)}), encoding="utf-8")
    return src


def test_export_defaults_next_to_the_source(app):
    c, opened, _, tmp_path = app
    src = with_source(tmp_path)
    measured(c, tmp_path)
    assert c.get("/api/report/export-dir").json()["dir"] == str(src.parent)
    r = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "从我说完话到设备回话"}).json()
    p = Path(r["path"])
    assert p.parent == src.parent and p.name.startswith("从我说完话到设备回话_")
    assert c.post("/api/open-folder", json={"path": r["path"]}).json()["ok"]  # 存到别处的也能在访达里显示


def test_export_to_a_folder_you_choose(app):
    c, _, _, tmp_path = app
    with_source(tmp_path)
    measured(c, tmp_path)
    mine = tmp_path / "我的报告"
    mine.mkdir()
    r = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "x", "folder": str(mine)}).json()
    assert Path(r["path"]).parent == mine
    assert c.post("/api/report/export", json={"html": PAGE_HTML, "what": "x", "folder": "相对路径"}).status_code == 400
    assert c.post("/api/report/export", json={"html": PAGE_HTML, "what": "x", "folder": str(tmp_path / "没有这个")}).status_code == 400


def test_open_the_report_just_exported_next_to_the_source(app):
    """导出完点「打开看看」：报告默认存在源码旁边（结果文件夹外面），也要能打开。"""
    c, opened, _, tmp_path = app
    with_source(tmp_path)
    measured(c, tmp_path)
    r = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "x"}).json()
    assert c.post("/api/report/open", json={"path": r["path"]}).json()["ok"]
    assert opened[-1] == str(Path(r["path"]).resolve())
    assert c.post("/api/report/open", json={"path": "/etc/passwd"}).status_code == 404  # 不是导出过的文件不开
    js = (Path(__file__).parent.parent / "fsw" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'data-ropen="${htmlEsc(EXP.saved)}">打开看看' in js and "'/api/report/open'" in js


def test_export_to_default_folder_that_does_not_exist_yet(app):
    """新数据目录里还没有 demo-reports/：对话框预填的就是它，第一次导出要能自动建，不报"文件夹不存在"。"""
    c, _, _, tmp_path = app
    d = c.get("/api/report/export-dir?demo=door").json()["dir"]
    assert not Path(d).exists()
    r = c.post("/api/report/export", json={"html": PAGE_HTML, "what": "x", "demo": "door", "folder": d})
    assert r.status_code == 200 and Path(r.json()["path"]).parent == Path(d)
    other = tmp_path / "别的不存在的文件夹"
    assert c.post("/api/report/export", json={"html": PAGE_HTML, "what": "x", "demo": "door", "folder": str(other)}).status_code == 400
    js = (Path(__file__).parent.parent / "fsw" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    i = js.index("EXP.saved=j.path;EXP.state='ok';")
    assert "t.classList.contains('t-bad'))t.hidden=true" in js[i - 200:i]  # 重试成功后收起上次的红色提示


def test_first_run_even_when_a_segment_and_a_line_share_a_name(app):
    # 真测第 29 次：全新数据第一次测，顶上却写"这次新出现的 12 项……其余照清单判"：按条数比，同名的段和线让条数对不上
    import copy
    c, _, _, tmp_path = app
    page = copy.deepcopy(PAGE)
    page["segs"][0]["t"] = page["ln"]["talk"]
    proj = tmp_path / "data" / "xiaozhi-2"
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 20, "project_dir": str(proj)})
    wait(c, lambda j: j["status"] == "done")
    assert c.get("/api/report").json()["scenario"]["first_run"]
