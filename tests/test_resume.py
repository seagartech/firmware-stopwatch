"""D 组：刷新、点首页、后退都不丢进度。"""

from pathlib import Path

from fastapi.testclient import TestClient

from fsw.web.app import create_app

JS = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")


def make(tmp_path):
    return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                 cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: []))


def test_page_progress_is_kept_and_read_back(tmp_path):
    c = make(tmp_path)
    empty = c.get("/api/ui").json()
    assert empty["ui"] is None
    assert empty["jobs"] == {"identify": None, "fsw": None, "bgbuild": None}
    snap = {"v": 1, "page": "flash", "flow": "first", "flowMax": 2, "REAL": {"work": "/w"}, "dropped": ["P1"]}
    assert c.post("/api/ui", json=snap).json() == {"ok": True}
    assert c.get("/api/ui").json()["ui"] == snap


def test_demo_and_real_progress_do_not_overwrite_each_other(tmp_path):
    c = make(tmp_path)
    c.post("/api/ui", json={"v": 1, "page": "flash"})
    c.post("/api/ui?key=door", json={"v": 1, "page": "report"})
    assert c.get("/api/ui").json()["ui"]["page"] == "flash"
    assert c.get("/api/ui?key=door").json()["ui"]["page"] == "report"


def test_bad_or_huge_progress_is_refused(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/ui", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 400
    assert c.post("/api/ui", json=[1, 2]).status_code == 400
    assert c.post("/api/ui", json={"x": "a" * 2_100_000}).status_code == 413


def test_jobs_say_which_copy_they_belong_to(tmp_path):
    c = make(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    r = c.post("/api/bgbuild/start", json={"work": str(work), "chip": "esp32s3"})
    assert r.status_code == 200
    assert c.get("/api/ui").json()["jobs"]["bgbuild"] == str(work)


def test_page_restores_from_backend_and_uses_the_address_bar():
    # 打开时先接上进度再画；换页写进网址，后退、前进在码表里换页
    assert "await uiRestore()" in JS and "booting=false" in JS
    assert "function render(){if(booting" in JS
    assert "history.pushState" in JS and "addEventListener('popstate'" in JS
    # 只读接口接上后台任务，不重新选源码（重新选会把插好的计时点撤掉）
    body = JS[JS.index("async function uiRestore("):JS.index("async function uiRestore(") + 4000]
    assert "/api/source/inspect" not in body
    for u in ("/api/identify", "/api/fsw", "/api/collect", "/api/bgbuild", "/api/advise"):
        assert u in body
    # 接不上的步骤：回到最近能接着做的一步，并说清楚
    assert "这一步的进度没能接上" in JS


def test_home_button_keeps_the_step_bar_in_first_run():
    # 点首页只是回看：第一次测的步骤条不清；真换了固件或板子才作废
    assert "if(to==='home'&&!g.dataset.keepflow&&flow!=='first')flow=null;" in JS
    assert "function newRun(){" in JS and "换了固件或板子：后面的步骤要重新做" in JS


def test_fresh_open_goes_home_with_a_way_back():
    # 新打开（网址没带 #页面名）进首页，首页顶上「接着看」回上次那页；刷新带着 #页面名，还停在原页
    body = JS[JS.index("async function uiRestore("):JS.index("async function uiRestore(") + 6000]
    assert "if(!HASH0&&page!=='home'&&page!=='settings'){LASTP=page;page='home'" in body
    assert "上次停在「${htmlEsc(PNAME(LASTP))}」" in JS and 'data-go="${LASTP}" data-keepflow="1">接着看' in JS
    assert "if(!isHome)LASTP='';" in JS
    # 在首页刷新，「接着看」还在
    assert "last:page==='home'?LASTP:''" in JS
