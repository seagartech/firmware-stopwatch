"""按时间顺序，用存下来的真实日志重放出报告 html（改了判断规则或报告样子后，一条命令重出，不用手点）。
用法（在 repo 目录）：uv run python tools/regen_reports.py 输出文件夹 运行名1 运行名2 …
  运行名 = 项目 runs/ 下的文件夹名，如 20261020-003935；每一份都带上它之前的真实测量，"和上次比"才对得上。
不动真数据：每份都在临时文件夹里复制项目；临时码表开在 8798；导出只写到给定的输出文件夹，
文件名和"测于"都改回原来测的时间。需要 Mac 上装了 Chrome。"""
import json, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

import uvicorn

from fsw.web.app import create_app

PROJ = Path.home() / "firmware-stopwatch-data" / "xiaozhi-899bf4"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 8798
INJECT = """<script>(async()=>{autoSave=function(){};await new Promise(r=>setTimeout(r,1500));
const pg=await (await fetch('/_page')).json();REAL={work:true,project_dir:'x'};IDENT={page:pg};FSWJ={status:'done'};mode='boss';
page='report';await fetchReport();JREP.raw_log=RAW;render();await exportReport(OUT);document.title='DONE';})()</script>"""


def one(run, out_dir):
    target = PROJ / "runs" / run
    tmp = Path(tempfile.mkdtemp())
    p = tmp / "data" / PROJ.name
    (p / "runs").mkdir(parents=True)
    shutil.copy(PROJ / "project.json", p / "project.json")
    for r in sorted((PROJ / "runs").iterdir()):
        if r.name < run and (r / "raw.log").exists() and (r / "run.json").exists():
            shutil.copytree(r, p / "runs" / r.name)
    lines = (target / "raw.log").read_text(encoding="utf-8", errors="replace").splitlines()
    pts = [(i, l.split(",")[1]) for i, l in enumerate(lines) if l.startswith("FSW,P")]
    seen, cut = set(), None
    for i, name in pts:  # 第 2 轮从第一个重复出现的计时点开始（P1 不一定最先打印，不能按第二个 P1 切）
        if name in seen:
            cut = i
            break
        seen.add(name)
    if pts and cut and not any(l.startswith("FSW,RESET") for l in lines[:cut]):  # 试一次带过来的第 1 轮缺结束标记：补上
        t = int(lines[cut].split(",")[2]) - 1000
        lines = [f"FSW,RESET,{int(lines[pts[0][0]].split(',')[2]) - 1000},0"] + lines[:cut] + [f"FSW,RESET,{t},0"] + lines[cut:]
    meta = json.loads((target / "run.json").read_text(encoding="utf-8"))

    class Dev:
        def stream(self, port, on_line, stop):
            for ln in lines:
                if stop():
                    return
                on_line(ln)
                time.sleep(0.001)
            while not stop():
                time.sleep(0.01)

    app = create_app(data_dir=tmp / "data", config_dir=tmp / "cfg", idf_finder=lambda: [],
                     cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.x"],
                     device=Dev(), wait_step=0.01, opener=lambda *a: None)
    from fastapi.responses import HTMLResponse

    @app.get("/_page")
    def _page():
        return meta["page"]

    @app.get("/_regen")
    def _regen():
        html = (Path(__import__("fsw.web.app", fromlist=["x"]).__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("</body>", f"<script>const OUT={json.dumps(str(out_dir))};const RAW={json.dumps(str(target / "raw.log"))};</script>{INJECT}</body>"))

    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    while not srv.started:
        time.sleep(0.1)
    body = {"page": meta["page"], "source": "device", "rounds": 10, "project_dir": str(p), "what": meta.get("what")}
    base = f"http://127.0.0.1:{PORT}"
    urllib.request.urlopen(urllib.request.Request(base + "/api/collect/start", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}))
    for _ in range(300):
        if json.load(urllib.request.urlopen(base + "/api/collect"))["status"] in ("done", "stopped"):
            break
        time.sleep(0.1)
    before = set(Path(out_dir).glob("*.html"))
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=20000", f"--user-data-dir={tmp / 'chrome'}",
                    "--dump-dom", base + "/_regen"], capture_output=True, timeout=120)
    new = sorted(set(Path(out_dir).glob("*.html")) - before)
    srv.should_exit = True
    th.join(5)
    shutil.rmtree(tmp, ignore_errors=True)
    if len(new) != 1:
        raise SystemExit(f"{run}：没导出成功（新文件 {len(new)} 个）")
    when = meta.get("measured_at") or run
    stamp = when.replace("-", "").replace(" ", "-").replace(":", "")
    html = new[0].read_text(encoding="utf-8")
    cur = __import__("re").search(r"测于 (\d{4}-\d\d-\d\d \d\d:\d\d)", html)
    if cur:
        html = html.replace("测于 " + cur.group(1), "测于 " + when)
    dest = new[0].with_name(f"全程_{stamp}.html")
    dest.write_text(html, encoding="utf-8")
    if dest != new[0]:
        new[0].unlink()
    print("✓", dest)


if __name__ == "__main__":
    out = Path(sys.argv[1]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for r in sys.argv[2:]:
        one(r, out)
