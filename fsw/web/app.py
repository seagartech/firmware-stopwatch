"""本地网页后台：给网页送页面、假数据、步骤条状态和设备输出。

这一版只用演示数据；真读源码、真编译、真串口后面逐个接进来。
"""

import hashlib
import os
import json
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fsw.config import load_config, save_config
from fsw.scenario import to_page
from fsw import identify as idf
from fsw import instrument as ins
from fsw import measure as meas
from fsw import judge as jdg
from fsw import advise as advs
from fsw import timeline as tln
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

    def read(self, port, seconds, on_line, stop=None):
        ser = EspIdf.open_port(port)
        try:
            return EspIdf.read_for(ser, seconds, on_line, stop=stop)
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
    what: str | None = None          # 你自己写的"测什么"（首页"以前测过的"和导出文件名用）


class PathBody(BaseModel):
    path: str


class ExportBody(BaseModel):
    html: str
    what: str = "报告"
    demo: str | None = None
    folder: str | None = None        # 存到哪（不写就用默认：被测源码的同级目录）


def _open_in_finder(path):
    """在 Finder / 资源管理器里打开文件所在的文件夹，并选中它。"""
    import subprocess
    import sys
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", path])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


def _open_file(path):
    """用默认程序打开文件（导出的报告是网页，会用浏览器打开）。"""
    import subprocess
    import sys
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(["xdg-open", path])


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|[\x00-\x08\x0b-\x1f\x7f]")


def _plain(text):
    """工具输出里控制颜色、进度条的字符去掉（决策 106）：网页上原样显示就是乱码。"""
    return ANSI.sub("", str(text or "")).replace("\r", "").strip()


def _log(job, msg, pct=None, show=None):
    """编译、烧录、试一次的输出：去掉控制字符，留最近 200 行给"完整日志"；页面上那一行可以换成人话。"""
    m = _plain(msg)
    if m:
        job["tail"] = (job.get("tail") or [])[-199:] + [m]
    upd = {"log": show or m or job.get("log", "")}
    if pct is not None:
        upd["pct"] = pct
    job.update(upd)


def _overall(fw_dir):
    """烧录工具按段写（引导程序、分区表、程序……），每段各自从 0 到 100%：合成一条只往前走的进度（决策 114）。"""
    try:
        n = max(1, len(json.loads((Path(fw_dir) / "flasher_args.json").read_text(encoding="utf-8")).get("flash_files") or {}))
    except (OSError, ValueError):
        n = 1
    st = {"seg": 0, "last": 0, "out": 0}

    def step(pct):
        if pct < st["last"] - 5 and st["seg"] < n - 1:  # 新的一段开始了
            st["seg"] += 1
        st["last"] = pct
        st["out"] = max(st["out"], min(100, int((st["seg"] * 100 + pct) / n)))
        return st["out"]
    return step


