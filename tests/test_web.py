import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw.web.app import create_app

WEB = Path(__file__).resolve().parent.parent / "fsw" / "web"


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css"])
def test_page_and_its_files_open(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert len(r.text) > 100


def test_top_bar_matches_the_prototype(client):
    html = client.get("/").text
    top = re.search(r'<div class="apphd">.*?</div>', html, re.S).group(0)
    assert "⏱ 固件码表" in top
    assert ">首页<" in top and ">设置<" in top
    assert top.count("<button") == 3


def test_demo_bar_of_the_prototype_is_not_in_the_real_page(client):
    html = client.get("/").text
    assert "演示栏" not in html
    assert 'class="bar"' not in html


def test_every_prototype_page_has_a_renderer():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    for fn in ["settingsPage", "stepsPage", "flashPage", "collectPage", "reportPage",
               "comparePage", "retestPage", "rangePage", "modalHTML", "popHTML", "flowBar"]:
        assert f"function {fn}(" in js, f"app.js 里没有 {fn}"
    assert "HOME={" in js


def test_two_demo_scenarios_are_listed(client):
    demos = client.get("/api/demo").json()
    assert [(d["id"], d["label"]) for d in demos if not d["hidden"]] == [("voice", "语音对话"), ("door", "门禁刷卡")]
    # 第三套"对话（只看开口）"只用来核对报告（只有一条线），不进"以前测过的"
    assert [d["id"] for d in demos if d["hidden"]] == ["dialog"]


@pytest.mark.parametrize("sid", ["voice", "door"])
def test_demo_data_has_everything_the_pages_need(client, sid):
    d = client.get(f"/api/demo/{sid}").json()
    for key in ["S", "F", "P", "C", "R", "RG", "RT"]:
        assert key in d, f"演示数据缺 {key}"
    S = d["S"]
    assert 1 <= len(S["lines"]) <= 4
    for line in S["lines"]:
        for k in line["segs"]:
            assert len(S["segs"][k]["v"]) == 20
            assert "→" in S["segs"][k]["code"]
    for p in d["C"]["probs"]:
        assert all(1 <= n <= 20 for n in p["rounds"])


def test_unknown_demo_is_404(client):
    assert client.get("/api/demo/nope").status_code == 404


def test_state_without_flow_is_empty(client):
    assert client.get("/api/state").json()["flow"] is None


def test_page_reports_its_step_bar_to_the_backend(client):
    client.post("/api/flow/start", json={"kind": "first", "demo": "door"})
    s = client.post("/api/flow/sync", json={"reached": 2, "viewing": "probes", "error": "编译没通过"}).json()
    assert [x["status"] for x in s["steps"]] == ["done", "done", "error", "todo", "todo"]
    assert s["error"] == "编译没通过"


def test_flow_api_refuses_invalid_moves_in_plain_words(client):
    client.post("/api/flow/start", json={"kind": "first", "demo": "voice"})
    r = client.post("/api/flow/view", json={"step": "report"})
    assert r.status_code == 409
    assert "还没做到" in r.json()["detail"]


SCENE_WORDS = re.compile(r"小灵|小智|xiaozhi|CoreS3|门禁|刷卡|door|voice|语音对话|scene==='")


def test_code_has_no_scenario_specific_names():
    files = [p for p in WEB.rglob("*") if p.suffix in {".py", ".js", ".html", ".css"}]
    hits = [
        f"{p.name}: {m.group(0)}"
        for p in files
        for m in SCENE_WORDS.finditer(p.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_home_page_reads_real_source_instead_of_demo_data():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/source/inspect" in js and "/api/source/pick" in js and "/api/source/upload/" in js
    assert "function pickSrc(" not in js, "首页还在用假的选源码"
    assert "demoErr==='java'" not in js and "demoErr==='stm32'" not in js
    assert "你用的是哪块？" in js  # 决策 61：多块板子时问用哪块


def test_key_test_button_is_real_and_demo_key_button_is_gone():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/llm/test" in js
    assert "data-demokey" not in js


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css"])
def test_browser_always_checks_for_a_newer_page(client, path):
    """升级码表后浏览器不能还用旧的网页代码。"""
    assert client.get(path).headers.get("cache-control") == "no-cache"


def test_page_files_are_requested_with_a_version_tag(client):
    html = client.get("/").text
    assert re.search(r'/static/app\.js\?v=\w+', html) and re.search(r'/static/style\.css\?v=\w+', html)


def test_home_light_uses_real_compiler_detection():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/env" in js
    assert "已装 v6.1'" not in js, "环境灯里的版本号不能写死"


def test_background_build_pill_uses_real_status():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/bgbuild/start" in js and "/api/bgbuild'" in js
    assert "原版编译没通过，不是码表改的" in js


def test_install_box_uses_real_installer_and_demo_switches_are_gone():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/install/start" in js and "/api/install/retry" in js
    assert 'data-inst="fail"' not in js, "演示安装失败的按钮应去掉"
    assert "样子演示，看哪台电脑" not in js


def test_settings_page_uses_real_checks_not_prototype_fakes():
    """任务 12：保存、Claude Code 登录、设备、结果位置都换成真查，原型里的假文字去掉。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "a3F9" not in js                      # 假密钥尾巴
    assert "找到 v5.3" not in js                  # 假的编译工具列表
    assert "/api/env?cc=0" in js and "data-datadir" in js and 'data-idf="pick"' in js
    assert "fetch('/api/settings'" in js          # 保存真写
    assert "applied" not in js                    # 灯不再看"点过保存"的假标记
    assert "REAL?'':'<button class=\"pill\" data-fl=\"plug\">演示：设备已插上</button>'" in js


def test_find_steps_page_uses_real_identification():
    """任务 13：选了真源码时，AI 推荐、找关键步骤、添加步骤都调后台真做；演示数据只在没选真源码时用。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    for api in ("/api/suggest", "/api/identify/start", "/api/identify/retry", "/api/identify/add", "fetch('/api/identify')"):
        assert api in js, api
    assert "data-idretry" in js
    assert "mmss(IDENT.seconds)" in js  # 选了真源码时，用时写真实的


def test_flash_page_uses_real_insert_build_flash():
    """任务 14：选了真源码时，插计时点、编译、烧录、试一次、撤掉一处、还原都调后台真做。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    for api in ("/api/fsw/insert", "/api/fsw/run", "/api/fsw/drop", "/api/fsw/restore", "fetch('/api/fsw')"):
        assert api in js, api
    assert "diffViewReal" in js and "flErrReal" in js


def test_collect_page_reads_rounds_from_backend():
    """任务 15：多轮实测的每轮数据由后台读设备输出算出来，不再直接拿演示数据里的数。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "/api/collect/start" in js and "/api/collect/stop" in js and "fetch('/api/collect')" in js
    assert "EventSource" not in js and "D.rounds[col.got" not in js


def test_report_uses_backend_judgment():
    """任务 16：报告的快慢、标签、两把尺子由后台按固定规则算，网页只显示。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "/api/report" in js and "withJudge" in js and "decisionsJ" in js


def test_plan_checkbox_does_not_open_the_source_picker():
    """方案勾选框和首页"选文件夹"按钮都用 data-pick：选源码只认按钮，勾方案不能弹出选文件夹窗口。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "closest('button[data-pick]')" in js and "closest('[data-pick]')" not in js


def test_report_text_uses_names_not_codes_and_open_folder():
    """任务 18：结论建议和待办里把 P2-P5 这类编号换成中文名；原始日志"打开所在文件夹"接后台；合理范围改过提示接后台。"""
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "function plain(" in js and "plain(F.why)" in js and "为什么：${F.title}。${F.dev.data}" not in js
    assert "/api/open-folder" in js and "/api/report/refresh" in js
