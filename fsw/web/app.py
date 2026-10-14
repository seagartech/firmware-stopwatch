"""本地网页后台：给网页送页面、假数据、步骤条状态和设备输出。

这一版只用演示数据；真读源码、真编译、真串口后面逐个接进来。
"""

import asyncio
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fsw.config import load_config, save_config
from fsw.scenario import to_page
from fsw import llm as llms
from fsw.workspace.picker import native_pick
from fsw.platforms.espidf import EspIdf, find_idf, find_ports, pick_idf
from fsw.platforms.espidf.install import MacInstaller, choose_version
from fsw.workspace.workspace import ascii_work_root, prepare
from fsw.web.flow import Flow, FlowError

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DEMO = HERE / "demo"


def load_demos():
    demos = []
    for p in DEMO.glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        demos.append({**d, **to_page(d)})  # 演示数据按通用格式存，换成网页要的样子
    return {d["id"]: d for d in sorted(demos, key=lambda d: d.get("order", 99))}


class StartBody(BaseModel):
    kind: str
    demo: str


class StepBody(BaseModel):
    step: str
    message: str | None = None
    skip: list[str] = []


def device_lines(demo, rounds, start=0, drop_at=0):
    """按演示数据生成设备调试口会打出来的计时点行。

    drop_at=k：在第 k 个完整轮之前插一轮只收到第一个计时点的（模拟没收全）。
    """
    S = demo["S"]
    chains = []
    for line in S["lines"]:
        chain = []
        for key in line["segs"]:
            a, b = [p.strip() for p in S["segs"][key]["code"].split("→")]
            chain.append((a, b, key))
        chains.append(chain)
    n = len(next(iter(S["segs"].values()))["v"])
    for i, r in enumerate(range(start, start + rounds)):
        base = 5_000_000 + r * 7_000_000
        marks = {}
        for chain in chains:
            t = 0.0
            marks.setdefault(chain[0][0], 0.0)
            for a, b, key in chain:
                t += S["segs"][key]["v"][r % n]
                marks.setdefault(b, t)
        ordered = sorted(marks.items(), key=lambda x: x[1])
        if drop_at and i + 1 == drop_at:
            yield "FSW,RESET"
            p, t = ordered[0]
            yield f"FSW,{p},{base - 3_000_000 + round(t * 1_000_000)},0"
        yield "FSW,RESET"
        for p, t in ordered:
            yield f"FSW,{p},{base + round(t * 1_000_000)},0"


PAGE_TO_STEP = {
    "home": "pick", "retest": "diff", "steps": "identify", "flash": "probes",
    "collect": "measure", "report": "report", "compare": "compare",
}


class SourceBody(BaseModel):
    path: str | None = None
    upload: str | None = None
    board: str | None = None


class BgBuildBody(BaseModel):
    work: str
    chip: str | None = None
    board: str | None = None
    idf_min: str | None = None


def _default_builder(work, target, board, idf_path, on_progress):
    return EspIdf(idf_path).build(work, target=target, board=board, on_progress=on_progress)


class InstallBody(BaseModel):
    idf_min: str | None = None
    dest: str | None = None
    tools: str | None = None  # 编译器等工具放哪；不写就用乐鑫默认的位置


class KeyTestBody(BaseModel):
    provider: str
    key: str | None = None


# 原型设置页里的叫法 → 码表内部的叫法
PROVIDERS = {"claude": "claude-api", "cc": "claude-code", "deepseek": "deepseek",
             "claude-api": "claude-api", "claude-code": "claude-code"}


class SettingsBody(BaseModel):
    llm_provider: str | None = None
    key: str | None = None  # 只在这次保存时用，写进密钥文件后不回传
    idf_path: str | None = None
    data_dir: str | None = None


class PickBody(BaseModel):
    kind: str = "dir"


TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SyncBody(BaseModel):
    reached: int
    viewing: str | None = None
    error: str | None = None
    skipped: list[str] = []