def _garbled(line):
    """一行像不像乱码（波特率不对时设备输出就是这样）。"""
    if not line:
        return False
    bad = sum(1 for ch in line if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t"))
    return bad / len(line) > 0.3


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


NO_DATA_SECS = 20  # 多轮实测：调试口开着，这么久没收到计时点就说人话提示（不停下，做了就接着收）


class RestoreBody(BaseModel):
    project_dir: str | None = None   # 码表重开过：按项目文件夹里存的插入记录还原


TRY_SECONDS = 180  # 试一次最多等多久（决策 107）：烧完就开始听，设备重启、连网、人做一次要测的事都在这里面；不用人判断"准备好了"
REOPEN_SECS = 8   # 试一次：打开调试口后这么久没有任何输出就重开（设备烧完重启，调试口会断开再回来）
TRY_SETTLE = 4     # 收到一些计时点后，几秒没有新的就算试通


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
    project_dir: str | None = None   # 源码没变（指纹一样）就用上次的推荐，不再调用大模型
    fingerprint: str | None = None


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
               config_dir=None, cc_status=None, port_finder=None, device=None, wait_step=1.0, opener=None):
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
        return [{"id": k, "label": d["label"], "hidden": bool(d.get("hidden"))} for k, d in demos.items()]

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

    # 码表自己记每段用时（决策 99）：选源码时开一份，存在项目文件夹的 timeline.json；测完也放一份进这次的结果文件夹
    tline = {"cur": tln.Timeline()}

    def T():
        return tline["cur"]

    def _fail(job, kind, message, **extra):
        job.update(status="failed", err={"kind": kind, "message": message, **extra})

    def _on_device(project_dir, fsw_on):
        """设备上现在是计时版还是原版（存在项目文件夹里，码表重开也认得）。"""
        (Path(project_dir) / "on-device.json").write_text(json.dumps({"fsw": fsw_on, "at": time.strftime("%Y-%m-%d %H:%M")}),
                                                          encoding="utf-8")

    def _wait_bg(job, work):
        """插之前等后台把原始代码编完：原版编好的固件要先存下来，还原时烧它。"""
        while True:
            b = bg["job"]
            if not b or b.get("work") != work or b["state"] != "running":
                return b
            job["log"] = f"等后台把原始代码编完再插（{b.get('pct', 0)}%），原版要先存一份"
            time.sleep(wait_step / 4)

    def _plan_cache(body):
        """源码和步骤都没变（比如码表重开过）：用上次的插入方案，不再调用大模型；设置里关掉"用上次结果"就不沿用。"""
        proj = Path(body.project_dir)
        try:
            fp = json.loads((proj / "project.json").read_text(encoding="utf-8")).get("fingerprint")
        except (OSError, ValueError):
            fp = None
        try:
            last = json.loads((proj / "fsw-plan.json").read_text(encoding="utf-8"))
            cached = last["reply"] if (fp and cfg().reuse_last and last.get("fingerprint") == fp
                                       and last.get("steps") == body.steps and last.get("ver") == ins.PLAN_VER) else None
        except (OSError, ValueError, KeyError):
            cached = None
        return fp, cached

    def _prep_view(p):
        return {k: v for k, v in p.items() if k not in ("text", "work", "steps")}

    # 插计时点提前（决策 96）：找完关键步骤、人核对步骤时，后台先按这些步骤生成插法（大模型只读）；
    # 人没改步骤，点"确认"就直接用；改了就作废、按新步骤重新生成
    @app.post("/api/fsw/prepare")
    def fsw_prepare(body: FswInsertBody):
        if _plan_cache(body)[1]:
            return {"status": "reuse"}
        backup, _, _ = _fsw_dirs(body)
        if ins.current(backup):  # 这份代码上已经插过（改了步骤要重插）：等点确认、先还原再生成，免得行号对不上
            return {"status": "skip"}
        p = fsw.get("prep")
        if p and p["work"] == body.work and p["steps"] == body.steps and p["status"] in ("running", "ready"):
            return _prep_view(p)
        model = _model()[1]
        p = {"status": "running", "work": body.work, "steps": body.steps, "started": time.time(), "text": None, "seconds": None,
             "read": ""}
        fsw["prep"] = p

        def run():
            needs = stds.needed_values(body.steps, stds.find(body.steps))
            try:
                text = model.investigate(ins.build_prompt(body.steps, ins.default_header(body.work), needs), body.work,
                                         on_tool=lambda d: p.update(read=d))
                p.update(status="ready", text=text)
            except Exception as e:
                p.update(status="failed", message=str(getattr(e, "message", e)))
            p["seconds"] = round(time.time() - p["started"], 1)
        threading.Thread(target=run, daemon=True).start()
        return _prep_view(p)

    @app.get("/api/fsw/prepare")
    def fsw_prepare_status():
        p = fsw.get("prep")
        return _prep_view(p) if p else {"status": "idle"}

    @app.post("/api/fsw/insert")
    def fsw_insert(body: FswInsertBody):
        proj = Path(body.project_dir)
        fp, cached = _plan_cache(body)
        prep, use_prep, redo = fsw.get("prep"), False, None
        if not cached and prep and prep["work"] == body.work and prep["status"] in ("running", "ready", "failed"):
            if prep["steps"] == body.steps and prep["status"] != "failed":
                use_prep = True
            elif prep["steps"] != body.steps:
                redo = "你改了步骤，AI 提前按原来的步骤准备的插法作废了，按你改过的步骤重新生成"
        fsw["prep"] = None
        T().end("check")
        T().begin("insert")
        T().count("clicks")
        calls = 1 if prep and not cached and prep["work"] == body.work else 0  # 提前准备的那次也算一次调用
        model = None if cached or use_prep else _model()[1]
        job = {"phase": "insert", "status": "running", "pct": 0, "log": redo or "AI 在读代码，确定每个计时点插在哪一行",
               "started": time.time(), "seconds": {}, "view": None, "err": None, "got": [], "total": 0, "port": None,
               "prepared": use_prep, "redo": redo, "calls": calls}
        fsw.update(job=job, args=body)
        backup, orig_fw, _ = _fsw_dirs(body)
        steps = body.steps
        Path(body.project_dir).mkdir(parents=True, exist_ok=True)
        (Path(body.project_dir) / "fsw-args.json").write_text(json.dumps(body.model_dump(), ensure_ascii=False), encoding="utf-8")

        def run():
            ins.restore(body.work, backup)  # 上次插过的先还原
            plan_text = {}

            class Tap:  # 先拿到插入清单（大模型只读），等原版编完存好，再真正插
                def investigate(self, prompt, root, on_text=None, on_tool=None):
                    plan_text["t"] = model.investigate(prompt, root, on_text=on_text, on_tool=on_tool)
                    return plan_text["t"]
            t0 = time.time()
            needs = stds.needed_values(steps, stds.find(steps))  # 问题规则要看附加值的计时点：让 AI 顺带记上
            if cached:
                text = cached
                job.update(reused=True, log="源码和步骤都没变，用上次的插入方案，没调用大模型")
            elif use_prep:
                while prep["status"] == "running":
                    job["log"] = f"你核对步骤时 AI 已经在后台按这些步骤准备插法，还差一点（已准备 {int(time.time() - prep['started'])} 秒）"
                    time.sleep(wait_step / 4)
                if prep["status"] == "ready":
                    text = prep["text"]
                    job["log"] = "用你核对步骤时 AI 提前准备好的插法"
                else:
                    job.update(prepared=False, log="提前准备的那次没成，重新生成")
                    try:
                        job["calls"] += 1
                        text = _model()[1].investigate(ins.build_prompt(steps, ins.default_header(body.work), needs), body.work,
                                                       on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
                    except llms.LLMError as e:
                        return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。")
            else:
                job["calls"] += 1
                try:
                    text = Tap().investigate(ins.build_prompt(steps, ins.default_header(body.work), needs), body.work,
                                             on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
                except llms.LLMError as e:
                    return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。")
            Path(body.project_dir).mkdir(parents=True, exist_ok=True)
            (Path(body.project_dir) / "fsw-plan-reply.txt").write_text(text, encoding="utf-8")  # AI 的原话留一份，出错时好查
            plan = ins.parse_plan(text, body.work, steps, needs)
            job["seconds"]["insert"] = round(time.time() - t0, 1)
            if plan["ok"] and fp:
                (proj / "fsw-plan.json").write_text(json.dumps({"fingerprint": fp, "steps": steps, "reply": text, "ver": ins.PLAN_VER},
                                                               ensure_ascii=False), encoding="utf-8")
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
            T().end("insert", reused=bool(cached), prepared=bool(job.get("prepared")))
            T().count("llm_calls", job.get("calls", 0))
            T().begin("confirm")

        threading.Thread(target=run, daemon=True).start()
        return job

    def _run_from(start):
        job, a = fsw["job"], fsw["args"]
        _, orig_fw, fsw_fw = _fsw_dirs(a)
        job.update(status="running", err=None, cancel=False)

        def build():
            T().end("confirm")
            T().begin("build")
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
                               lambda pct, msg: _log(job, msg, pct))
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
            T().end("build")
            return True

        def flash():
            job.update(phase="flash", pct=0, log="查设备")
            T().begin("flash")
            w0 = time.time() if not port_finder() else None
            while not port_finder():
                if job.get("cancel"):  # 等设备的时候点了还原：不烧了
                    return None
                job.update(status="waiting_device", log="没看到设备")
                time.sleep(wait_step)
            if job.get("cancel"):
                return None
            if w0:
                T().wait("flash", time.time() - w0, "等设备插上")
            port = port_finder()[0]
            job.update(status="running", port=port, log=f"设备已连上 {port}")
            ov = _overall(fsw_fw)
            r = device.flash(fsw_fw, port, lambda pct, msg: (lambda o: _log(job, msg, o, f"正在写进设备：{o}%"))(ov(pct)))
            job["seconds"]["flash"] = r.get("seconds")
            if not r.get("ok"):
                return _fail(job, "burn", "烧录中断", error=r.get("error"))
            _on_device(a.project_dir, True)
            T().end("flash")
            return True

        def ready():
            """烧完先停下：设备会重启、连网，人要先喊唤醒词再说一句，等点了"准备好了"才开始试一次。"""
            job.update(phase="try", status="ready_try", pct=0, got=[], log="等你准备好：先喊唤醒词，再说一句", ready_at=time.time())
            return None

        def try_once():
            if job.get("ready_at"):  # 码表重开前停在"准备好了"的老状态
                T().wait("ready", time.time() - job.pop("ready_at"), "等你准备好、点「准备好了」")
            if "try" not in T().data["stages"]:
                T().begin("try")
            T().count("try_said")
            T().count("clicks")
            job.update(phase="try", status="running", pct=0, got=[], log="等设备开机", try_secs=TRY_SECONDS,
                       try_started=time.time(), dev_seen=None)
            want = set(int(k[1:]) for k in (ins.current(_fsw_dirs(a)[0]) or {"steps": {}})["steps"])
            need_n = max(1, len(want) - min(2, len(want) // 4))  # 差不多齐了才算试通：9 个至少 7 个，3 个要全到
            seen, last = set(), {"t": None}

            def on_line(line):
                if not job.get("dev_seen") and _plain(line):
                    job["dev_seen"] = time.time()  # 设备开机、有输出了：页面上从"设备正在重启"换成"现在做一次要测的事"
                new = ins.count_marks([line]) & want
                if new - seen:
                    last["t"] = time.time()
                seen.update(new)
                job["got"] = sorted(seen)
                _log(job, line[:300])

            def enough():  # 都收到了；或者差不多齐了（最多差 2 个，有的点只在某些情况下才走到）、之后几秒再没有新的，就算试通
                return seen == want or (len(seen) >= need_n and last["t"] and time.time() - last["t"] > TRY_SETTLE)
            t_end, lines, err = time.time() + TRY_SECONDS, None, None
            while lines is None and time.time() < t_end:  # 烧完设备重启，调试口会断开一下再回来：等它回来再读
                ports = port_finder()
                if not ports:
                    time.sleep(wait_step / 4)
                    continue
                opened, reopen = time.time(), {"hit": False}

                def stop_or_reopen():  # 决策 127：打开调试口 8 秒还一行输出都没有，多半打开的是设备重启前那个（它断开又回来了）：关掉重开
                    if not job.get("dev_seen") and time.time() - opened > REOPEN_SECS:
                        reopen["hit"] = True
                    return enough() or reopen["hit"]
                try:
                    got_lines = device.read(ports[0], max(1, t_end - time.time()), on_line, stop=stop_or_reopen)
                    if not reopen["hit"] or time.time() >= t_end:
                        lines = got_lines
                    else:
                        job["log"] = "设备还没有输出，重新连一下调试口"
                        time.sleep(wait_step)
                except Exception as e:
                    err = e
                    time.sleep(wait_step)
            if lines is None:
                return _fail(job, "try", "设备的调试口一直打不开。看看线插好没有，或者有没有别的软件（比如串口监视器）占着它。",
                             error=f"打不开调试口：{err}" if err else "没看到设备")
            got = ins.count_marks(lines) & want
            job["got"] = sorted(got)
            if got and len(got) < need_n:  # 只收到零星几个：不算试通（决策 124）
                job["try_fails"] = job.get("try_fails", 0) + 1
                return _fail(job, "try", f"只收到 {len(got)} / {len(want)} 个计时点，这一次没走完。点「再试一次」，然后完整做一次要测的事。",
                             why="可能刚才只唤醒了设备、还没说那句话，或者设备还没准备好。", error=f"（收到 {len(got)} / {len(want)} 个）",
                             offer_restore=job["try_fails"] >= 2)
            if not got:
                job["try_fails"] = job.get("try_fails", 0) + 1
                odd = sum(1 for l in lines if _garbled(l))
                why = ("收到的都是乱码，多半是波特率不对（码表按 115200 读）。" if lines and odd >= len(lines) / 2 else
                       "常见原因：没触发到；串口被别的软件占着（比如串口监视器）；波特率不对（码表按 115200 读）。")
                return _fail(job, "try", f"{TRY_SECONDS // 60} 分钟里没收到计时点。点「再试一次」，然后做一次要测的事。", why=why,
                             error=f"（{TRY_SECONDS} 秒内没有计时点输出）", offer_restore=job["try_fails"] >= 2)
            job.update(status="done", log=f"试通了：收到 {len(got)} / {len(want)} 个计时点")
            T().end("try")
            return True

        order = ["build", "flash", "try"] if start != "try" else ["try"]  # 烧完直接试一次，不再停下等人点"准备好了"

        def run():
            for name in order[order.index(start) if start in order else 0:]:
                if {"build": build, "flash": flash, "ready": ready, "try": try_once}[name]() is not True:
                    return

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.post("/api/fsw/run")
    def fsw_run(body: FswRunBody):
        job = fsw["job"]
        if not job or job["status"] in ("running", "waiting_device") or (job["status"] == "failed" and job["err"]["kind"] == "insert") \
                or (body.from_ == "try" and not job.get("port") and not port_finder()):
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
        if not 3 <= body.rounds <= 100:
            raise HTTPException(400, "测几轮要在 3～100 轮之间：3～9 轮适合跑流程，正式测建议 20 轮以上。")
        old = col["job"]
        if old and old["status"] in ("running", "waiting_device", "paused"):
            old["_stop"] = True
            time.sleep(0.05)
        std_hash = None
        values = None
        if body.page:  # 真找完的步骤：按代码位置对上码表自带的合理范围标准
            standard = stds.find(body.page.get("steps") or [])
            if body.project_dir:  # 插计时点时顺带记了附加值的计时点（插入记录里），看附加值的问题规则才用
                values = (ins.current(Path(body.project_dir) / "fsw-backup") or {}).get("values") or None
            scenario = stds.real_scenario(body.page, standard, values)
            std_hash = stds.digest(standard)
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
            if body.page:  # 这次测的是什么，留一份：首页"以前测过的"按它重算
                (run / "run.json").write_text(json.dumps({"page": body.page, "values": values, "what": body.what or body.page.get("mapWhat"),
                                                          "measured_at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False), encoding="utf-8")
            coll = meas.Collector(spec)
            job = {"rounds": [], "raw_log": str(run / "raw.log"), "need": spec.need, "_coll": coll, "_scenario": scenario,
                   "demo": None if body.page else body.demo, "_page": body.page, "_std": std_hash, "_values": values,
                   "measured_at": time.strftime("%Y-%m-%d %H:%M"),
                   # 这次真的场景：网页多轮实测页按它画线、标问题，不借演示数据
                   "lines": [{"name": l["name"], "range": l.get("range"), "slow": l.get("slow")} for l in scenario["lines"]],
                   "problems": [{"key": p["key"], "name": p.get("name", p["key"]), "level": p.get("level", "warn")}
                                for p in scenario.get("problems") or []],
                   "experience": scenario.get("experience")}
            col["job"] = job
        job.update(status="running", target=body.rounds, message="", progress=[0, len(spec.need)], last="", _stop=False)
        raw = open(job["raw_log"], "a", encoding="utf-8")
        T().count("clicks")
        if body.source == "device":
            if not body.resume:
                T().begin("collect")
            T().copies = [Path(job["raw_log"]).parent / "timeline.json"]  # 这次的结果文件夹里也放一份

        def ok_count():
            return len([r for r in job["rounds"] if r["ok"]])

        quiet = {"since": time.time(), "lines": 0, "odd": 0, "any": False}  # 上一个计时点以来：多久、几行别的、几行乱码；收到过计时点没有

        def on_line(line):
            raw.write(line + "\n")  # 设备调试口原样存一份（码表不改日志去向）
            raw.flush()
            if meas.parse_line(line):
                quiet.update(since=time.time(), lines=0, odd=0, any=True)
                job["hint"] = ""
            else:
                quiet["lines"] += 1
                quiet["odd"] += _garbled(line)
            for r in coll.feed(line):
                if r.get("half"):  # 开始读时这一次已经过了起点：不算，提示再做一次（决策 101）
                    job["half"] = job.get("half", 0) + 1
                    job["half_hint"] = ("这一次开头没收到（码表开始听的时候它已经开始了），不算。"
                                        "刚才要是在做要测的事，再做一次；只是唤醒设备的话，不用管。")
                    continue
                if r["ok"]:
                    job["half_hint"] = ""
                    job["stale_run"] = {}
                elif r.get("stale"):
                    run = job.setdefault("stale_run", {})
                    for m in r["missing"]:
                        run[m] = run.get(m, 0) + 1
                    worst = max(run.items(), key=lambda kv: kv[1])
                    if worst[1] >= 2:
                        name = worst[0].split("（")[0]
                        job["half_hint"] = (f"「{name}」连着 {worst[1]} 轮都记在你说完之前，不像是这一轮的事，再测也一样。"
                                            "多半是这个计时点插的位置不对：先停下，回到「插计时点」那一步换个位置，或者删掉它。")
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

        def watch_quiet():
            """调试口开着却一直没有计时点：不停下，说人话提示是哪种情况（做了 / 插好就接着收）。"""
            while not stop() and job is col["job"]:
                mid = coll.started and coll.cur and not coll.done  # 一轮做到一半
                waiting = quiet["any"] and not mid  # 收齐一轮、等你做下一次：两次之间正常停顿，不算出错
                if job["status"] == "running" and not waiting and time.time() - quiet["since"] > NO_DATA_SECS:
                    secs = int(time.time() - quiet["since"])
                    if not job.get("hint"):
                        job["hints"] = job.get("hints", 0) + 1  # 弹了几次提示（人配合那笔账里算"被打断几次"）
                    job["hint"] = (f"这一轮做到一半，{secs} 秒没有下一个计时点：可能这一次没走完（比如没听清、服务器没回）。"
                                   "再做一次要测的事就行，这一轮不算、会自动补。" if mid else
                                   f"已经 {secs} 秒设备一点输出都没有：看看线是不是插在了别的设备上，或者设备卡住了，拔插一下试试。"
                                   if not quiet["lines"] else
                                   f"已经 {secs} 秒收到的都是乱码：多半是波特率不对（码表按 115200 读），或者插的不是这块板子。"
                                   if quiet["odd"] >= quiet["lines"] / 2 else
                                   f"设备在输出，但 {secs} 秒没有计时点：照提示做一次要测的事；还是没有的话，"
                                   "设备上可能不是计时版（回到上一步重新烧录、试一次）。")
                elif waiting:
                    job["hint"] = ""
                time.sleep(min(1.0, NO_DATA_SECS / 3))

        def run_device():
            threading.Thread(target=watch_quiet, daemon=True).start()
            while not stop():
                ports = port_finder()
                if not ports:
                    job.update(status="waiting_device" if job["status"] != "paused" else "paused")
                    job.setdefault("_away", time.time())
                    time.sleep(wait_step)
                    continue
                if job.get("_away"):
                    T().wait("collect", time.time() - job.pop("_away"), "设备没插 / 拔线了，等插回")
                job.update(status="running", message="", port=ports[0])
                quiet.update(since=time.time(), lines=0, odd=0)
                got0 = quiet["lines"] + len(job["rounds"])
                try:
                    device.stream(ports[0], on_line, stop)
                except Exception as e:  # 拔线了：已测的留着，插回后接着测
                    coll.finish()
                    job["pauses"] = job.get("pauses", 0) + 1
                    opened = quiet["lines"] + len(job["rounds"]) > got0 or job.get("progress", [0])[0]
                    job.update(status="paused", error=str(e), hint="",
                               message=f"设备断开了，已测的 {ok_count()} 轮都留着；插回去会自动接着测。" if opened else
                               f"打不开设备的调试口（{ports[0]}）：可能被别的软件占着（比如串口监视器、另一个码表窗口），"
                               f"关掉它，码表会自动重试。已测的 {ok_count()} 轮都留着。")
                    time.sleep(wait_step)

        def run():
            try:
                (run_device if body.source == "device" else run_demo)()
            finally:
                raw.close()
                coll.finish()
                if job is col["job"]:
                    job["status"] = "done" if ok_count() >= job["target"] else "stopped"
                    if body.source == "device":
                        T().end("collect")

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
        stale = None
        if ok and same:
            sc, raw = job["_scenario"], job["raw_log"]
            if job.get("_page") is not None:  # 合理范围改过（标准文件变了）：提示用新范围更新，刚测完的不提示
                now = stds.find(job["_page"].get("steps") or [])
                if stds.digest(now) != job.get("_std"):
                    stale = stds.changed_at(now)
        elif demo in demos:
            sc, ok, raw = demos[demo]["scenario"], _demo_rounds(demos[demo]), None
        else:
            raise HTTPException(404, "还没有测完的轮")
        out = jdg.judge(sc, ok)
        keep = ("name", "start", "segments", "lines", "experience", "problems", "standard", "matched", "point_names")
        allr = (job or {}).get("rounds", []) if ok and same else []
        quality = {"ok": len(ok), "stale": len([r for r in allr if r.get("stale")]),
                   "missing": len([r for r in allr if not r.get("ok") and not r.get("stale")]), "half": (job or {}).get("half", 0) if raw else 0}
        return {**out, "rounds": ok, "raw_log": raw, "scenario": {k: sc.get(k) for k in keep}, "timing": _timing() if raw else None,
                "quality": quality,
                "stale": bool(stale), "stale_since": stale, "measured_at": (job or {}).get("measured_at") if ok and same else None}

    def _timing():
        job = col["job"]
        dev = None
        if job and job.get("raw_log"):
            dev = {**tln.split_rounds(job.get("rounds", [])), "hints": job.get("hints", 0)}
        return tln.account(T().data, dev)

    @app.get("/api/timing")
    def timing():
        """这次测试本身用了多久（四笔账、每段用时、对照 14 号文档的标准）。"""
        return _timing()

    @app.post("/api/report/refresh")
    def report_refresh():
        """用新范围更新这份报告：不用重测，用时不变，按现在的合理范围重新判。"""
        job = col["job"]
        if not job or job.get("_page") is None:
            raise HTTPException(409, "这份报告不用更新")
        standard = stds.find(job["_page"].get("steps") or [])
        job["_scenario"] = stds.real_scenario(job["_page"], standard, job.get("_values"))
        job["_std"] = stds.digest(standard)
        return report()

    KEYLIKE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
    OUTSIDE = re.compile(r"""<(?:script|link|img|iframe|source|video|audio)\b[^>]*\b(?:src|href)\s*=\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//""", re.I)

    @app.post("/api/report/export")
    def report_export(body: ExportBody):
        """导出：网页把报告拼成一个 HTML 文件（样式、切换都在文件里，断网能开），这里只核对后存下。
        真测的存在项目文件夹的 reports/，演示数据的存在结果文件夹的 demo-reports/；文件名"场景_测完时间.html"，不覆盖。"""
        html = body.html
        T().begin("export")
        T().count("clicks")
        if not html.lstrip().lower().startswith("<!doctype html"):
            raise HTTPException(400, "导出的内容不是网页")
        if KEYLIKE.search(html):
            raise HTTPException(400, "导出的内容里像有密钥，没有存")
        if OUTSIDE.search(html):
            raise HTTPException(400, "导出的内容引用了外面的文件，断网打不开，没有存")
        job = col["job"]
        real = body.demo is None and job and job.get("demo") is None and job.get("raw_log")
        if body.folder:
            folder = Path(body.folder).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise HTTPException(400, "存不了：这个文件夹不存在，换一个已有的文件夹")
        else:
            folder = Path(_export_dir(body.demo))
        when = (job or {}).get("measured_at") if job and (real or job.get("demo") == body.demo) else None
        stamp = (when or time.strftime("%Y-%m-%d %H:%M")).replace("-", "").replace(" ", "-").replace(":", "")
        name = re.sub(r'[\\/:*?"<>|\s]+', "-", body.what.strip()).strip("-")[:40] or "报告"
        folder.mkdir(parents=True, exist_ok=True)
        p, n = folder / f"{name}_{stamp}.html", 2
        while p.exists():
            p, n = folder / f"{name}_{stamp}-{n}.html", n + 1
        p.write_text(html, encoding="utf-8")
        exported.add(p.resolve())
        T().end("export")
        if real:  # 记在项目里：首页"以前测过的"点这条能打开最近一份
            ej = Path(job["raw_log"]).parent.parent.parent / "exports.json"
            try:
                old = json.loads(ej.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = []
            ej.write_text(json.dumps(old + [str(p)], ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "path": str(p)}

    def _export_dir(demo=None):
        """导出默认存哪：真测的放被测源码的同级目录（源码文件夹记在项目文件夹的 project.json 里）；
        找不到源码就放项目文件夹的 reports/；演示数据放结果文件夹的 demo-reports/。"""
        job = col["job"]
        if demo is None and job and job.get("demo") is None and job.get("raw_log"):
            proj = Path(job["raw_log"]).parent.parent.parent
            try:
                src = Path(json.loads((proj / "project.json").read_text(encoding="utf-8"))["source"])
                if src.parent.is_dir():
                    return str(src.parent)
            except (OSError, ValueError, KeyError):
                pass
            return str(proj / "reports")
        return str(live["data"] / "demo-reports")

    def _run_meta(proj, run):
        try:
            return json.loads((run / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):  # 早先测的没留这份：用这个项目最近一次找关键步骤的结果
            ids = sorted((proj / "identify").glob("*.json"), key=lambda f: f.stat().st_mtime)
            if not ids:
                return None
            d = json.loads(ids[-1].read_text(encoding="utf-8"))
            t = run.name
            return {"page": d.get("page"), "values": None, "what": d.get("what") or (d.get("page") or {}).get("mapWhat"),
                    "measured_at": f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}:{t[11:13]}" if len(t) >= 13 else t}

    def _run_med(proj, run, meta):
        page = meta.get("page") or {}
        sc = stds.real_scenario(page, stds.find(page.get("steps") or []), meta.get("values"))
        lines = (run / "raw.log").read_text(encoding="utf-8", errors="replace").splitlines()
        ok = [r for r in meas.collect(lines, meas.Spec.from_scenario(sc)) if r["ok"] and r["ends"][-1] is not None]
        return (round(jdg.med([r["ends"][-1] for r in ok]), 3), len(ok)) if ok else (None, 0)

    def _latest_report(proj):
        cands = []
        try:
            cands += [Path(x) for x in json.loads((proj / "exports.json").read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            pass
        cands += list((proj / "reports").glob("*.html"))
        cands = [x for x in cands if x.exists()]
        return str(max(cands, key=lambda x: x.stat().st_mtime)) if cands else None

    def _fsw_on(proj):
        try:
            return bool(json.loads((proj / "on-device.json").read_text(encoding="utf-8"))["fsw"])
        except (OSError, ValueError, KeyError):
            return (proj / "fsw-backup" / "record.json").exists()

    @app.get("/api/history")
    def history():
        """首页"以前测过的"：只列真测过的（结果文件夹里有原始日志的项目），每条按原始日志重算，不混演示数据。"""
        out = []
        for pj in sorted(Path(live["data"]).glob("*/project.json")):
            proj = pj.parent
            try:
                info = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            done = []
            for log in sorted(proj.glob("runs/*/raw.log")):
                meta = _run_meta(proj, log.parent)
                if not meta or not meta.get("page"):
                    continue
                try:
                    m, n = _run_med(proj, log.parent, meta)
                except Exception:
                    continue
                if m is not None:
                    done.append((meta, m, n))
            if not done:
                continue
            meta, now, n = done[-1]
            full = [d for d in done[:-1] if d[2] >= 10]  # 不足 10 轮（跑流程）的不拿来比，也不当"上次"
            page = meta["page"]
            metric = (page.get("measure") or "").split("→")[-1].strip() or "终点"
            out.append({"name": info.get("name", proj.name) + (f"（{info['board']}）" if info.get("board") else ""),
                        "what": meta.get("what") or "", "last": meta.get("measured_at") or "", "rounds": n, "metric": metric,
                        "now": now, "prev": full[-1][1] if full and n >= 10 else None, "few": n < 10, "path": info.get("source"),
                        "project_dir": str(proj), "report": _latest_report(proj),
                        "fsw_on": _fsw_on(proj)})
        return sorted(out, key=lambda x: x["last"], reverse=True)

    @app.post("/api/history/open")
    def history_open(body: PathBody):
        """首页点一条以前测过的：打开它最近导出的那份报告（一个网页文件）。"""
        proj = Path(body.path).expanduser().resolve()
        if Path(live["data"]).resolve() not in proj.parents:
            raise HTTPException(403, "只能打开码表结果文件夹里的项目")
        rep = _latest_report(proj)
        if not rep:
            raise HTTPException(404, "这次测完还没导出报告")
        (opener or _open_file)(rep)
        return {"ok": True, "path": rep}

    ROUNDS = json.loads((Path(__file__).parent / "rounds.json").read_text(encoding="utf-8"))["templates"]

    @app.get("/api/rounds")
    def rounds_template(what: str = "", start: str = ""):
        """多轮实测每轮做什么：按你写的"测什么"挑模板（对话类用固定的 20 句；别的按起点的说法，比如"按一下按键"）。"""
        t = next(t for t in ROUNDS if not t["match"] or re.search(t["match"], what))
        fill = lambda v: [fill(x) for x in v] if isinstance(v, list) else str(v).replace("{start}", start.strip() or "触发一次")
        return {"template": t["name"], **{k: fill(v) for k, v in t["fields"].items()}}

    @app.get("/api/report/export-dir")
    def report_export_dir(demo: str | None = None):
        return {"dir": _export_dir(demo)}

    exported = set()  # 导出过的文件（可能存在结果文件夹以外，比如源码旁边）：也能"在访达里显示"
    allowed_roots = lambda: [Path(live["data"]).resolve(), Path(data_dir).resolve()]

    @app.post("/api/open-folder")
    def open_folder(body: PathBody):
        """原始日志的"打开所在文件夹"：只开码表自己的结果文件夹里的东西。"""
        p = Path(body.path).expanduser().resolve()
        if not (p in exported or any(r == p or r in p.parents for r in allowed_roots())) or not p.exists():
            raise HTTPException(403, "只能打开码表结果文件夹里的东西")
        (opener or _open_in_finder)(str(p))
        return {"ok": True}

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
        T().begin("advise")
        T().count("clicks")

        def run():
            save = Path(body.project_dir) / "advice" if body.project_dir else None
            try:
                r = advs.advise(model, root, j, sc, steps=body.steps, what=body.what or "", save_to=save,
                                on_progress=lambda p: job.update(read=p.get("read", "")))
            except Exception as e:
                r = {"ok": False, "kind": "other", "message": f"写建议时码表出错了：{e}"}
            job["seconds"] = r.get("seconds", round(time.time() - job["started"], 1))
            if r.get("called", True):
                T().count("llm_calls")
            if r["ok"]:
                job.update(status="done", fixes=r["fixes"], fine=r["fine"], called=r.get("called", True))
                T().end("advise")
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

    @app.post("/api/client-log")
    def client_log(body: dict):
        """网页那边的小记录（比如提示音响了没有、浏览器的声音开没开）：存在结果文件夹的 client-log.jsonl，查"没响"时用（决策 121）。"""
        try:
            Path(live["data"]).mkdir(parents=True, exist_ok=True)
            with open(Path(live["data"]) / "client-log.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.strftime("%H:%M:%S"), **{k: str(v)[:300] for k, v in body.items()}}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return {"ok": True}

    @app.get("/api/collect/events")
    def collect_events():
        """多轮实测的进度推给网页（服务器推送）：一有变化就推，网页在后台、定时查询被浏览器拖慢时也能马上"叮"一声（决策 102）。
        测完（或停下）推最后一次就结束；网页断了会退回定时查询。"""
        def gen():
            last, beat, job0 = None, time.time(), col["job"]
            while True:
                job = col["job"]
                if job is not job0 and job0 is not None:
                    return  # 换了一次新的测量：让网页重新连
                v = _col_view(job) if job else {"status": "idle", "rounds": []}
                s = json.dumps(v, ensure_ascii=False, default=str)
                if s != last:
                    last, beat = s, time.time()
                    yield f"data: {s}\n\n"
                    if v.get("status") in ("done", "stopped", "idle"):
                        return
                elif time.time() - beat > 15:
                    beat = time.time()
                    yield ": 还在\n\n"  # 心跳，免得连接被当成断了
                time.sleep(0.1)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/fsw")
    def fsw_status():
        return fsw["job"] or {"status": "idle"}

    @app.post("/api/fsw/restore")
    def fsw_restore(body: RestoreBody | None = None):
        """还原成原版：源码放回去（和你原来的源码逐字节比对），设备插着就把后台编好的原版烧回去。
        码表重开过（内存里没有这次的记录）：按项目文件夹里存的插入记录还原。"""
        a = fsw["args"]
        if body and body.project_dir and (not a or Path(a.project_dir).resolve() != Path(body.project_dir).resolve()):
            try:
                a = FswInsertBody(**json.loads((Path(body.project_dir) / "fsw-args.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                a = None
        if not a:
            raise HTTPException(409, "还没插过计时点，不用还原")
        backup, orig_fw, _ = _fsw_dirs(a)
        T().begin("restore")
        T().count("clicks")
        if fsw["job"]:
            fsw["job"]["cancel"] = True  # 还在等设备烧计时版的，不烧了
        ins.restore(a.work, backup)
        try:
            source = json.loads((Path(a.project_dir) / "project.json").read_text(encoding="utf-8"))["source"]
        except (OSError, ValueError, KeyError):
            source = None
        cmp = ins.compare(a.work, source) if source and Path(source).is_dir() else {"same": None, "diff": [], "files": 0}
        out = {"same": cmp["same"], "diff": cmp["diff"][:10], "files": cmp["files"], "flashed": False, "port": None, "waiting": False}
        ports = port_finder()
        job = fsw["job"] or {}
        fsw["job"] = job

        def burn(port):
            r = device.flash(orig_fw, port, lambda pct, msg: None)
            out.update(flashed=bool(r.get("ok")), port=port, error=r.get("error"), waiting=False)
            if r.get("ok"):
                _on_device(a.project_dir, False)
                out["say"] = "已还原：源码放回原样，原版已经烧回设备。"
            else:
                out["say"] = f"源码已还原，但原版没烧上：{_plain(r.get('error'))}。插好线再点一次「还原成原版」。"
            job.update(status="restored", restore=dict(out))
            T().end("restore")

        if ports and orig_fw.exists():
            burn(ports[0])
        elif orig_fw.exists():  # 拔了线：源码先还原，说清楚；插上线就自动把原版烧回去（决策 109）
            out.update(waiting=True, say="源码已还原。设备没插着，原版还没烧回去：插上设备就自动烧，不用再点。")
            job.update(status="restored", restore=dict(out))

            def wait_and_burn():
                t_end = time.time() + 1800
                while time.time() < t_end and job.get("restore", {}).get("waiting"):
                    p2 = port_finder()
                    if p2:
                        return burn(p2[0])
                    time.sleep(wait_step)
            threading.Thread(target=wait_and_burn, daemon=True).start()
        else:
            out["say"] = "源码已还原。没找到编好的原版固件，设备上的计时版要等下次编好原版再烧回去。"
            job.update(status="restored", restore=dict(out))
            T().end("restore")
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
            if not r.get("reused"):
                T().count("llm_calls")
            if r.get("ok"):
                T().end("identify", reused=bool(r.get("reused")))
                T().begin("check")

        threading.Thread(target=run, daemon=True).start()
        return job

    @app.post("/api/identify/start")
    def identify_start(body: IdentifyBody):
        if not Path(body.work).is_dir():
            raise HTTPException(400, "找不到复制好的源码，请回首页重新选一次固件源码。")
        if not body.what.strip():
            raise HTTPException(400, "先写要测从什么到什么。")
        T().end("source")
        T().begin("identify")
        T().count("clicks")
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
        """选好固件后的"AI 推荐"常用语：只给项目概况，一般十几秒。源码没变就用上次的，不再调用大模型。"""
        f = Path(body.project_dir) / "suggest.json" if body.project_dir and body.fingerprint else None
        provider = cfg().llm_provider
        if f and cfg().reuse_last:
            try:
                last = json.loads(f.read_text(encoding="utf-8"))
                if last.get("fingerprint") == body.fingerprint and last.get("provider") == provider and last.get("phrases"):
                    return {"phrases": last["phrases"], "called": False}
            except (OSError, ValueError):
                pass
        _, model = _model()
        phrases = idf.suggest(model, body.work, {"name": body.name, "chip": body.chip})
        if f and phrases:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"fingerprint": body.fingerprint, "provider": provider, "phrases": phrases},
                                    ensure_ascii=False), encoding="utf-8")
        return {"phrases": phrases, "called": True}

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
        r = prepare(path, data_dir=live["data"], board=body.board, unpack_to=unpack)
        if r.get("project_dir"):
            if not (T().path and T().path.parent == Path(r["project_dir"]) and "identify" not in T().data["stages"]):
                tline["cur"] = tln.Timeline(Path(r["project_dir"]) / "timeline.json")  # 新的一次测量
                T().begin("source")
            T().count("clicks")
        return r

    @app.middleware("http")
    async def no_stale_page(request, call_next):
        """网页文件每次都让浏览器先问一下有没有新的，升级码表后不会还用旧代码。"""
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
