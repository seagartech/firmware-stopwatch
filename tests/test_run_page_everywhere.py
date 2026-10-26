"""10-26 用户："所有界面显示新版本"。一次测量（flow first）的每一页都画成「测量：全程」那一页，不再靠网页内存里的
"自动执行"开关：以前它在三种情况下丢掉，页面就退回老的一页一页界面（带老步骤条）：
1. 用 DeepSeek 找完关键步骤停下请人核对；2. 刷新网页 / 新开标签页；3. 第 1 轮校验没过点「查看并修改计时点位置」。
这里用无界面的 Chrome 真跑 app.js（后台接口用假的），看页面上画的是哪一种。
10-26 11:00 又遇到："又跳到老版本界面了"：用 DeepSeek 找完就按模型名停下，测量页里塞的是老的整页"找关键步骤"
（关键流程地图、读取状态、"9 个只找对 4 个"）。决策 181：模型选择本身不要人核对，只有码表核对出实际问题才停，
停下也只在测量页里列要看的步骤。fixtures/run_page_deepseek_hold_1026.json 是那一刻的接口原样。
10-26 13:07 用户在 Safari 上又说"老版本"：其实是核对停下的那一页还是老的步骤卡片（需要你核对、添加步骤……），停下的原因
还写错了（"落进条件分支"，真正是先后说不准）。现在：中间的点有问题码表自动去掉、接着走；只有起点、终点有问题才停，
停下那页和测量页一个样子（原因、计时点清单、「确认，插入计时点」「重新分析」）。WebKit（Safari 的内核）装了就也跑一遍。"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "fsw/web/static/app.js").read_text(encoding="utf-8")
CHROME = next((p for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                           shutil.which("google-chrome") or "", shutil.which("chromium") or "",
                           shutil.which("chromium-browser") or ""] if p and Path(p).exists()), None)
needs_chrome = pytest.mark.skipif(not CHROME, reason="没有 Chrome")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    c = TestClient(create_app(data_dir=d / "data", config_dir=d / "cfg", idf_finder=lambda: [], port_finder=lambda: [],
                              cc_status=lambda: {"found": True, "logged_in": True}))
    lst = c.get("/api/demo").json()
    return {"/api/demo": lst, **{f"/api/demo/{x['id']}": c.get(f"/api/demo/{x['id']}").json() for x in lst}}


FIX = json.loads((ROOT / "tests/fixtures/run_page_deepseek_hold_1026.json").read_text(encoding="utf-8"))
OLD_HOLD = "这次用的是 DeepSeek，准确率较低，请核对关键步骤"


PW = Path(os.environ.get("FSW_PLAYWRIGHT_DIR", "/tmp/pw"))
WEBKIT = bool(shutil.which("node")) and (PW / "node_modules/playwright").is_dir() and (PW / "browsers").is_dir()
ENGINES = ["chrome"] + (["webkit"] if WEBKIT else [])


def page(tmp_path, demo, *, session=None, ui=None, after="", app_js=None, api_extra=None, engine="chrome"):
    """在 Chrome 里打开码表网页（接口全是假的），开机完成后跑 after，再把页面上画了什么交回来。"""
    body = (ROOT / "fsw/web/static/index.html").read_text(encoding="utf-8")
    body = body[body.index("<body>") + 6:body.index('<script src="/static/app.js">')]
    api = {**demo, "/api/ui": {"ui": ui, "jobs": {}, "server": None}, "/api/collect": {"status": "idle"},
           "/api/fsw": {"status": "idle"}, "/api/identify": {"state": "idle"}, "/api/history": [],
           "/api/state": {"settings": {"llm_provider": "cc", "reuse_last": True}}, "/api/env": {"idf": [], "ports": []},
           **(api_extra or {})}
    seed = "".join(f"sessionStorage.setItem({json.dumps(k)},{json.dumps(v)});" for k, v in (session or {}).items())
    js = Path(app_js or ROOT / "fsw/web/static/app.js").as_uri()
    html = f"""<!doctype html><html><head><meta charset="utf-8"></head><body>{body}
<pre id="__res"></pre>
<script>
sessionStorage.clear();{seed}
const __API={json.dumps(api, ensure_ascii=False)};
window.fetch=(u,o)=>{{const p=String(u).split('?')[0];const d=p in __API?__API[p]:{{}};
 return Promise.resolve({{ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(d))}})}};
