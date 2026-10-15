"""本地网页后台：给网页送页面、假数据、步骤条状态和设备输出。

这一版只用演示数据；真读源码、真编译、真串口后面逐个接进来。
"""

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fsw.config import load_config, save_config
from fsw.scenario import to_page
from fsw import identify as idf
from fsw import instrument as ins
from fsw import measure as meas
from fsw import judge as jdg
from fsw import advise as advs
from fsw import standards as stds
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


class EspDevice:
    """真设备：烧存下的固件、读调试口一段时间（乐鑫官方 esptool 和串口）。"""

    def __init__(self, idf_finder):
        self.idf_finder = idf_finder

    def flash(self, fw, port, on_progress):
        found = pick_idf(self.idf_finder(), None)
        if not found:
            return {"ok": False, "error": "没找到乐鑫编译工具，烧录要用它带的 esptool。"}
        return EspIdf(found["path"]).flash_saved(fw, port, on_progress=on_progress)

    def read(self, port, seconds, on_line):
        ser = EspIdf.open_port(port)
        try:
            return EspIdf.read_for(ser, seconds, on_line)
        finally:
            ser.close()

    def stream(self, port, on_line, stop):
        """多轮实测：一直读调试口，一行交一行，直到 stop()；拔线时串口会报错，交给上面处理。"""
        ser = EspIdf.open_port(port)
        buf = b""
        try:
            while not stop():
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    on_line(line.rstrip(b"\r").decode("utf-8", "replace"))
        finally:
            ser.close()


class CollectBody(BaseModel):
    rounds: int = 20
    demo: str | None = None          # 演示数据（没真源码时）：按它的场景算；没设备时回放它的设备输出
    page: dict | None = None         # 真找完的步骤（结果页数据）：按它的段算
    source: str | None = None        # "device"：读真设备调试口；不写：演示数据回放
    project_dir: str | None = None
    resume: bool = False             # 接着测 / 再测几轮
    interval: float = 0.12           # 回放时每行间隔（秒）
    drop_at: int = 0                 # 回放时第几次故意没收全（演示"没收全，自动补测"）


class AdviseBody(BaseModel):
    work: str
    steps: list[dict] | None = None
    what: str | None = None
    project_dir: str | None = None


class FswInsertBody(BaseModel):
    work: str
    project_dir: str
    steps: list[dict]
    chip: str | None = None
    board: str | None = None


class FswRunBody(BaseModel):
    from_: str = Field("build", alias="from")


class FswDropBody(BaseModel):
    step: str


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
    reuse: bool | None = None  # 源码没变时直接用上次的识别结果


class IdentifyBody(BaseModel):
    work: str
    what: str
    project_dir: str | None = None
    fingerprint: str | None = None
    chip: str | None = None
    reuse: bool = True  # 设置页"源码没变时，直接用上次的识别结果"


class SuggestBody(BaseModel):
    work: str
    name: str | None = None
    chip: str | None = None


class AddStepBody(BaseModel):
    work: str
    sentence: str
    what: str | None = None


class PickBody(BaseModel):
    kind: str = "dir"


TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SyncBody(BaseModel):
    reached: int
    viewing: str | None = None
    error: str | None = None
    skipped: list[str] = []


