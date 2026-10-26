"""10-26 用户："所有界面显示新版本"。一次测量（flow first）的每一页都画成「测量：全程」那一页，不再靠网页内存里的
"自动执行"开关：以前它在三种情况下丢掉，页面就退回老的一页一页界面（带老步骤条）：
1. 用 DeepSeek 找完关键步骤停下请人核对；2. 刷新网页 / 新开标签页；3. 第 1 轮校验没过点「查看并修改计时点位置」。
这里用无界面的 Chrome 真跑 app.js（后台接口用假的），看页面上画的是哪一种。
10-26 11:00 又遇到："又跳到老版本界面了"：用 DeepSeek 找完就按模型名停下，测量页里塞的是老的整页"找关键步骤"
（关键流程地图、读取状态、"9 个只找对 4 个"）。决策 181：模型选择本身不要人核对，只有码表核对出实际问题才停，
停下也只在测量页里列要看的步骤。fixtures/run_page_deepseek_hold_1026.json 是那一刻的接口原样。"""

import json
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


def page(tmp_path, demo, *, session=None, ui=None, after="", app_js=None, api_extra=None):
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
  old:!!m.querySelector('.mapbox')||/关键流程地图|只找对 4 个|请每条都仔细核对|准确率较低/.test(m.innerText),
  text:m.innerText.slice(0,400)}});
}})();
</script></body></html>"""
    f = tmp_path / "h.html"
    f.write_text(html, encoding="utf-8")
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
@pytest.mark.parametrize("how", ["refresh", "new_tab"])
def test_exact_1026_state_shows_the_new_run_page(tmp_path, demo, how):
    # 10-26 11:00 那一刻：page steps、flowMax 1、hStage 1、auto、autoHold=DeepSeek；后台流程停在"找关键步骤 doing"
    ui = FIX["/api/ui"]["ui"]
    assert ui["autoHold"] == OLD_HOLD and ui["page"] == "steps" and ui["flowMax"] == 1 and ui["hStage"] == 1
    assert FIX["/api/state"]["flow"]["current"] == "identify"
    session = {"fsw-open": "1", "fsw-ui": json.dumps(ui, ensure_ascii=False)} if how == "refresh" else None
    r = page(tmp_path, demo, session=session, api_extra=_fix_api())
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


@needs_chrome
def test_real_check_failure_pauses_inside_the_new_run_page(tmp_path, demo):
    # 码表核对出实际问题（落进条件分支）才停；停下也只在测量页里列步骤，不搬老的整页
    bad = json.loads(json.dumps(FIX["/api/identify"]))
    bad["page"]["steps"][2]["struct_ok"] = False
    r = page(tmp_path, demo, after=_found(bad), api_extra=_fix_api())
    assert r["page"] == "steps" and r["hold"] and "条件分支" in r["autoHold"], r
    assert r["run"] and r["steps"] and not r["oldbar"] and not r["old"] and r["AUTO"] is True, r
    # 核对完点确认：接着自动走，还是测量页
    r = page(tmp_path, demo, after=_found(bad) + "document.querySelector('[data-goins]').click();await w(200);",
             api_extra=_fix_api())
    assert r["page"] == "flash" and r["run"] and not r["hold"] and r["AUTO"] is True and not r["oldbar"], r


@needs_chrome
def test_refresh_during_a_real_check_pause_keeps_it(tmp_path, demo):
    bad = json.loads(json.dumps(FIX["/api/identify"]))
    bad["page"]["steps"][0]["loc_ok"] = False
    ui = {**FIX["/api/ui"]["ui"], "autoHold": "有计时点的位置码表核对没通过（文件或行号不对），请核对", "autoHoldKind": "ident"}
    r = page(tmp_path, demo, session={"fsw-open": "1", "fsw-ui": json.dumps(ui, ensure_ascii=False)},
             api_extra={**_fix_api(), "/api/identify": bad})
    assert r["page"] == "steps" and r["hold"] and "位置" in r["autoHold"], r
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