def create_app(data_dir=None, picker=None, llm_factory=None, idf_finder=None, bg_builder=None, installer_factory=None,
               config_dir=None, cc_status=None, port_finder=None):
    app = FastAPI(title="固件码表")
    demos = load_demos()
    cfg = lambda: load_config(config_dir)  # 设置文件夹默认 ~/.config/firmware-stopwatch/，测试时换成临时文件夹
    data_dir = Path(data_dir) if data_dir else cfg().data_dir
    live = {"data": data_dir}  # 设置页改了"测量结果放在哪"，新选的源码就放到新位置
    cc_status = cc_status or llms.claude_code_status
    port_finder = port_finder or find_ports
    uploads = ascii_work_root(data_dir) / "_uploads"
    picker = picker or native_pick
    installs_rec = data_dir / "idf-installs.json"  # 码表帮装过的编译工具，记下位置以后也能找到

    def _recorded():
        try:
            return json.loads(installs_rec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    idf_finder = idf_finder or (lambda: find_idf(extra=[cfg().idf_path, *_recorded()]))
    installer_factory = installer_factory or (lambda version, dest, tools=None: MacInstaller(dest, version, tools_path=tools))
    llm_factory = llm_factory or (lambda provider, key: llms.make(provider, key, cfg=cfg()))
    state = {"flow": None, "demo": None}

    def settings():
        """已保存的设置：只说有没有密钥，密钥本身不回传给网页。"""
        c = cfg()
        return {
            "llm_provider": c.llm_provider,
            "has_key": {"claude-api": bool(c.claude_api_key), "deepseek": bool(c.deepseek_api_key)},
            "idf_path": c.idf_path or "",
            "serial_port": c.serial_port or "",
            "data_dir": str(live["data"]),
        }

    def snapshot():
        flow = state["flow"]
        return {
            "flow": flow.to_dict() if flow else None,
            "demo": state["demo"],
            "settings": settings(),
            "viewing": flow.viewing if flow else None,
            "current": flow.current if flow else None,
            "steps": flow.to_dict()["steps"] if flow else [],
            "notice": flow.notice if flow else None,
            "error": flow.error if flow else None,
        }

    @app.get("/")
    def index():
        """网页文件地址带上版本号（按文件修改时间），升级后浏览器一定拿新的。"""
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "style.css"):
            v = format(int((STATIC / name).stat().st_mtime), "x")
            html = html.replace(f"/static/{name}", f"/static/{name}?v={v}")
        return HTMLResponse(html)

    @app.get("/api/demo")
    def demo_list():
        return [{"id": k, "label": d["label"]} for k, d in demos.items()]

    @app.get("/api/demo/{sid}")
    def demo_get(sid: str):
        if sid not in demos:
            raise HTTPException(404, "没有这套演示数据")
        return demos[sid]

    @app.get("/api/state")
    def get_state():
        return snapshot()

    @app.post("/api/flow/start")
    def flow_start(body: StartBody):
        if body.demo not in demos:
            raise HTTPException(404, "没有这套演示数据")
        try:
            state["flow"] = Flow.start(body.kind)
        except FlowError as e:
            raise HTTPException(409, str(e))
        state["demo"] = body.demo
        return snapshot()

    @app.post("/api/flow/sync")
    def flow_sync(body: SyncBody):
        flow = state["flow"]
        if flow is None:
            raise HTTPException(409, "还没开始测量")
        viewing = PAGE_TO_STEP.get(body.viewing, body.viewing)
        keys = [s.key for s in flow.steps]
        try:
            flow.sync(
                min(body.reached, len(keys) - 1),
                viewing=viewing if viewing in keys else None,
                error=body.error,
                skipped=[PAGE_TO_STEP.get(k, k) for k in body.skipped],
            )
        except FlowError as e:
            raise HTTPException(409, str(e))
        return snapshot()

    @app.post("/api/flow/{action}")
    def flow_action(action: str, body: StepBody):
        flow = state["flow"]
        if flow is None:
            raise HTTPException(409, "还没开始测量")
        try:
            if action == "view":
                flow.view(body.step)
            elif action == "complete":
                flow.complete(body.step, skip=body.skip)
            elif action == "change":
                flow.change(body.step)
            elif action == "fail":
                flow.fail(body.step, body.message or "出错了")
            elif action == "retry":
                flow.retry(body.step)
            else:
                raise HTTPException(404, "没有这个操作")
        except FlowError as e:
            raise HTTPException(409, str(e))
        return snapshot()

    @app.post("/api/settings")
    def save_settings(body: SettingsBody):
        """设置页"保存"：真写进设置文件夹（密钥单独一个只有自己能读的文件），重开码表还在。"""
        provider = PROVIDERS.get(body.llm_provider, "?") if body.llm_provider else None
        if provider == "?":
            raise HTTPException(400, "不认识这个大模型：只能选 Claude、DeepSeek 或本机的 Claude Code。")
        idf_path = body.idf_path.strip() if body.idf_path else None
        if idf_path:
            want = Path(idf_path).expanduser().resolve()
            if not any(Path(i["path"]) == want for i in find_idf(home="/nonexistent", env={}, extra=[idf_path])):
                raise HTTPException(400, f"这个文件夹里没找到乐鑫编译工具（没有 tools/idf.py）：{idf_path}。请选 esp-idf 那一层文件夹。")
        save_config(config_dir or load_config().config_dir, provider=provider, key=(body.key or "").strip() or None,
                    idf_path=idf_path, data_dir=body.data_dir or None)
        if body.data_dir:
            live["data"] = Path(body.data_dir).expanduser()
        return snapshot()

    # ---- 后台编原始代码：点"开始找关键步骤"时开始，和找步骤同时跑，互不影响 ----
    bg_builder = bg_builder or _default_builder
    bg = {"job": None, "lock": threading.Lock()}

    @app.post("/api/bgbuild/start")
    def bgbuild_start(body: BgBuildBody):
        with bg["lock"]:
            job = bg["job"]
            if job and job["work"] == body.work and job["state"] in ("running", "done"):
                return job  # 同一份原始代码已经在编或编好了，不重复编
            idf = pick_idf(idf_finder(), body.idf_min)
            if not idf:
                bg["job"] = {"work": body.work, "state": "waiting_tool", "pct": 0,
                             "message": "等编译工具装好就开始编；不挡找关键步骤，烧录前装好就行。"}
                return bg["job"]
            job = {"work": body.work, "state": "running", "pct": 0, "message": "准备编译", "started": time.time(),
                   "seconds": None, "error": None, "idf": idf["version"]}
            bg["job"] = job
        target = re.sub(r"[^a-z0-9]", "", (body.chip or "esp32").lower())

        def progress(pct, msg):
            job["pct"], job["message"] = pct, msg

        def run():
            try:
                r = bg_builder(body.work, target, body.board, idf["path"], progress)
            except Exception as e:  # 编译工具本身出问题也要说清楚，不能一直转圈
                r = {"ok": False, "seconds": round(time.time() - job["started"], 1), "error": str(e)}
            job["seconds"] = r.get("seconds")
            if r.get("ok"):
                job.update(state="done", pct=100, message="原始代码已编好")
            else:
                job.update(state="failed", error=r.get("error"),
                           message="原版编译没通过，不是码表改的：你选的源码本身在这台电脑上编不过"
                                   "（编译工具、依赖或板子配置的问题），烧录前要先解决。")

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.get("/api/bgbuild")
    def bgbuild_status():
        return bg["job"] or {"state": "idle", "pct": 0}

    # ---- 编译工具帮装（决策 48）：后台跑乐鑫官方安装方式，点"保存"回首页也照样在装 ----
    inst = {"job": None, "args": None}

    def _install(version, dest, tools=None):
        job = {"state": "running", "step": 0, "pct": 0, "log": "", "message": "", "version": version,
               "path": str(dest), "started": time.time()}
        inst["job"], inst["args"] = job, (version, dest, tools)

        def event(e):
            job.update({k: v for k, v in e.items() if k in ("step", "pct", "log")})

        def run():
            try:
                r = installer_factory(version, dest, tools).run(on_event=event)
            except Exception as e:
                r = {"ok": False, "step": job["step"], "message": f"安装中途出错：{e}", "log": str(e)}
            job["seconds"] = round(time.time() - job["started"], 1)
            if r.get("ok"):
                job.update(state="done", pct=100, message="✓ 编译工具装好了")
                rec = _recorded()
                if str(dest) not in rec:
                    installs_rec.parent.mkdir(parents=True, exist_ok=True)
                    installs_rec.write_text(json.dumps(rec + [str(dest)], ensure_ascii=False), encoding="utf-8")
            else:
                job.update(state="failed", step=r.get("step", job["step"]), message=r.get("message", ""),
                           log=r.get("log") or job["log"])

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.post("/api/install/start")
    def install_start(body: InstallBody):
        if inst["job"] and inst["job"]["state"] == "running":
            return inst["job"]
        version = choose_version(body.idf_min)
        if not version:
            raise HTTPException(409, f"码表还不认识 ESP-IDF {body.idf_min} 这么新的版本，请照手动安装步骤自己装")
        dest = Path(body.dest).expanduser() if body.dest else Path.home() / "esp" / f"esp-idf-{version}"
        return _install(version, dest, Path(body.tools).expanduser() if body.tools else None)

    @app.post("/api/install/retry")
    def install_retry():
        if not inst["args"]:
            raise HTTPException(409, "还没开始装")
        if inst["job"]["state"] == "running":
            return inst["job"]
        return _install(*inst["args"])  # 已下好的不重下，从断的地方接着装

    @app.get("/api/install")
    def install_status():
        return inst["job"] or {"state": "idle"}

    @app.get("/api/env")
    def env_status(cc: bool = True):
        """电脑上的环境：装了哪些乐鑫编译工具、本机 Claude Code 登没登录、插着哪些设备。都不用大模型、不花钱。"""
        return {"idf": idf_finder(), "claude_code": cc_status() if cc else None, "ports": port_finder()}

    @app.get("/api/ports")
    def ports():
        """插着的设备（USB 转串口）：设置页、首页灯、烧录前等设备时每隔几秒问一次。"""
        return {"ports": port_finder()}

    @app.post("/api/llm/test")
    def llm_test(body: KeyTestBody):
        """设置页的"测试密钥"：真问一句最短的话，分清是密钥错、没余额还是连不上。密钥只用这一次，不存、不打印。"""
        try:
            model = llm_factory(PROVIDERS.get(body.provider, body.provider), (body.key or "").strip() or None)
            return model.test()
        except llms.LLMError as e:
            return {"ok": False, "kind": e.kind, "message": e.message}

    @app.post("/api/source/pick")
    def source_pick(body: PickBody):
        """选文件夹 / 选压缩包：弹出系统自带的选择窗口。"""
        return {"path": picker("zip" if body.kind == "zip" else "dir")}

    @app.put("/api/source/upload/{token}/{rel:path}")
    async def source_upload(token: str, rel: str, request: Request):
        """拖进来的文件夹或压缩包：网页一个个文件传过来，先放进码表工作区。"""
        parts = Path(rel).parts
        if not TOKEN.match(token) or not parts or Path(rel).is_absolute() or ".." in parts:
            raise HTTPException(400, "文件路径不对")
        dest = uploads / token / Path(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await request.body())
        return {"ok": True}

    @app.post("/api/source/inspect")
    def source_inspect(body: SourceBody):
        """选好源码：认出是什么固件，复制到纯英文路径，建码表项目文件夹。"""
        if body.upload:
            if not TOKEN.match(body.upload) or not (uploads / body.upload).is_dir():
                raise HTTPException(400, "没收到拖进来的文件")
            base = uploads / body.upload
            entries = list(base.iterdir())
            path = entries[0] if len(entries) == 1 else base
        elif body.path:
            path = Path(body.path.strip()).expanduser()
        else:
            raise HTTPException(400, "先选一个固件源码位置")
        unpack = ascii_work_root(live["data"]) / "_unpack" / hashlib.sha1(str(path).encode()).hexdigest()[:10]
        return prepare(path, data_dir=live["data"], board=body.board, unpack_to=unpack)

    @app.get("/api/device/stream")
    async def device_stream(
        demo: str, rounds: int = 1, start: int = 0, drop_at: int = 0, interval: float = 0.05
    ):
        if demo not in demos:
            raise HTTPException(404, "没有这套演示数据")

        async def gen():
            for line in device_lines(demos[demo], rounds, start, drop_at):
                yield f"data: {line}\n\n"
                if interval:
                    await asyncio.sleep(interval)
            yield "data: END\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.middleware("http")
    async def no_stale_page(request, call_next):
        """网页文件每次都让浏览器先问一下有没有新的，升级码表后不会还用旧代码。"""
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