def create_app(data_dir=None, picker=None, llm_factory=None, idf_finder=None, bg_builder=None, installer_factory=None,
               config_dir=None, cc_status=None, port_finder=None, device=None, wait_step=1.0):
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
            "reuse_last": c.reuse_last,
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
                    idf_path=idf_path, data_dir=body.data_dir or None, reuse_last=body.reuse)
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
                   "seconds": None, "error": None, "idf": idf["version"], "idf_path": idf["path"],
                   "board": body.board, "target": re.sub(r"[^a-z0-9]", "", (body.chip or "esp32").lower())}
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

    # ---- 插计时点 → 确认 → 编译（只重编改过的）→ 烧录（没插就等）→ 试一次；撤掉一处；还原 ----
    device = device or EspDevice(idf_finder)
    fsw = {"job": None, "args": None, "plan": None}

    def _fsw_dirs(a):
        pd = Path(a.project_dir)
        return pd / "fsw-backup", pd / "firmware" / "orig", pd / "firmware" / "fsw"

    def _fail(job, kind, message, **extra):
        job.update(status="failed", err={"kind": kind, "message": message, **extra})

    def _wait_bg(job, work):
        """插之前等后台把原始代码编完：原版编好的固件要先存下来，还原时烧它。"""
        while True:
            b = bg["job"]
            if not b or b.get("work") != work or b["state"] != "running":
                return b
            job["log"] = f"等后台把原始代码编完再插（{b.get('pct', 0)}%），原版要先存一份"
            time.sleep(wait_step / 4)

    @app.post("/api/fsw/insert")
    def fsw_insert(body: FswInsertBody):
        _, model = _model()
        job = {"phase": "insert", "status": "running", "pct": 0, "log": "AI 在读代码，确定每个计时点插在哪一行",
               "started": time.time(), "seconds": {}, "view": None, "err": None, "got": [], "total": 0, "port": None}
        fsw.update(job=job, args=body)
        backup, orig_fw, _ = _fsw_dirs(body)
        steps = body.steps

        def run():
            ins.restore(body.work, backup)  # 上次插过的先还原
            plan_text = {}

            class Tap:  # 先拿到插入清单（大模型只读），等原版编完存好，再真正插
                def investigate(self, prompt, root, on_text=None, on_tool=None):
                    plan_text["t"] = model.investigate(prompt, root, on_text=on_text, on_tool=on_tool)
                    return plan_text["t"]
            try:
                t0 = time.time()
                text = Tap().investigate(ins.build_prompt(steps, ins.default_header(body.work)), body.work,
                                         on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
            except llms.LLMError as e:
                return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。")
            Path(body.project_dir).mkdir(parents=True, exist_ok=True)
            (Path(body.project_dir) / "fsw-plan-reply.txt").write_text(text, encoding="utf-8")  # AI 的原话留一份，出错时好查
            plan = ins.parse_plan(text, body.work, steps)
            job["seconds"]["insert"] = round(time.time() - t0, 1)
            if not plan["ok"]:
                return _fail(job, "insert", plan["message"])
            if not bg["job"] or bg["job"].get("work") != body.work:  # 码表重开过：原版再编一次（只是检查，很快）
                bgbuild_start(BgBuildBody(work=body.work, chip=body.chip, board=body.board))
            b = _wait_bg(job, body.work)
            if b and b["state"] == "done" and not orig_fw.exists():
                EspIdf.save_firmware(body.work, orig_fw)
            rec = ins.apply(body.work, plan, backup)
            if not rec["ok"]:
                return _fail(job, "insert", rec["message"])
            fsw["plan"] = plan
            job.update(status="inserted", view=ins.diff_view(rec, steps), notes=plan["notes"], log="插好了，先看改动",
                       total=len(rec["steps"]))

        threading.Thread(target=run, daemon=True).start()
        return job

    def _run_from(start):
        job, a = fsw["job"], fsw["args"]
        _, orig_fw, fsw_fw = _fsw_dirs(a)
        job.update(status="running", err=None, cancel=False)

        def build():
            job.update(phase="build", pct=0, log="准备编译")
            b = _wait_bg(job, a.work)
            if b and b["state"] == "failed":
                return _fail(job, "orig", b.get("message") or "原版编译没通过", error=b.get("error"))
            if not b or b["state"] != "done":
                return _fail(job, "orig", "原始代码还没编好，没法编计时版。回首页重新选一次源码。")
            if not orig_fw.exists():
                EspIdf.save_firmware(a.work, orig_fw)
            t0 = time.time()
            try:
                r = bg_builder(a.work, b["target"], b.get("board"), b["idf_path"],
                               lambda pct, msg: job.update(pct=pct, log=msg))
            except Exception as e:
                r = {"ok": False, "error": str(e)}
            job["seconds"]["build"] = r.get("seconds", round(time.time() - t0, 1))
            if not r.get("ok"):
                rec = ins.current(_fsw_dirs(a)[0]) or {"steps": {}}
                step = ins.locate_error(r.get("error"), rec, a.work)
                s = next((x for x in a.steps if f"P{ins.number(x['p'], a.steps)}" == step), None)
                where = f"{rec['steps'][step]['file']} 第 {rec['steps'][step]['at']} 行" if step else ""
                short = (r.get("error") or "").replace(str(Path(a.work).resolve()) + "/", "").replace(str(a.work) + "/", "")
                return _fail(job, "build", "编译没通过：是码表插的计时点引起的" if step else "编译没通过",
                             error=short, step=step, step_name=s["n"] if s else None, where=where)
            if not EspIdf.save_firmware(a.work, fsw_fw):
                return _fail(job, "build", "编译说通过了，但没找到编出来的固件文件（build/flasher_args.json）。点\"重新编译\"再试一次。")
            return True

        def flash():
            job.update(phase="flash", pct=0, log="查设备")
            while not port_finder():
                if job.get("cancel"):  # 等设备的时候点了还原：不烧了
                    return None
                job.update(status="waiting_device", log="没看到设备")
                time.sleep(wait_step)
            if job.get("cancel"):
                return None
            port = port_finder()[0]
            job.update(status="running", port=port, log=f"设备已连上 {port}")
            r = device.flash(fsw_fw, port, lambda pct, msg: job.update(pct=pct, log=msg))
            job["seconds"]["flash"] = r.get("seconds")
            if not r.get("ok"):
                return _fail(job, "burn", "烧录中断", error=r.get("error"))
            return True

        def try_once():
            job.update(phase="try", pct=0, got=[], log="等计时点")
            want = set(int(k[1:]) for k in (ins.current(_fsw_dirs(a)[0]) or {"steps": {}})["steps"])
            seen = set()

            def on_line(line):
                seen.update(ins.count_marks([line]) & want)
                job.update(got=sorted(seen), log=line[:200])
            try:
                lines = device.read(job["port"] or port_finder()[0], 15, on_line)
            except Exception as e:
                return _fail(job, "try", "试一次没收到计时点", error=f"打不开调试口：{e}")
            got = ins.count_marks(lines) & want
            job["got"] = sorted(got)
            if not got:
                return _fail(job, "try", "试一次没收到计时点", error="（15 秒内没有计时点输出）")
            job.update(status="done", log=f"试通了：收到 {len(got)} / {len(want)} 个计时点")
            return True

        order = ["build", "flash", "try"]

        def run():
            for name in order[order.index(start):]:
                if {"build": build, "flash": flash, "try": try_once}[name]() is not True:
                    return

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.post("/api/fsw/run")
    def fsw_run(body: FswRunBody):
        job = fsw["job"]
        if not job or job["status"] in ("running", "waiting_device") or (job["status"] == "failed" and job["err"]["kind"] == "insert"):
            raise HTTPException(409, "还没插好计时点，或者正在做")
        return _run_from(body.from_ if body.from_ in ("build", "flash", "try") else "build")

    @app.post("/api/fsw/drop")
    def fsw_drop(body: FswDropBody):
        """编译出错时"撤掉这处计时点，重新编译"：只撤这一处，其他的照样插着。"""
        job, a = fsw["job"], fsw["args"]
        if not job or not fsw["plan"]:
            raise HTTPException(409, "还没插好计时点")
        backup = _fsw_dirs(a)[0]
        rec = ins.drop(a.work, fsw["plan"], body.step, backup)
        fsw["plan"] = {**fsw["plan"], "inserts": [x for x in fsw["plan"]["inserts"] if x["step"] != body.step]}
        a.steps = [x for x in a.steps if f"P{ins.number(x['p'], a.steps)}" != body.step]
        job.update(view=ins.diff_view(rec, a.steps), total=len(rec["steps"]), dropped=(job.get("dropped") or []) + [body.step])
        return _run_from("build")

    # ---- 多轮实测：一轮轮收，收齐算一轮；没收全自动补；停止 / 接着测 / 从头重测；断开自动暂停 ----
    col = {"job": None, "lock": threading.Lock()}

    def _col_view(job):
        return {k: v for k, v in job.items() if not k.startswith("_")}

    @app.post("/api/collect/start")
    def collect_start(body: CollectBody):
        old = col["job"]
        if old and old["status"] in ("running", "waiting_device", "paused"):
            old["_stop"] = True
            time.sleep(0.05)
        if body.page:  # 真找完的步骤：按代码位置对上码表自带的合理范围标准
            scenario = stds.real_scenario(body.page, stds.find(body.page.get("steps") or []))
        elif body.demo in demos:
            scenario = demos[body.demo]["scenario"]
        else:
            raise HTTPException(400, "不知道要测什么：先找关键步骤")
        spec = meas.Spec.from_scenario(scenario)
        if body.resume and old:
            job, coll = old, old["_coll"]
            coll.finish()  # 停下时没收齐的那一轮不算
        else:
            base = Path(body.project_dir) if body.project_dir else live["data"] / "demo-runs"
            run = base / "runs" / time.strftime("%Y%m%d-%H%M%S") if body.project_dir else base / f"{body.demo}-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
            run.mkdir(parents=True, exist_ok=True)
            coll = meas.Collector(spec)
            job = {"rounds": [], "raw_log": str(run / "raw.log"), "need": spec.need, "_coll": coll, "_scenario": scenario,
                   "demo": None if body.page else body.demo}
            col["job"] = job
        job.update(status="running", target=body.rounds, message="", progress=[0, len(spec.need)], last="", _stop=False)
        raw = open(job["raw_log"], "a", encoding="utf-8")

        def ok_count():
            return len([r for r in job["rounds"] if r["ok"]])

        def on_line(line):
            raw.write(line + "\n")  # 设备调试口原样存一份（码表不改日志去向）
            raw.flush()
            for r in coll.feed(line):
                job["rounds"].append(r)
            job["progress"], job["last"] = list(coll.progress()), coll.last
            if ok_count() >= job["target"]:
                job["_stop"] = True

        stop = lambda: job["_stop"]

        def run_demo():
            d = demos[body.demo]
            ok0 = ok_count()
            for line in meas.demo_lines(d, max(0, job["target"] - ok0) + 1, start=ok0,
                                        drop_at=body.drop_at if not body.resume else 0):
                if stop():
                    break
                on_line(line)
                if body.interval:
                    time.sleep(body.interval)

        def run_device():
            while not stop():
                ports = port_finder()
                if not ports:
                    job.update(status="waiting_device" if job["status"] != "paused" else "paused")
                    time.sleep(wait_step)
                    continue
                job.update(status="running", message="", port=ports[0])
                try:
                    device.stream(ports[0], on_line, stop)
                except Exception as e:  # 拔线了：已测的留着，插回后接着测
                    coll.finish()
                    job["pauses"] = job.get("pauses", 0) + 1
                    job.update(status="paused", message=f"设备断开了，已测的 {ok_count()} 轮都留着；插回去会自动接着测。",
                               error=str(e))
                    time.sleep(wait_step)

        def run():
            try:
                (run_device if body.source == "device" else run_demo)()
            finally:
                raw.close()
                coll.finish()
                if job is col["job"]:
                    job["status"] = "done" if ok_count() >= job["target"] else "stopped"

        threading.Thread(target=run, daemon=True).start()
        return _col_view(job)

    @app.post("/api/collect/stop")
    def collect_stop():
        job = col["job"]
        if job:
            job["_stop"] = True
        return _col_view(job) if job else {"status": "idle"}

    def _demo_rounds(d):
        """演示数据的每一轮（没测过时报告用）。"""
        sc, res = d["scenario"], d["result"]
        n = len(next(iter(res["segments"].values())))
        out = []
        for i in range(n):
            segs = {k: v[i] for k, v in res["segments"].items()}
            ends = [round(sum(segs[k] for k in l["segments"]), 6) for l in sc["lines"]]
            out.append({"ok": True, "n": i + 1, "att": i + 1, "segs": segs, "ends": ends,
                        "probs": [k for k, rs in res["problems"].items() if i + 1 in rs]})
        return out

    @app.get("/api/report")
    def report(demo: str | None = None):
        """报告用的判断：这次收齐的轮，按固定规则算（判快慢、两把尺子、结论标签），不用大模型。
        demo=…：这套演示数据测过就用测的，没测过就用演示数据本身的数。"""
        job = col["job"]
        ok = [r for r in (job or {}).get("rounds", []) if r["ok"]]
        same = job and (demo is None or job.get("demo") == demo)
        if ok and same:
            sc, raw = job["_scenario"], job["raw_log"]
        elif demo in demos:
            sc, ok, raw = demos[demo]["scenario"], _demo_rounds(demos[demo]), None
        else:
            raise HTTPException(404, "还没有测完的轮")
        out = jdg.judge(sc, ok)
        keep = ("name", "start", "segments", "lines", "experience", "problems", "standard", "matched", "point_names")
        return {**out, "rounds": ok, "raw_log": raw, "scenario": {k: sc.get(k) for k in keep}}

    # ---- 为什么、怎么改：问题按固定规则从这次的数据算出，文字由 AI 读代码写（只读），码表核对格式 ----
    adv = {"job": None}

    @app.post("/api/advise/start")
    def advise_start(body: AdviseBody):
        job0 = col["job"]
        ok = [r for r in (job0 or {}).get("rounds", []) if r["ok"]]
        if not ok:
            raise HTTPException(409, "还没测完：先做多轮实测")
        _, model = _model()
        sc = job0["_scenario"]
        j = jdg.judge(sc, ok)
        root = body.work  # 读你原来的源码（只读）：码表那份插过计时点，行号会变
        try:
            src = json.loads((Path(body.project_dir) / "project.json").read_text(encoding="utf-8"))["source"]
            if Path(src).is_dir():
                root = src
        except (OSError, ValueError, KeyError, TypeError):
            pass
        job = {"status": "running", "started": time.time(), "read": "", "fixes": None, "fine": "", "message": "", "root": root}
        adv["job"] = job

        def run():
            save = Path(body.project_dir) / "advice" if body.project_dir else None
            try:
                r = advs.advise(model, root, j, sc, steps=body.steps, what=body.what or "", save_to=save,
                                on_progress=lambda p: job.update(read=p.get("read", "")))
            except Exception as e:
                r = {"ok": False, "kind": "other", "message": f"写建议时码表出错了：{e}"}
            job["seconds"] = r.get("seconds", round(time.time() - job["started"], 1))
            if r["ok"]:
                job.update(status="done", fixes=r["fixes"], fine=r["fine"], called=r.get("called", True))
            else:
                job.update(status="failed", kind=r.get("kind"), message=r["message"])

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.get("/api/advise")
    def advise_status():
        return adv["job"] or {"status": "idle"}

    @app.get("/api/collect")
    def collect_status():
        return _col_view(col["job"]) if col["job"] else {"status": "idle", "rounds": []}

    @app.get("/api/fsw")
    def fsw_status():
        return fsw["job"] or {"status": "idle"}

    @app.post("/api/fsw/restore")
    def fsw_restore():
        """还原成原版：源码放回去（和你原来的源码逐字节比对），设备插着就把后台编好的原版烧回去。"""
        a = fsw["args"]
        if not a:
            raise HTTPException(409, "还没插过计时点，不用还原")
        backup, orig_fw, _ = _fsw_dirs(a)
        if fsw["job"]:
            fsw["job"]["cancel"] = True  # 还在等设备烧计时版的，不烧了
        ins.restore(a.work, backup)
        try:
            source = json.loads((Path(a.project_dir) / "project.json").read_text(encoding="utf-8"))["source"]
        except (OSError, ValueError, KeyError):
            source = None
        cmp = ins.compare(a.work, source) if source and Path(source).is_dir() else {"same": None, "diff": [], "files": 0}
        out = {"same": cmp["same"], "diff": cmp["diff"][:10], "files": cmp["files"], "flashed": False, "port": None}
        ports = port_finder()
        if ports and orig_fw.exists():
            r = device.flash(orig_fw, ports[0], lambda pct, msg: None)
            out.update(flashed=bool(r.get("ok")), port=ports[0], error=r.get("error"))
        if fsw["job"]:
            fsw["job"].update(status="restored", restore=out)
        return out

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

    # ---- 找关键步骤：后台调用大模型读代码（只读），网页每秒问一次进度 ----
    ident = {"job": None, "args": None, "lock": threading.Lock()}

    def _model():
        """按已保存的设置挑大模型；没设置好就说去设置页。"""
        c = cfg()
        if not c.llm_provider:
            raise HTTPException(409, "还没设置大模型：到设置页选一个，点保存。")
        try:
            return c.llm_provider, llm_factory(c.llm_provider, None)
        except llms.LLMError as e:
            raise HTTPException(409, e.message)

    def _identify(body, known=None):
        provider, model = _model()
        job = {"state": "running", "what": body.what, "provider": provider, "found": list(known or []), "read": 0,
               "last": "", "seconds": 0, "started": time.time()}
        with ident["lock"]:
            ident["job"], ident["args"] = job, body

        def progress(p):
            job.update(found=p["found"], at=p["at"], read=p["read"], last=p["last"], seconds=p["seconds"])

        def run():
            try:
                r = idf.identify_cached(model, body.work, body.what, body.project_dir, body.fingerprint, provider,
                                        reuse=body.reuse and not known, chip=body.chip or "", known=known,
                                        on_progress=progress)
            except Exception as e:  # 码表自己出错也要说清楚，不能一直转圈
                r = {"ok": False, "kind": "other", "message": f"找关键步骤时码表出错了：{e}", "found": job["found"]}
            r.pop("reply", None)
            job.update({k: v for k, v in r.items() if k in ("page", "found", "kind", "message", "reused", "summary", "doubts")})
            job["seconds"] = r.get("seconds", round(time.time() - job["started"], 1))
            job["state"] = "done" if r.get("ok") else "failed"

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.post("/api/identify/start")
    def identify_start(body: IdentifyBody):
        if not Path(body.work).is_dir():
            raise HTTPException(400, "找不到复制好的源码，请回首页重新选一次固件源码。")
        if not body.what.strip():
            raise HTTPException(400, "先写要测从什么到什么。")
        return _identify(body)

    @app.post("/api/identify/retry")
    def identify_retry():
        job = ident["job"]
        if not job or not ident["args"]:
            raise HTTPException(409, "还没开始找")
        if job["state"] == "running":
            return job
        return _identify(ident["args"], known=job.get("found"))  # 已找到的告诉大模型，接着找

    @app.get("/api/identify")
    def identify_status():
        return ident["job"] or {"state": "idle"}

    @app.post("/api/suggest")
    def suggest(body: SuggestBody):
        """选好固件后的"AI 推荐"常用语：只给项目概况，一般十几秒。"""
        _, model = _model()
        return {"phrases": idf.suggest(model, body.work, {"name": body.name, "chip": body.chip})}

    @app.post("/api/identify/add")
    def identify_add(body: AddStepBody):
        """结果页"添加步骤 → 让 AI 找位置"。"""
        _, model = _model()
        return idf.add_step(model, body.work, body.sentence.strip(), what=body.what or "")

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

    @app.middleware("http")
    async def no_stale_page(request, call_next):
        """网页文件每次都让浏览器先问一下有没有新的，升级码表后不会还用旧代码。"""
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
