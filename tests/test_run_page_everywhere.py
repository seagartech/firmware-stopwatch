"""10-26 用户："所有界面显示新版本"。一次测量（flow first）的每一页都画成「测量：全程」那一页，不再靠网页内存里的
"自动执行"开关：以前它在三种情况下丢掉，页面就退回老的一页一页界面（带老步骤条）：
1. 用 DeepSeek 找完关键步骤停下请人核对；2. 刷新网页 / 新开标签页；3. 第 1 轮校验没过点「查看并修改计时点位置」。
这里用无界面的 Chrome 真跑 app.js（后台接口用假的），看页面上画的是哪一种。"""

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


def page(tmp_path, demo, *, session=None, ui=None, after="", app_js=None):
    """在 Chrome 里打开码表网页（接口全是假的），开机完成后跑 after，再把页面上画了什么交回来。"""
    body = (ROOT / "fsw/web/static/index.html").read_text(encoding="utf-8")
    body = body[body.index("<body>") + 6:body.index('<script src="/static/app.js">')]
    api = {**demo, "/api/ui": {"ui": ui, "jobs": {}, "server": None}, "/api/collect": {"status": "idle"},
           "/api/fsw": {"status": "idle"}, "/api/identify": {"state": "idle"}, "/api/history": [],
           "/api/state": {"settings": {"llm_provider": "cc", "reuse_last": True}}, "/api/env": {"idf": [], "ports": []}}
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
  steps:!!m.querySelector('[data-goins],[data-go="flash"]'),hold:/自动执行在这里停下/.test(m.innerHTML),text:m.innerText.slice(0,400)}});
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
def test_refresh_keeps_a_pause_on_the_run_page(tmp_path, demo):
    sid = demo["/api/demo"][0]["id"]
    s = snap(sid, page="steps", flowMax=1, auto=True, autoHold="这次用的是 DeepSeek，准确率较低，请核对关键步骤")
    r = page(tmp_path, demo, session={"fsw-open": "1", "fsw-ui": json.dumps(s)})
    assert r["page"] == "steps" and r["run"] and r["hold"] and r["steps"] and not r["oldbar"], r


@needs_chrome
def test_review_pause_after_finding_steps_stays_on_the_run_page(tmp_path, demo):
    # 1. 找完关键步骤要人核对（DeepSeek）：以前 AUTO=false、进老的步骤页
    after = """autoStart();await w(50);stepsRead=2;clearInterval(readT);readT=null;
     REAL={work:'/w',chip:'esp32s3',project_dir:'/p',ai:[]};IDENT={provider:'deepseek',seconds:5,page:{steps:[],measure:'a → b'}};
     render();await w(300);REAL=null;IDENT=null;render();"""
    r = page(tmp_path, demo, after=after)
    assert r["page"] == "steps" and r["hold"] and "DeepSeek" in r["autoHold"], r
    assert r["run"] and r["steps"] and not r["oldbar"] and r["AUTO"] is True, r
    # 核对完点确认：接着自动走，还是测量页
    r = page(tmp_path, demo, after=after + "document.querySelector('[data-goins]').click();await w(200);")
    assert r["page"] == "flash" and r["run"] and not r["hold"] and r["AUTO"] is True and not r["oldbar"], r


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
    assert "data-autofix]')){AUTO=false" not in JS
    assert "page='home';hStage=2" not in JS                                       # 设置页回来不再进老的确认窗口
    k = JS.index("[data-mfind]')")
    mf = JS[k:JS.index("\n", k)]
    assert "autoStart()" in mf
    snap_keys = re.search(r"return \{v:1,scene,page,(.*?)\}\}", JS).group(1)
    assert "auto:AUTO" in snap_keys and "autoHold" in snap_keys                   # 刷新接得上
