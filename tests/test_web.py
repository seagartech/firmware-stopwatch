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


def test_report_export_button_next_to_the_toggle(client):
    """任务 18：导出按钮在报告右上角"结论建议 / 技术细节"旁边；导出的文件把样式和切换都放进去，按钮换掉、勾选换成 ☑ / ☐。"""
    html = client.get("/").text
    head = re.search(r'<header class="top">.*?</header>', html, re.S).group(0)
    assert 'id="v-exp"' in head and ">导出<" in head
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "async function exportReport(" in js and "/api/report/export" in js
    assert "'☑':'☐'" in js and "fetch('/static/style.css')" in js


SAY20 = ["今天天气怎么样", "给我讲个笑话", "你叫什么名字", "一加一等于几", "你会唱歌吗", "我有点累了", "推荐一本适合周末看的书",
         "你今天心情好吗", "用一句话介绍一下你自己", "北京到上海有多远", "你觉得猫可爱还是狗可爱", "帮我想个周末去哪玩",
         "说一句鼓励我的话", "你能听懂方言吗", "春天适合种什么花", "喝水有什么好处", "推荐一首轻松的歌", "怎么才能睡得好一点",
         "给我出个简单的谜语", "你喜欢吃什么"]


@pytest.mark.parametrize("sid", ["voice", "dialog"])
def test_voice_scenario_says_the_fixed_20_for_real_tests(client, sid):
    """真机实测要说的 20 句（项目文档"真机实测20句"）：去掉会结束对话、触发工具、回答太长的几句，改完再测也用这份。"""
    d = client.get(f"/api/demo/{sid}").json()
    assert d["C"]["items"] == SAY20


@pytest.mark.parametrize("sid", ["voice", "dialog", "door"])
def test_prep_page_says_the_list_is_fixed_not_made_by_ai(client, sid):
    """准备页：要说 / 要刷的清单是固定清单，不是"AI 读完代码生成的"。"""
    assert "固定清单" in client.get(f"/api/demo/{sid}").json()["C"]["itemsFrom"]
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert 'AI 读完代码、按"测从什么到什么"生成的' not in js and "C.itemsFrom" in js


def test_export_lets_you_pick_the_folder_and_keeps_the_notice():
    """导出：先问存到哪（默认被测源码的同级目录，可换），存完的提示一直在、写完整路径，直到你关掉。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/report/export-dir" in js and "pop==='export'" in js and "data-expdir" in js
    assert "function expDone(" in js and "data-expclose" in js and "EXP.saved" in js
    assert "toast('✓ 已导出：'" not in js  # 不再用一闪就没的提示


def test_export_file_name_uses_your_own_words():
    """文件名用你自己写的场景说法（比如"从我说完话到设备回话"），不换成码表的简写。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "IDENT.page.mapWhat||hSay" in js.split("async function exportReport(")[1].split("\n}")[0] or \
        "expWhat()" in js


def test_device_status_comes_from_one_place():
    """首页、找关键步骤页、烧录页、多轮实测页说设备插没插，都看同一个来源；不再写死"没插上"或假端口名。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "const devNow=" in js and js.count("devNow()") >= 4
    assert "'设备已连上 '+(PORTS[0]||(setOS==='mac'?'/dev/cu.usbmodem1101':'COM3'))" not in js


def test_try_once_waits_for_you_to_be_ready():
    """试一次：先大字提示"先喊唤醒词，再说一句"，点"准备好了"才开始。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "先喊唤醒词，再说一句" in js and "准备好了" in js and "data-flx=\"trygo\"" in js and "'ready_try'" in js


def test_home_history_is_real_only_demo_only_in_demo_entry():
    """首页"以前测过的"只列真测过的（后台 /api/history）；演示数据只在演示入口（地址里写 ?demo=）出现。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    rl = js.split("let HIST=")[1].split("const PROJ_LIST=")[0]
    assert "DEMO?PROJ" in rl and "HIST" in rl and "data-hopen" in rl
    assert "fetch('/api/history')" in js and "/api/history/open" in js


def test_report_links_each_item_to_the_card_that_discusses_it():
    """要你拍板：每段"偶尔特别慢"各有自己的编号，挂到讲它的那张卡片；和对错问题同一根因的合并进去，不单列。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "const cardOf=" in js and "x.with" in js
    assert "out.push({k:'spiky'," not in js.split("function decisionsJ(")[1].split("\nfunction ")[0]


def test_real_collect_page_does_not_borrow_demo_content():
    """多轮实测页：真流程用这次的线、问题和每轮做什么（/api/rounds），不再用演示数据的。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "/api/rounds" in js and "const CX=" in js and "const DX=" in js
    for fn in ("function colSetup(", "function colA(", "function colB(", "function colC(", "function tipOf(",
               "function needMore(", "function enough(", "function colApply("):
        body = js.split(fn)[1].split("\nfunction ")[0]
        assert "build(SCENES[scene])" not in body and "CC()" not in body, fn


def test_suggestions_have_no_duplicates():
    """首页常用语：这个项目测过的 → AI 推荐 → 通用例子，同一句只出现一次。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "new Set(" in js.split("function chips(")[1].split("\nfunction ")[0]


def test_real_range_page_shows_this_runs_standard_not_demo():
    """报告里"合理范围依据"：真流程看这次对上的标准和实测基线（只读），不再显示演示数据的标准文件。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "function rangeReal(" in js
    assert "if(realFlash()&&JREP&&!JREP._none)return rangeReal()" in js.split("function rangePage(")[1][:200]