navigator.sendBeacon=()=>true;
</script>
<script src="{js}"></script>
<script>
(async()=>{{const w=ms=>new Promise(r=>setTimeout(r,ms));for(let i=0;i<100&&booting;i++)await w(50);
 try{{ {after} }}catch(e){{document.getElementById('__res').textContent=JSON.stringify({{err:String(e)}});return}}
 await w(300);
 const m=document.getElementById('main');
 document.getElementById('__res').textContent=JSON.stringify({{page,flow,AUTO,autoHold,
  run:/测量：全程/.test(m.innerHTML),oldbar:!!document.querySelector('#flowbar ol.flow'),
  steps:!!m.querySelector('[data-goins],[data-go="flash"]'),hold:/自动执行在这里停下/.test(m.innerHTML),
  old:!!m.querySelector('.mapbox,.srow2,.hiwrap')||/关键流程地图|只找对 4 个|请每条都仔细核对|准确率较低|需要你核对|添加步骤|可信度高的/.test(m.innerText),
  drop:[...dropped],reanalyze:!!m.querySelector('[data-reanalyze]'),
  text:m.innerText.slice(0,400)}});
}})();
</script></body></html>"""
    f = tmp_path / "h.html"
    f.write_text(html, encoding="utf-8")
    if engine == "webkit":
        js_run = tmp_path / "wk.js"
        js_run.write_text("const {webkit}=require('playwright');(async()=>{const b=await webkit.launch();const p=await b.newPage();"
                          f"await p.goto({json.dumps(f.as_uri())});await p.waitForFunction(()=>document.getElementById('__res').textContent,null,{{timeout:30000}});"
                          "console.log('<pre id=\"__res\">'+await p.evaluate(()=>document.getElementById('__res').innerHTML)+'</pre>');await b.close()})()",
                          encoding="utf-8")
        out = subprocess.run(["node", str(js_run)], capture_output=True, text=True, timeout=120, cwd=PW,
                             env={**os.environ, "NODE_PATH": str(PW / "node_modules"), "PLAYWRIGHT_BROWSERS_PATH": str(PW / "browsers")}).stdout
        m = re.search(r'<pre id="__res">(.*?)</pre>', out, re.S)
        assert m and m.group(1), out[-2000:]
        import html as H
        return json.loads(H.unescape(m.group(1)))
    out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                          f"--user-data-dir={tmp_path / 'chrome'}", "--allow-file-access-from-files",
                          "--virtual-time-budget=15000", "--dump-dom", f.as_uri()],
                         capture_output=True, text=True, timeout=120).stdout
    m = re.search(r'<pre id="__res">(.*?)</pre>', out, re.S)
    assert m and m.group(1), out[-2000:]
    import html as H
    return json.loads(H.unescape(m.group(1)))


def snap(scene, **kw):
    return {"v": 1, "scene": scene, "page": "home", "mode": "boss", "flow": "first", "flowMax": 0, "hStage": 1,
            "hSrc": "/src", "hSay": "全程：从设备最早察觉人做完那件事，到用户感觉到的最后一个结果", "pName": "x",
            "REAL": None, "dropped": [], "added": [], "ed": {}, **kw}


@needs_chrome
@pytest.mark.parametrize("where,flow_max", [("flash", 2), ("collect", 3)])
def test_refresh_mid_run_stays_on_the_run_page(tmp_path, demo, where, flow_max):
    sid = demo["/api/demo"][0]["id"]
    s = snap(sid, page=where, flowMax=flow_max)        # 老存档：没有 auto 那一项（以前刷新就丢了）
    r = page(tmp_path, demo, session={"fsw-open": "1", "fsw-ui": json.dumps(s)})
    assert r["page"] == where and r["run"] and not r["oldbar"], r
    assert r["AUTO"] is True                            # 接着自动往下走


@needs_chrome
def test_refresh_with_old_model_name_pause_carries_on(tmp_path, demo):
    # 老存档里按模型名停下的那种：接上后不再停，接着自动插计时点（还是测量页）
    sid = demo["/api/demo"][0]["id"]
    s = snap(sid, page="steps", flowMax=1, auto=True, autoHold=OLD_HOLD)
    r = page(tmp_path, demo, session={"fsw-open": "1", "fsw-ui": json.dumps(s)})
    assert r["page"] == "flash" and r["run"] and not r["hold"] and not r["autoHold"], r
    assert r["AUTO"] is True and not r["oldbar"] and not r["old"], r


def _fix_api():
    return {k: v for k, v in FIX.items() if k.startswith("/api/")}


@needs_chrome
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("how", ["refresh", "new_tab"])
def test_exact_1026_state_shows_the_new_run_page(tmp_path, demo, how, engine):
    # 10-26 11:00 那一刻：page steps、flowMax 1、hStage 1、auto、autoHold=DeepSeek；后台流程停在"找关键步骤 doing"
    ui = FIX["/api/ui"]["ui"]
    assert ui["autoHold"] == OLD_HOLD and ui["page"] == "steps" and ui["flowMax"] == 1 and ui["hStage"] == 1
    assert FIX["/api/state"]["flow"]["current"] == "identify"
    session = {"fsw-open": "1", "fsw-ui": json.dumps(ui, ensure_ascii=False)} if how == "refresh" else None
    r = page(tmp_path, demo, session=session, api_extra=_fix_api(), engine=engine)
    assert not r["oldbar"] and not r["old"] and not r["hold"], r
    if how == "new_tab":                         # 新开标签页、后台没在跑任务：从首页开始（老规矩），首页也是新版
        assert r["page"] == "home", r
        return
    assert r["run"] and r["page"] == "flash" and r["AUTO"] is True and r["flow"] == "first", r  # 核对都过了：接着插计时点


AFTER_FOUND = """autoStart();await w(50);stepsRead=2;clearInterval(readT);readT=null;
 REAL=__F['/api/ui'].ui.REAL;IDENT=__ID;render();await w(400);"""


def _found(ident):
    return f"const __F={json.dumps(FIX, ensure_ascii=False)};const __ID={json.dumps(ident, ensure_ascii=False)};" + AFTER_FOUND


@needs_chrome
def test_deepseek_alone_never_pauses(tmp_path, demo):
    r = page(tmp_path, demo, after=_found(FIX["/api/identify"]), api_extra=_fix_api())
    assert r["page"] == "flash" and r["run"] and not r["hold"] and not r["old"] and r["AUTO"] is True, r


def _bad(**chg):
    b = json.loads(json.dumps(FIX["/api/identify"]))
    for k, kv in chg.items():
        b["page"]["steps"][int(k[1:])].update(kv)
    return b


@needs_chrome
@pytest.mark.parametrize("engine", ENGINES)
def test_middle_point_problems_are_dropped_automatically(tmp_path, demo, engine):
    # 中间的点没过检查（落进分支 / 位置不对）或 AI 自己说在这块板子上什么都不做：码表自动去掉，不停
    bad = _bad(s2={"struct_ok": False, "chk": [{"level": "fail", "text": "插在只在条件成立时才走的分支里"}]},
               s4={"loc_ok": False}, s5={"note": "这块板子没有指示灯，这一步实际是空操作"})
    r = page(tmp_path, demo, after=_found(bad), api_extra=_fix_api(), engine=engine)
    ps = [bad["page"]["steps"][k]["p"] for k in (2, 4, 5)]
    assert r["page"] == "flash" and r["run"] and not r["hold"] and r["AUTO"] is True and not r["old"], r
    assert sorted(r["drop"]) == sorted(ps), r
    assert "码表自动去掉 3 个用不上的" in r["text"], r


@needs_chrome
@pytest.mark.parametrize("engine", ENGINES)
def test_start_point_problem_pauses_on_a_new_style_page(tmp_path, demo, engine):
    # 起点有问题：没它量不了，才停；停下那页是新样子，原因按真正没过的那一条说
    bad = _bad(s0={"loc_ok": False})
    r = page(tmp_path, demo, after=_found(bad), api_extra=_fix_api(), engine=engine)
    assert r["page"] == "steps" and r["hold"] and "起点" in r["autoHold"] and "行号" in r["autoHold"], r
    assert "条件分支" not in r["autoHold"], r
    assert r["run"] and r["steps"] and r["reanalyze"] and not r["oldbar"] and not r["old"] and r["AUTO"] is True, r
    r = page(tmp_path, demo, after=_found(bad) + "document.querySelector('[data-goins]').click();await w(200);",
             api_extra=_fix_api(), engine=engine)
    assert r["page"] == "flash" and r["run"] and not r["hold"] and r["AUTO"] is True and not r["oldbar"], r


@needs_chrome
def test_order_reason_is_not_called_a_branch(tmp_path, demo):
    # 终点先后说不准：原因照实说先后，不说"落进条件分支"
    pg = FIX["/api/identify"]["page"]
    end = next(k for k, x in enumerate(pg["steps"]) if x["n"] == pg["measure"].split("→")[-1].strip())
    bad = _bad(**{f"s{end}": {"struct_ok": False, "chk": [{"level": "fail", "text": "和上一步谁先谁后说不准，这一段可能是负的"}]}})
    r = page(tmp_path, demo, after=_found(bad), api_extra=_fix_api())
    assert r["hold"] and "终点" in r["autoHold"] and "说不准" in r["autoHold"] and "条件分支" not in r["autoHold"], r


@needs_chrome
def test_reanalyze_asks_for_a_fresh_read(tmp_path, demo):
    after = _found(_bad(s0={"loc_ok": False})) + """let __b=null;const __f=window.fetch;window.fetch=(u,o)=>{if(String(u).includes('/api/identify/start'))__b=o.body;return __f(u,o)};
     document.querySelector('[data-reanalyze]').click();await w(300);autoHold=__b&&JSON.parse(__b).reuse===false?'FRESH':'REUSE:'+__b;"""
    r = page(tmp_path, demo, after=after, api_extra=_fix_api())
    assert r["autoHold"] == "FRESH", r


@needs_chrome
@pytest.mark.parametrize("engine", ENGINES)
def test_refresh_during_a_real_check_pause_keeps_it(tmp_path, demo, engine):
    bad = _bad(s0={"loc_ok": False})
    ui = {**FIX["/api/ui"]["ui"], "autoHold": "有计时点的位置码表核对没通过（文件或行号不对），请核对", "autoHoldKind": "ident"}
    r = page(tmp_path, demo, session={"fsw-open": "1", "fsw-ui": json.dumps(ui, ensure_ascii=False)},
             api_extra={**_fix_api(), "/api/identify": bad}, engine=engine)
    assert r["page"] == "steps" and r["hold"] and "起点" in r["autoHold"] and "行号" in r["autoHold"], r
    assert r["run"] and r["steps"] and not r["oldbar"] and not r["old"], r


@needs_chrome
def test_fix_points_after_failed_first_round_stays_on_the_run_page(tmp_path, demo):
    # 3. 第 1 轮校验没过，点「查看并修改计时点位置」：以前 AUTO=false、进老的步骤页
    after = """autoStart();await w(50);stepsRead=2;clearInterval(readT);readT=null;flowMax=3;page='collect';col={st:4,att:1,got:[],phase:0,sc:scene};
     COLJ={status:'suspect',suspect:[{name:'上传',seconds:-0.12,why:'先后反了'}]};render();await w(50);
     document.querySelector('[data-autofix]').click();await w(100);"""
    r = page(tmp_path, demo, after=after)
    assert r["page"] == "steps" and r["run"] and r["hold"] and r["steps"] and not r["oldbar"] and r["AUTO"] is True, r


@needs_chrome
def test_new_tab_goes_home_without_the_old_step_bar(tmp_path, demo):
    r = page(tmp_path, demo)
    assert r["page"] == "home" and not r["oldbar"], r


def test_no_way_back_into_the_old_page_by_page_flow():
    assert "function runView(){if(flow!=='first')return false;" in JS            # 不看 AUTO
    assert "AUTO=false;autoHold=bad" not in JS                            # 停下核对不再关掉
    assert "provider==='deepseek'?'这次用的是" not in JS                          # 模型名不再是停下的理由（决策 181）
    k = JS.index("function runPage(")
    assert "stepsPage()" not in JS[k:JS.index("\nfunction ", k + 10)]              # 测量页里不搬老的整页核对
    assert "data-autofix]')){AUTO=false" not in JS
    assert "page='home';hStage=2" not in JS                                       # 设置页回来不再进老的确认窗口
    k = JS.index("[data-mfind]')")
    mf = JS[k:JS.index("\n", k)]
    assert "autoStart()" in mf
    snap_keys = re.search(r"return \{v:1,scene,page,(.*?)\}\}", JS).group(1)
    assert "auto:AUTO" in snap_keys and "autoHold" in snap_keys and "autoHoldKind" in snap_keys                  # 刷新接得上


def test_from_first_occurrence_is_not_an_uncertain_order():
    # 10-26 真机：后一步写明和上一步（输出）先后不确定，但段是从上一步的"第一次"（首段输出）量起的：一定在前，不该判失败、停下
    from fsw import structure
    chain = [{"n": 1, "role": ""}, {"n": 2, "role": "输出"}, {"n": 3, "role": "", "after": "和上一步谁先谁后不确定"}]
    steps = [{"id": "P1", "name": "起点", "chain_step": 1, "once": "once"},
             {"id": "P2", "name": "首段输出", "chain_step": 2, "once": "first"},
             {"id": "P3", "name": "末段输出", "chain_step": 2, "once": "last"},
             {"id": "P4", "name": "回复结束", "chain_step": 3, "once": "once"}]
    by_n = {c["n"]: c for c in chain}
    assert structure.check_uncertain(steps, [{"from": "P2", "to": "P4"}], by_n) == []
    assert structure.check_uncertain(steps, [{"from": "P3", "to": "P4"}], by_n)       # 从最后一次量起：照旧算说不准
