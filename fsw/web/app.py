"""本地网页后台：给网页送页面、假数据、步骤条状态和设备输出。

这一版只用演示数据；真读源码、真编译、真串口后面逐个接进来。
"""

import hashlib
import os
import json
import re
import shutil
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fsw.config import load_config, save_config
from fsw.scenario import main_experience, to_page
from fsw import identify as idf
from fsw import instrument as ins
from fsw import measure as meas
from fsw import judge as jdg
from fsw import advise as advs
from fsw import timeline as tln
from fsw import standards as stds
from fsw import rangelist as rl
from fsw import carry as cy
from fsw import llm as llms
from fsw.workspace.picker import native_pick
from fsw.platforms.espidf import EspIdf, ToolEnvError, chip_name, find_idf, find_ports, pick_idf, port_key
from fsw.platforms.espidf.install import MacInstaller, choose_version
from fsw.workspace import workspace as ws
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
    project_dir: str | None = None


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
        try:
            return EspIdf(found["path"]).flash_saved(fw, port, on_progress=on_progress)
        except ToolEnvError as e:
            return {"ok": False, "cause": "tool", "message": e.message, "error": e.detail}

    def read_chip(self, port):
        """设备上是哪种芯片（选板子时核对用）：乐鑫烧录工具连一下，设备会重启一下。"""
        found = pick_idf(self.idf_finder(), None)
        if not found:
            return {"chip": None, "error": "没找到乐鑫编译工具，读芯片要用它带的 esptool。"}
        try:
            return EspIdf(found["path"]).read_chip(port)
        except ToolEnvError as e:
            return {"chip": None, "error": e.detail or e.message}

    def port_key(self, port):
        return port_key(port)

    def read(self, port, seconds, on_line, stop=None):
        ser = EspIdf.open_port(port)
        try:
            return EspIdf.read_for(ser, seconds, on_line, stop=stop)
        finally:
            EspIdf.close_port(ser)

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
            EspIdf.close_port(ser)


class CollectBody(BaseModel):
    rounds: int = 20
    demo: str | None = None          # 演示数据（没真源码时）：按它的场景算；没设备时回放它的设备输出
    page: dict | None = None         # 真找完的步骤（结果页数据）：按它的段算
    steps: list[dict] | None = None  # 插计时点时用的步骤（核对后的，含你自己加的）：后台有插入时的那份就用后台的
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


def _detail(e):
    """出错时的原始报错：放进"详情"（error 字段），不放进提示。大模型的错用它自带的原话。"""
    return getattr(e, "detail", None) or f"{type(e).__name__}: {e}"


def _guarded(fn, on_error):
    """后台线程整段兜住：码表自己出错也要转成这个任务的失败状态、说清楚，不能一直停在"进行中"挡住重试。
    on_error(e) 负责把任务改成失败；原始的调用栈打在运行码表的窗口里，好查。"""
    def run():
        try:
            fn()
        except Exception as e:
            traceback.print_exc()
            on_error(e)
    return run


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


def _fw_chip(fw):
    """存下的固件是给哪种芯片编的（乐鑫编译时写进 flasher_args.json）。"""
    try:
        args = json.loads((Path(fw) / "flasher_args.json").read_text(encoding="utf-8"))
        return chip_name((args.get("extra_esptool_args") or {}).get("chip"))
    except (OSError, ValueError, TypeError):
        return None


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


TAIL = re.compile(r"FSW,TAIL,P(\d+),(-?\d+),-?\d+")  # 设备补打的"可能晚到的点在清零 1 秒之后才走到"（见 fw_stopwatch.h 的 fsw_tail）
TAIL_MAX_US = 30_000_000  # 清零之后这么久以后才走到的不算"上一轮晚到"（多半是下一轮开始前的动静，比如提示音）
NO_DATA_SECS = 20  # 多轮实测：调试口开着，这么久没收到计时点就说人话提示（不停下，做了就接着收）
CARRY_SECS = 30  # 试通后这么久内开始多轮实测，才接着算试通时正在进行的那一轮
ROUND_SETTLE = 1.5  # 多轮实测：一轮收全后，下一轮一直没开始，再等这么久就收尾（最后一轮、或设备不再开始下一轮）
MISS_RUN = 3       # 多轮实测：同一个计时点连着这么多轮没收到，就在提示里点名


class RestoreBody(BaseModel):
    project_dir: str | None = None   # 码表重开过：按项目文件夹里存的插入记录还原


TRY_END_WAIT = 20  # 试一次收全后最多再等多久结束标记（回复还在放）：等到了，这一轮连同结束时补打的点一起交给多轮实测
SKIP_ROUNDS = 2  # 试一次里连着这么多轮（走完了、后面的点都到了）都缺同一个点：不再等满时间，直接说是哪一个、在哪一行
TRY_SECONDS = 180  # 试一次最多等多久（决策 107）：烧完就开始听，设备重启、连网、人做一次要测的事都在这里面；不用人判断"准备好了"
REOPEN_SECS = 8   # 试一次：打开调试口后这么久没有任何输出就重开（设备烧完重启，调试口会断开再回来）


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


def code_ctx(job):
    """技术细节"展开代码"：每个计时点在原版源码里的位置、函数名和前后各 3 行（10-21 用户定）。
    读项目里记的原版源码（只读）；计时版插过计时点，行号会变，不用它。截下来放进报告，发给别人、断网也能看。"""
    page, pd = (job or {}).get("_page") or {}, (job or {}).get("_project_dir")
    try:
        root = Path(json.loads((Path(pd) / "project.json").read_text(encoding="utf-8"))["source"]).resolve()
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    out = {}
    for s in page.get("steps") or []:
        path, line = s.get("path"), s.get("l")
        if not path or not isinstance(line, int) or line <= 0:
            continue
        f = (root / path).resolve()
        if root not in f.parents or not f.is_file():
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        a = max(1, line - 3)
        out[s["p"]] = {"path": path, "l": line, "func": re.sub(r"[（(].*$", "", s.get("func") or "").strip(),
                       "ctx": [[n, src[n - 1]] for n in range(a, min(len(src), line + 3) + 1)]}
    return out


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
            if job and job["work"] == body.work and job["state"] in ("running", "done") and job.get("board") == body.board:
                return job  # 同一份原始代码已经在编或编好了，不重复编
            idf = pick_idf(idf_finder(), body.idf_min)
            if idf and body.project_dir and _orig_ok(body.project_dir, body.board):
                # 源码和板子都没变、上次编好的原版存着：直接用，不用再等后台编一遍（决策 134）
                bg["job"] = {"work": body.work, "state": "done", "pct": 100, "message": "源码没变，用上次编好的原版",
                             "started": time.time(), "seconds": 0, "error": None, "idf": idf["version"], "idf_path": idf["path"],
                             "board": body.board, "target": re.sub(r"[^a-z0-9]", "", (body.chip or "esp32").lower()),
                             "reused": True}
                return bg["job"]
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
            c = _clean_work(body.work, body.project_dir)  # 副本里还插着上次的计时点（没还原就换了板子）：先还原再编原版
            if not c["ok"]:
                return job.update(state="failed", error=None, message=c["message"])
            try:
                r = bg_builder(body.work, target, body.board, idf["path"], progress)
            except Exception as e:  # 编译工具本身出问题也要说清楚，不能一直转圈；这不是源码编不过
                r = {"ok": False, "seconds": round(time.time() - job["started"], 1), "cause": "tool",
                     "message": ToolEnvError.message, "error": getattr(e, "detail", None) or str(e)}
            job["seconds"] = r.get("seconds")
            if r.get("ok"):
                job.update(state="done", pct=100, message="原始代码已编好")
            elif r.get("cause"):  # 编译工具没加载起来、组件没下载下来：都不是源码的问题
                job.update(state="failed", cause=r["cause"], error=r.get("error"), message=r["message"])
            else:
                job.update(state="failed", error=r.get("error"),
                           message="原版编译没通过，不是码表改的：你选的源码本身在这台电脑上编不过"
                                   "（编译工具、依赖或板子配置的问题），烧录前要先解决。")

        def crashed(e):
            job.update(state="failed", cause="tool", error=_detail(e),
                       message="后台编原始代码时码表出错了，不是源码的问题：回首页重新选一次源码再试。")
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return job

    @app.get("/api/bgbuild")
    def bgbuild_status():
        return bg["job"] or {"state": "idle", "pct": 0}

    # ---- 插计时点 → 确认 → 编译（只重编改过的）→ 烧录（没插就等）→ 试一次；撤掉一处；还原 ----
    device = device or EspDevice(idf_finder)
    fsw = {"job": None, "args": None, "plan": None}
    last_src = {}  # 最近选好的源码（/api/source/inspect 的结果）

    def _fsw_dirs(a):
        pd = Path(a.project_dir)
        return pd / "fsw-backup", pd / "firmware" / "orig", pd / "firmware" / "fsw"

    # 码表自己记每段用时（决策 99）：选源码时开一份，存在项目文件夹的 timeline.json；测完也放一份进这次的结果文件夹
    tline = {"cur": tln.Timeline()}

    def T():
        return tline["cur"]

    def _fail(job, kind, message, **extra):
        if job.get("cancel"):  # 已经点了「停止测量」：不再报失败
            return
        job.update(status="failed", err={"kind": kind, "message": message, **extra})

    def _on_device(project_dir, fsw_on):
        """设备上现在是计时版还是原版（存在项目文件夹里，码表重开也认得）。"""
        (Path(project_dir) / "on-device.json").write_text(json.dumps({"fsw": fsw_on, "at": time.strftime("%Y-%m-%d %H:%M")}),
                                                          encoding="utf-8")

    def _orig_ok(project_dir, board):
        """存着的原版固件是不是现在这份源码、这块板子编的（决策 134）：改过源码再测，还原时不能烧回改之前的旧版本。"""
        try:
            fp = json.loads((Path(project_dir) / "project.json").read_text(encoding="utf-8")).get("fingerprint")
            rec = json.loads((Path(project_dir) / "firmware" / "orig" / "fsw-orig.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(fp) and rec.get("fingerprint") == fp and rec.get("board") == board

    def _orig_older(project_dir):
        """存着的原版是改源码之前那份编的（改了源码还没重新插计时点）：还原时照样烧它，把设备上的计时版换掉，但要说清楚。"""
        try:
            fp = json.loads((Path(project_dir) / "project.json").read_text(encoding="utf-8")).get("fingerprint")
            rec = json.loads((Path(project_dir) / "firmware" / "orig" / "fsw-orig.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(fp and rec.get("fingerprint")) and rec["fingerprint"] != fp

    def _save_orig(a, orig_fw):
        """后台编好的原版存一份，记下是哪份源码、哪块板子编的。
        先存到旁边核对：编出来的带计时点（编译目录里是计时版）就不存，不然还原时烧回去的是计时版；之前存的原版留着。"""
        new = orig_fw.with_name("orig.new")
        if not EspIdf.save_firmware(a.work, new):
            return
        if any(b"FSW,RESET," in p.read_bytes() for p in new.rglob("*.bin")):
            shutil.rmtree(new, ignore_errors=True)
            return
        shutil.rmtree(orig_fw, ignore_errors=True)
        new.rename(orig_fw)
        try:
            fp = json.loads((Path(a.project_dir) / "project.json").read_text(encoding="utf-8")).get("fingerprint")
        except (OSError, ValueError):
            fp = None
        (orig_fw / "fsw-orig.json").write_text(json.dumps({"fingerprint": fp, "board": a.board}), encoding="utf-8")

    def _wait_bg(job, work):
        """插之前等后台把原始代码编完：原版编好的固件要先存下来，还原时烧它。"""
        while True:
            b = bg["job"]
            if not b or b.get("work") != work or b["state"] != "running":
                return b
            job["log"] = f"等后台把原始代码编完再插（{b.get('pct', 0)}%），原版要先存一份"
            time.sleep(wait_step / 4)

    def _clean_work(work, project_dir=None):
        """编原版、找关键步骤之前：工作副本里不能留着上次的计时点（上次没还原）。有插入记录就先还原，
        记录和副本对不上就从源码重新复制。只撤副本里的，设备上是哪一版不变，还原成原版照样能烧。"""
        pd = Path(project_dir) if project_dir else Path(live["data"]).expanduser() / Path(work).parent.name
        if not (pd / "project.json").exists():
            return {"ok": True, "did": None}
        r = ws.ensure_clean(pd)
        if r.get("changed") and fsw["args"] and Path(fsw["args"].work) == Path(work):
            fsw["plan"] = None  # 源码在这期间改过：上次的插入方案对不上了
        return r

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
                                       and cy.sig(last.get("steps")) == cy.sig(body.steps) and last.get("ver") == ins.PLAN_VER) else None
        except (OSError, ValueError, KeyError):
            cached = None
        return fp, cached

    def _redo(project_dir):
        """上一次插完编译或试的时候码表看出来的问题（存在项目文件夹的 fsw-redo.json，码表重开也在）：
        {"note": 重插时告诉 AI 的那句话（用掉、插好就清掉）, "end": 试一次看出来一轮到哪一步才完 {"end": P 编号, "name": 名字}（一直留着）}。"""
        try:
            d = json.loads((Path(project_dir) / "fsw-redo.json").read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _redo_set(project_dir, note=None, end=None, clear_note=False):
        d = _redo(project_dir)
        if note:
            d.update(note=note, at=time.strftime("%Y-%m-%d %H:%M:%S"))
        if clear_note:
            d.pop("note", None)
        if end is not None:
            d["end"] = end
        try:
            Path(project_dir).mkdir(parents=True, exist_ok=True)
            (Path(project_dir) / "fsw-redo.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _flow(project_dir):
        """核对每轮结束那一步要用的识别结果：识别时说这条线到哪一步结束、分段；加上试一次看出来的结束（见 _redo）。"""
        out = {"hint": _redo(project_dir).get("end")}
        try:
            ids = sorted((Path(project_dir) / "identify").glob("*.json"), key=lambda f: f.stat().st_mtime)
            d = json.loads(ids[-1].read_text(encoding="utf-8")) if ids else {}
            out.update(end=(d.get("main") or {}).get("end"), segs=(d.get("page") or {}).get("segs") or [],
                       follow=[s["p"] for s in (d.get("page") or {}).get("steps") or [] if s.get("follow")])
        except (OSError, ValueError, AttributeError):
            pass
        return out

    def _prep_view(p):
        return {k: v for k, v in p.items() if k not in ("text", "work", "steps", "llm")}

    # 插计时点提前（决策 96）：找完关键步骤、人核对步骤时，后台先按这些步骤生成插法（大模型只读）；
    # 人没改步骤，点"确认"就直接用；改了就作废、按新步骤重新生成
    @app.post("/api/fsw/prepare")
    def fsw_prepare(body: FswInsertBody):
        note = _redo(body.project_dir).get("note")
        if _plan_cache(body)[1] and not note:
            return {"status": "reuse"}
        backup, _, _ = _fsw_dirs(body)
        if ins.current(backup):  # 这份代码上已经插过（改了步骤要重插）：等点确认、先还原再生成，免得行号对不上
            return {"status": "skip"}
        p = fsw.get("prep")
        if p and p["work"] == body.work and p["steps"] == body.steps and p["status"] in ("running", "ready") and p.get("note") == note:
            return _prep_view(p)
        model = _model()[1]
        p = {"status": "running", "work": body.work, "steps": body.steps, "started": time.time(), "text": None, "seconds": None,
             "read": "", "llm": model, "note": note}  # note：上次看出来的问题，提前准备时也告诉 AI  # llm：插法不合规时在同一段对话里追问一次（见 ins.fix_plan）
        fsw["prep"] = p

        def run():
            needs = stds.needed_values(body.steps, stds.find(body.steps))
            try:
                text = model.investigate(ins.build_prompt(body.steps, ins.default_header(body.work), needs, extra=note), body.work,
                                         on_tool=lambda d: p.update(read=d))
                p.update(status="ready", text=text)
            except Exception as e:
                p.update(status="failed", message=str(getattr(e, "message", e)), error=_detail(e))
            p["seconds"] = round(time.time() - p["started"], 1)

        def crashed(e):  # 比如步骤的行号不对、对标准时出错：标成没成，点确认时重新生成，插入不会一直等它
            p.update(status="failed", message="提前准备插法时码表出错了，点确认时会重新生成。", error=_detail(e),
                     seconds=round(time.time() - p["started"], 1))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return _prep_view(p)

    @app.get("/api/fsw/prepare")
    def fsw_prepare_status():
        p = fsw.get("prep")
        return _prep_view(p) if p else {"status": "idle"}

    @app.post("/api/fsw/insert")
    def fsw_insert(body: FswInsertBody):
        _stop_collect()
        proj = Path(body.project_dir)
        fp, cached = _plan_cache(body)
        note = _redo(body.project_dir).get("note")
        if note:  # 上一次插完编译或试的时候发现插法有问题：不再沿用上次的插法，带上这个问题重新问 AI；提前准备时没带上这句的也不用
            cached = None
            if fsw.get("prep") and fsw["prep"].get("note") != note:
                fsw["prep"]["status"] = "stale"
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
            c = ws.ensure_clean(body.project_dir)  # 上次插过的先还原；插入记录和副本对不上就重新复制，不拿旧备份盖新副本
            if not c["ok"]:
                return _fail(job, "insert", c["message"])
            plan_text = {}
            talk = {"llm": None}  # 生成插法的那段对话：插法不合规时在里面追问一次

            class Tap:  # 先拿到插入清单（大模型只读），等原版编完存好，再真正插
                def investigate(self, prompt, root, on_text=None, on_tool=None):
                    plan_text["t"] = model.investigate(prompt, root, on_text=on_text, on_tool=on_tool)
                    return plan_text["t"]
            t0 = time.time()
            needs = stds.needed_values(steps, stds.find(steps))  # 问题规则要看附加值的计时点：让 AI 顺带记上
            if cached:
                text = cached
                job.update(reused=True, log="计时点那几处和上次一样，用上次的插入方案，没调用大模型")
            elif use_prep:
                while prep["status"] == "running":
                    job["log"] = f"你核对步骤时 AI 已经在后台按这些步骤准备插法，还差一点（已准备 {int(time.time() - prep['started'])} 秒）"
                    time.sleep(wait_step / 4)
                if prep["status"] == "ready":
                    text = prep["text"]
                    talk["llm"] = prep.get("llm")
                    job["log"] = "用你核对步骤时 AI 提前准备好的插法"
                else:
                    job.update(prepared=False, log="提前准备的那次没成，重新生成")
                    try:
                        job["calls"] += 1
                        talk["llm"] = _model()[1]
                        text = talk["llm"].investigate(ins.build_prompt(steps, ins.default_header(body.work), needs, extra=note), body.work,
                                                       on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
                    except llms.LLMError as e:
                        return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。", error=e.detail)
            else:
                job["calls"] += 1
                try:
                    text = Tap().investigate(ins.build_prompt(steps, ins.default_header(body.work), needs, extra=note), body.work,
                                             on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
                    talk["llm"] = model
                except llms.LLMError as e:
                    return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。", error=e.detail)
            Path(body.project_dir).mkdir(parents=True, exist_ok=True)
            (Path(body.project_dir) / "fsw-plan-reply.txt").write_text(text, encoding="utf-8")  # AI 的原话留一份，出错时好查
            flow = _flow(body.project_dir)
            first = ins.parse_plan(text, body.work, steps, needs, flow)
            if not first["ok"] and first.get("bad") and talk["llm"] is not None:
                job["log"] = f"AI 给的插法里有 {len(first['bad'])} 处不能用（只能插计时点、注释和 #include），追问它改一次"
            plan, again = ins.fix_plan(talk["llm"], text, body.work, steps, needs, flow) if not first["ok"] else (first, None)
            job["calls"] += 1 if plan.get("asked") else 0
            if again:  # 追问后的回答也留一份
                (Path(body.project_dir) / "fsw-plan-followup.txt").write_text(again, encoding="utf-8")
            job["seconds"]["insert"] = round(time.time() - t0, 1)
            if plan["ok"] and fp:
                (proj / "fsw-plan.json").write_text(json.dumps({"fingerprint": fp, "steps": steps, "reply": again or text, "ver": ins.PLAN_VER,
                                                                "fixes": plan.get("fixes") or [], "unchecked": plan.get("unchecked") or [],
                                                                "tail": plan.get("tail") or [], "end": plan.get("end"),
                                                                "follow": plan.get("follow") or []},
                                                               ensure_ascii=False), encoding="utf-8")
            if not plan["ok"]:
                return _fail(job, "insert", plan["message"])
            if not bg["job"] or bg["job"].get("work") != body.work:  # 码表重开过：原版再编一次（只是检查，很快）
                bgbuild_start(BgBuildBody(work=body.work, chip=body.chip, board=body.board, project_dir=body.project_dir))
            b = _wait_bg(job, body.work)
            if b and b["state"] == "done" and not b.get("reused") and not _orig_ok(body.project_dir, body.board):
                _save_orig(body, orig_fw)
            try:
                rec = ins.apply(body.work, plan, backup)
            except Exception as e:  # apply 自己会撤回已改的文件；这里兜住没料到的错，任务不卡在"进行中"
                rec = {"ok": False, "message": f"插计时点时码表出错了（{e}），一个文件都没改，点\"重新插入\"再试一次。"}
            if not rec["ok"]:
                return _fail(job, "insert", rec["message"])
            _save_inserted(body.project_dir, steps, rec["steps"])
            fsw["plan"] = plan
            if note and not cached:
                _redo_set(body.project_dir, clear_note=True)  # 这句话已经告诉 AI、插好了：下次不再带
            job.update(status="inserted", view=ins.diff_view(rec, steps), notes=plan["notes"], log="插好了，先看改动",
                       total=len(rec["steps"]), checks=ins.plan_checks(plan))
            T().end("insert", reused=bool(cached), prepared=bool(job.get("prepared")))
            T().count("llm_calls", job.get("calls", 0))
            T().begin("confirm")

        def crashed(e):
            if job["status"] == "running":
                _fail(job, "insert", "插计时点时码表出错了，点\"重新插入\"再试一次。", error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return job

    def _stop_collect(wait=2.0):
        """还在跑的多轮实测先停下、让出调试口，再插点、编译、烧录（10-20 真测：测量没停就又开了一次，
        两边同时用调试口，烧录失败、设备停在等烧录）。已收的轮照常留着；最多等 wait 秒让读调试口的线程退出。"""
        old = col["job"]
        if not old or old.get("status") not in ("running", "waiting_device", "paused"):
            return
        old["_stop"] = True
        t = time.time()
        while old.get("status") in ("running", "waiting_device", "paused") and time.time() - t < wait:
            time.sleep(0.02)

    def _run_from(start):
        _stop_collect()
        stray = EspIdf.release_ports()  # 先把码表自己还占着的调试口关掉：烧录和试一次要用
        if stray:
            print(f"[fsw] 烧录 / 试一次前关掉了 {stray} 个还开着的调试口", flush=True)
        job, a = fsw["job"], fsw["args"]
        _, orig_fw, fsw_fw = _fsw_dirs(a)
        job.update(status="running", err=None, cancel=False, gen=job.get("gen", 0) + 1)  # 第几趟：上一趟还没停的"试一次"见到就停，让出调试口
        if start == "try" and not job.get("_just_flashed"):  # 再试一次、没重新烧录：设备开着，页面别先闪一下"设备正在重启"
            job.update(phase="try", dev_seen=time.time())

        def build():
            T().end("confirm")
            T().begin("build")
            job.update(phase="build", pct=0, log="准备编译")
            b = _wait_bg(job, a.work)
            if b and b["state"] == "failed":
                return _fail(job, "orig", b.get("message") or "原版编译没通过", error=b.get("error"), cause=b.get("cause"))
            if not b or b["state"] != "done":
                return _fail(job, "orig", "原始代码还没编好，没法编计时版。回首页重新选一次源码。")
            if not b.get("reused") and not _orig_ok(a.project_dir, a.board):
                _save_orig(a, orig_fw)
            if not ins.current(_fsw_dirs(a)[0]):  # 编原版、找关键步骤时把副本里的计时点撤掉了：按这次的插入方案插回去
                rec = ins.apply(a.work, fsw["plan"], _fsw_dirs(a)[0]) if fsw["plan"] else \
                    {"ok": False, "message": "副本里的计时点已经撤掉了（源码改过，或者回去重新找了关键步骤），点\"重新插入\"。"}
                if not rec["ok"]:
                    return _fail(job, "insert", rec["message"])
            t0 = time.time()
            try:
                r = bg_builder(a.work, b["target"], b.get("board"), b["idf_path"],
                               lambda pct, msg: _log(job, msg, pct))
            except Exception as e:  # 编译工具本身出问题，不是计时点引起的
                return _fail(job, "build", ToolEnvError.message, error=getattr(e, "detail", None) or str(e), cause="tool")
            job["seconds"]["build"] = r.get("seconds", round(time.time() - t0, 1))
            if r.get("cause"):
                return _fail(job, "build", r["message"], error=r.get("error"), cause=r["cause"])
            if not r.get("ok"):
                rec = ins.current(_fsw_dirs(a)[0]) or {"steps": {}}
                place = ins.error_place(r.get("error"), rec, a.work)
                step = place["step"] if place and place["step"] not in ins.HELPERS else None
                s = next((x for x in a.steps if ins.point_id(x, a.steps) == step), None)
                where = f"{place['file']} 第 {place['at']} 行" if place else ""
                short = (r.get("error") or "").replace(str(Path(a.work).resolve()) + "/", "").replace(str(a.work) + "/", "")
                if place:  # 报错对回了插进去的哪一句（报错指到头文件时按"在哪里展开的宏"找）：说清楚，重插时告诉 AI
                    note = ins.error_text(place, a.steps)
                    _redo_set(a.project_dir, note)
                    return _fail(job, "build", f"编译没通过：{note}。点\"重新插入\"，码表会把这个问题告诉 AI。",
                                 error=short, step=step, step_name=s["n"] if s else None, where=where)
                return _fail(job, "build", "编译没通过", error=short, step=None, step_name=None, where="")
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
            if r.get("cause") == "tool":
                return _fail(job, "burn", r["message"], error=r.get("error"), cause="tool")
            if r.get("wrong_chip"):  # 选的板子和设备芯片不同：乐鑫工具没写进去，设备里原来的东西没动
                w = r["wrong_chip"]
                _remember_chip(port, w["device"])
                return _fail(job, "burn", f"板子选错了：设备上的芯片是 {w['device']}，你选的板子用的是 {w['firmware']}。"
                                          f"乐鑫烧录工具没往里写，设备里原来的东西没动。回首页换一块芯片是 {w['device']} 的板子，再点「开始测量」。",
                             error=r.get("error"), wrong_chip=w)
            if not r.get("ok"):
                return _fail(job, "burn", "烧录中断", error=r.get("error"))
            _remember_chip(port, _fw_chip(fsw_fw))  # 烧进去了：设备就是固件要的芯片，以后选板子直接核对，不用再读
            _on_device(a.project_dir, True)
            job["_just_flashed"] = True  # 刚烧完设备会重启；之后点「再试一次」时设备一直开着，不用再等它开机
            T().end("flash")
            return True

        def ready():
            """烧完先停下：设备会重启、连网，人要先喊唤醒词再说一句，等点了"准备好了"才开始试一次。"""
            job.update(phase="try", status="ready_try", pct=0, got=[], log="等你准备好：完整做一次要测的事", ready_at=time.time())
            return None

        def try_once():
            if job.get("ready_at"):  # 码表重开前停在"准备好了"的老状态
                T().wait("ready", time.time() - job.pop("ready_at"), "等你准备好、点「准备好了」")
            if "try" not in T().data["stages"]:
                T().begin("try")
            T().count("try_said")
            T().count("clicks")
            fresh = job.pop("_just_flashed", False)
            my_gen = job.get("gen")
            superseded = lambda: fsw["job"] is not job or job.get("gen") != my_gen or job.get("cancel")  # 又插了一次 / 又点了编译烧录 / 再试一次：这一趟作废，让出调试口、不改页面状态
            job.update(phase="try", status="running", pct=0, got=[], log="等设备开机" if fresh else "设备开着，等计时点",
                       try_started=time.time(), dev_seen=None if fresh else time.time(), boot_quiet=False, heard=None)
            want = set(int(k[1:]) for k in (ins.current(_fsw_dirs(a)[0]) or {"steps": {}})["steps"])
            # 和多轮实测同一个标准：有一轮（从一个起点到下一个起点）插上的计时点全部收到，才算试通。
            # 有的点只在某些情况下才走到（比如只喊唤醒词时，设备不会判断"你说完了"），所以某一轮没收全不马上判失败：
            # 接着听，人可以再做一次，直到有一轮收齐或时间到
            names = {ins.point_id(x, a.steps): x.get("n") for x in a.steps}
            # 只在某种触发方式才走的计时点（比如点屏幕开始、喊唤醒词那一下）：每轮不一定有，不算必收
            want = (want - {int(ins.point_id(x, a.steps)[1:]) for x in a.steps if x.get("opt")}) or want
            job["need"] = len(want)  # 页面上"已收到 x / 共几个"按这个数，和判通过的标准一致
            need_n = max(1, len(want) - min(2, len(want) // 4))
            # 起点（比如"设备听出你说完"）：喊唤醒词、点屏幕开头的那一轮本来就没有它（唤醒词模块认的，不经过"说完"判断）。
            # 所以试一次不等起点：其余的有一轮收齐就算试通；起点留给多轮实测去核对（连着几轮没记到起点会点名，见 take）
            start_ids = {int(ins.point_id(x, a.steps)[1:]) for x in a.steps if x.get("line") == "start"} & want
            rounds = {"cur": set(), "best": set(), "seen": set(), "raw": set(), "start_us": None}  # 这一轮收到的、收得最多的那一轮、收到过的
            # 每轮半路被清零：一轮有了起点、还没走到结束那一步就读到清零，清零后（下一个起点之前）又陆续来了这一轮后面的点
            order = [int(ins.point_id(x, a.steps)[1:]) for x in a.steps]
            flow = _flow(a.project_dir)
            end_s = ins.round_end(a.steps, flow.get("hint"))
            end_id = int(ins.point_id(end_s, a.steps)[1:]) if end_s else None
            # 可能在每轮清零之后才打出来的点（见 ins.tail_points）：清零后、下一个起点之前打出来的，看晚了多久
            tail_ids = {int(ins.point_id(x, a.steps)[1:]) for x in ins.tail_points(a.steps, flow)} & want
            full = lambda got: got >= want - start_ids
            shown = lambda got: got | (rounds["seen"] & start_ids)
            job["missing"] = []
            job["skip"], job["skip_note"] = [], None
            where = {int(ins.point_id(x, a.steps)[1:]): f"{x.get('path') or x.get('f') or ''}:{x.get('l')}" for x in a.steps}

            def skipped(cur):
                """这一轮带起点、走到了结束那一步（没有结束那一步的，读到了清零），流程里排在收到的最后那个点前面、却没收到的点：
                后面的都到了，就它没到，说明它插的那一句设备这样用时不经过（比如只在某种设置下才走的那一支）。
                可能在清零之后才打出来的点（tail）不算，它们在清零前后都可能到。"""
                if start_ids and not cur & start_ids or (end_id is not None and end_id not in cur):
                    return set()
                last = max((order.index(k) for k in cur - start_ids if k in order), default=-1)
                return {k for k in want - start_ids - tail_ids - cur if k in order and order.index(k) < last}

            def skip_note(ks):
                ks = sorted(ks, key=order.index)
                names_ = "」「".join(names.get(f"P{k}") or f"P{k}" for k in ks)
                at = "、".join(f"「{names.get(f'P{k}') or f'P{k}'}」在 {where.get(k)}" for k in ks)
                return (f"「{names_}」这一轮没走到：它后面的点都收到了、这一轮也走完了，说明这个点插的那一句设备这样用时不经过"
                        f"（比如只在某种设置、某种模式下才走的那一支）。{at}。"
                        "回到「找关键步骤」把它删掉，或者换成每轮都会走的那一句，再插一次")

            def counts(st, start_us):
                """这一行算不算这一轮收到的：起点之后的才算（10-25 真机：唤醒提示音在起点之前走了回复那几个点，试一次照样判通过，
                提示音那次的"末段输出"还被交给第 1 轮）。起点本身算；不知道起点（没有起点那一步）的照旧都算。"""
                return not start_ids or (st is not None and (st[0] in start_ids or (start_us is not None and st[1] >= start_us)))

            def _late_tail(pid, us):
                """上一轮（有起点的）清零之后、下一个起点之前打出来的、可能晚到的点：晚了多久。晚过 1 秒的记下（算不进上一轮）。"""
                rs = rounds.get("reset")
                if pid not in tail_ids or not rs or not rs["had"] or rs["us"] is None:
                    return
                gap = us - rs["us"]
                if meas.LATE_US < gap <= TAIL_MAX_US:
                    tl = rounds.setdefault("too_late", {})
                    tl[pid] = max(tl.get(pid, 0), gap)

            def feed(line):
                p = meas.parse_line(line)
                tm = TAIL.search(line)
                if tm:  # 设备说：可能晚到的点在清零 1 秒之后才走到（下一个起点之前），码表算不进上一轮
                    _late_tail(int(tm.group(1)), int(tm.group(2)))
                    return
                if line.strip().startswith("FSW,RESET"):
                    cur = rounds["cur"]
                    sk = skipped(cur) if cur and not full(cur) else set()
                    if sk:  # 走完了一轮、后面的都到了，就这几个没到：页面上马上说；连着两轮都这样就不用等满时间了
                        prev_sk = rounds.get("skip") or set()
                        rounds["skip"] = sk
                        rounds["skip_n"] = rounds.get("skip_n", 0) + 1 if sk & prev_sk else 1
                        job["skip"] = [{"name": names.get(f"P{k}") or f"P{k}", "at": where.get(k)} for k in sorted(sk, key=order.index)]
                        job["skip_note"] = skip_note(sk)
                    rounds["reset"] = {"us": p[1] if p else None, "had": bool(cur & start_ids) or (not start_ids and bool(cur))}
                    # 这一轮有起点、走过起点、还没到结束那一步、没收全就被清零：先记着，清零后真来了这一轮后面的点才算"半路被清零"
                    rounds["maybe_cut"] = {"after": rounds.get("last_id"), "had": set(cur), "later": set()} \
                        if start_ids and cur & start_ids and end_id is not None and end_id not in cur and not full(cur) \
                        and rounds.get("last_id") is not None else None
                    if (rounds["cur"] & start_ids or not start_ids) and full(rounds["cur"]):
                        # 带起点、收全了的一轮读到了结束标记：记着，试通时交给多轮实测（带上这一轮最后补打的 LATE 行）
                        rounds["last_full"] = {"lines": list(rounds.get("tail") or []), "at": time.time()}
                        rounds["ended"] = True
                    rounds["prev"] = (rounds["cur"], p[1] if p else None, rounds["start_us"], list(rounds.get("tail") or []))
                    rounds["cur"], rounds["raw"], rounds["start_us"], rounds["last_id"] = set(), set(), None, None
                    rounds["tail"] = []
                    return
                lm = meas.LATE.search(line)
                st = (int(p[0][1:]), p[1]) if p else (int(lm.group(1)[1:]), int(lm.group(2))) if lm else None
                if st and rounds["start_us"] is None and not (st[0] in start_ids):
                    _late_tail(*st)
                if st:
                    rounds.setdefault("tail", []).append(line)  # 这一轮到现在的计时点：试通时交给多轮实测，这一轮接着算（不用再多说一句）
                raw = ins.count_marks([line]) & want
                rounds["seen"] |= raw
                pv = rounds.get("prev")
                # 设备回到聆听（结束标记）后 1 秒内、下一轮还没开始时才放完的最后一声：补上就收齐的，算上一轮的（和多轮实测同一个做法）
                late = raw if pv and counts(st, pv[2]) else set()
                if late and not rounds["cur"] and pv and pv[0] and pv[1] is not None and st and 0 <= st[1] - pv[1] <= meas.LATE_US and full(pv[0] | late) and not full(pv[0]):
                    pv[0].update(late)
                    rounds["best"] = set(pv[0])
                    rounds["last_full"] = {"lines": pv[3] + [line], "at": time.time()}  # 这一轮已经结束：连同晚到的这一声交给多轮实测
                    rounds["ended"] = True
                    return
                mc = rounds.get("maybe_cut")
                if mc and p and st and rounds["start_us"] is None:
                    if st[0] in start_ids:
                        rounds["maybe_cut"] = None  # 下一轮开始了：没看到被切掉的那一截
                    elif st[0] in want and st[0] not in mc["had"] and st[0] in order and mc["after"] in order \
                            and order.index(st[0]) > order.index(mc["after"]):
                        # 清零之后、下一个起点之前来的点：流程里排在被切断处后面、那一轮还没收到的，就是被切掉的那一截
                        mc["later"].add(st[0])
                        rounds["cut"] = mc
                if st and st[0] in start_ids and rounds["start_us"] is None:
                    rounds["start_us"] = st[1]
                rounds["raw"] |= raw
                got = raw if counts(st, rounds["start_us"]) else set()
                rounds["cur"] |= got
                if rounds["cur"] - start_ids:  # 这一轮走到流程里最后面的那一步（半路被清零时说"在哪一步之后"）
                    rounds["last_id"] = max(rounds["cur"] - start_ids, key=lambda k: order.index(k) if k in order else -1)
                if (full(rounds["cur"]), len(rounds["cur"])) >= (full(rounds["best"]), len(rounds["best"])):
                    if full(rounds["cur"]) and not full(rounds["best"]):
                        rounds["full_at"] = time.time()
                    rounds["best"] = set(rounds["cur"])

            def on_line(line):
                if superseded():
                    return
                job["heard"] = time.time()  # 调试口收到过东西（哪怕不是计时点）：口是通的。一直没有就是口卡住了，页面提示拔插 USB
                if not job.get("dev_seen") and _plain(line):
                    job["dev_seen"] = time.time()  # 设备开机、有输出了：页面上从"设备正在重启"换成"现在做一次要测的事"
                feed(line)
                # 页面显示收得最多的那一轮：唤醒那一轮（没有起点）结束后计数不清回 0，免得像又从头来了一遍。
                # 起点还没到的这一轮先按收到的显示（缺的就是起点），判通过只算起点之后的
                now = rounds["cur"] or (rounds["raw"] if not rounds["best"] else set())
                cur = shown(now if (full(now), len(now)) >= (full(rounds["best"]), len(rounds["best"])) else rounds["best"])
                job["got"] = sorted(cur)
                job["missing"] = [names.get(f"P{k}") or f"P{k}" for k in sorted(want - cur)] if cur else []
                _log(job, line[:300])

            def enough():
                """有一轮起点之后全部收到，还要读到这一轮的结束标记才停（结束时补打的 LATE 行、紧跟着的最后一声一起交给多轮实测；
                10-25 真机：读到 LATE,P3 就停，紧跟着那行回复的末段输出没读到）。结束标记一直不来，收全后最多再等 TRY_END_WAIT 秒。"""
                if not full(rounds["best"]):
                    return False
                return bool(rounds.get("ended")) or time.time() - rounds.get("full_at", time.time()) >= TRY_END_WAIT
            t_end, lines, err = time.time() + TRY_SECONDS, None, None
            while lines is None and time.time() < t_end and not superseded():  # 烧完设备重启，调试口会断开一下再回来：等它回来再读
                ports = port_finder()
                if not ports:
                    time.sleep(wait_step / 4)
                    continue
                opened, reopen = time.time(), {"hit": False}

                def stop_or_reopen():  # 决策 127：打开调试口 8 秒还一行输出都没有，多半打开的是设备重启前那个（它断开又回来了）：关掉重开
                    if not job.get("dev_seen") and time.time() - opened > REOPEN_SECS:
                        reopen["hit"] = True
                    return superseded() or enough() or reopen["hit"] or (rounds.get("skip_n", 0) >= SKIP_ROUNDS and not full(rounds["best"]))
                try:
                    got_lines = device.read(ports[0], max(1, t_end - time.time()), on_line, stop=stop_or_reopen)
                    if not reopen["hit"] or time.time() >= t_end:
                        lines = got_lines
                    else:
                        job["log"] = "设备还没有输出，重新连一下调试口"
                        job["boot_quiet"] = True  # 开机那几行可能在重连前就打完了，设备待机不出声：页面上请人直接做一次，别一直等"开机"
                        time.sleep(wait_step)
                except Exception as e:
                    err = e
                    time.sleep(wait_step)
            if superseded():
                return
            if lines is None:
                return _fail(job, "try", "设备的调试口一直打不开。看看线插好没有，或者有没有别的软件（比如串口监视器）占着它。",
                             error=f"打不开调试口：{err}" if err else "没看到设备")
            got = shown(rounds["best"])  # 按收得最多的那一轮判（起点不要求）
            job["got"] = sorted(got)
            late = {k: v for k, v in (rounds.get("too_late") or {}).items() if k not in got}
            if late and not full(got):  # 缺的点其实走到了，只是在每轮清零之后才打出来（晚过 1 秒）：说清楚是哪一个
                job["try_fails"] = job.get("try_fails", 0) + 1
                k = max(late, key=lambda x: order.index(x) if x in order else -1)
                s = next((x for x in a.steps if ins.point_id(x, a.steps) == f"P{k}"), {})
                n, secs = names.get(f"P{k}") or f"P{k}", late[k] / 1e6
                end_n = names.get(f"P{end_id}") if end_id else None
                if s.get("once") == "once" and s.get("line") != "start":  # 一轮只走一次：码表重插时把清零挪到它后面
                    _redo_set(a.project_dir, f"「{n}」在每轮清零之后 {secs:.1f} 秒才走到，算不进这一轮；一轮其实到「{n}」才完",
                              end={"end": f"P{k}", "name": s.get("n")})
                    msg = (f"「{n}」走到了，但在每轮清零{('（「' + end_n + '」那里）') if end_n else ''}之后 {secs:.1f} 秒才打出来，算不进这一轮。"
                           f"一轮其实到「{n}」才完：回到「插计时点」那一步点「重新插入」，码表会把清零挪到「{n}」后面。")
                    why = "清零放在码表按步骤先后找到的结束那一步；试一次看到还有一步更晚，这一步才是一轮真正的结束。"
                else:
                    msg = (f"「{n}」走到了，但它一轮会走好几次，最后那次在每轮清零{('（「' + end_n + '」那里）') if end_n else ''}之后 {secs:.1f} 秒才打出来，"
                           f"算不进这一轮；清零没法挪到它后面（它每轮走好几次）。回到「找关键步骤」把「{n}」去掉，或者换成一轮只走一次、真正最后发生的那一步，再插一次。")
                    why = f"码表只把清零后 {meas.LATE_US // 1_000_000} 秒内打出来的算回上一轮；再晚就分不清是这一轮的尾巴，还是下一轮开始前的动静。"
                return _fail(job, "try", msg, why=why, error=f"（收到 {len(got)} / {len(want)} 个）",
                             missing=[names.get(f"P{x}") or f"P{x}" for x in sorted(want - got)], offer_restore=job["try_fails"] >= 2)
            sk = rounds.get("skip") if not full(got) else None
            if sk and (sk & (want - got) or rounds.get("skip_n", 0) >= SKIP_ROUNDS):
                job["try_fails"] = job.get("try_fails", 0) + 1
                early = rounds.get("skip_n", 0) >= SKIP_ROUNDS
                return _fail(job, "try", skip_note(sk) + "。" + (f"连着 {rounds['skip_n']} 轮都是这样，码表没再等满 {TRY_SECONDS // 60} 分钟。" if early else ""),
                             why="同一轮里排在它后面的点都收到了，它却一次没来：不是没做完，是设备不走它插的那一句。再试一次也一样。",
                             error=f"（收到 {len(got)} / {len(want)} 个）", missing=[names.get(f"P{k}") or f"P{k}" for k in sorted(want - got)],
                             offer_restore=job["try_fails"] >= 2, skip=job.get("skip"))
            cut = rounds.get("cut")
            if cut and not full(got):
                job["try_fails"] = job.get("try_fails", 0) + 1
                at_n = names.get(f"P{cut['after']}") or f"P{cut['after']}"
                rest = "」「".join(names.get(f"P{k}") or f"P{k}" for k in sorted(cut["later"], key=order.index))
                note = (f"每轮清零那一句放错了：每一轮在「{at_n}」之后就被清零，后面的「{rest}」都算不进这一轮。"
                        "清零要放在一轮结束、回到等待的地方，不能放在每次换状态、每一步都会走的地方")
                _redo_set(a.project_dir, note)  # 回到「插计时点」重插时告诉 AI（存在项目文件夹，码表重开也在）
                return _fail(job, "try", note + "。再试也一样，回到「插计时点」那一步重新插一次（码表会把这个问题告诉 AI）。",
                             why="设备真的走完了这一轮（后面的点都打出来了），只是半路被清零，码表没法把它们算进同一轮。",
                             error=f"（收到 {len(got)} / {len(want)} 个）", missing=[names.get(f"P{k}") or f"P{k}" for k in sorted(want - got)],
                             offer_restore=job["try_fails"] >= 2)
            if got and not full(got) and len(got) >= need_n:  # 差几个：不算试通，否则正式实测每一轮都会"没收全"
                job["try_fails"] = job.get("try_fails", 0) + 1
                miss = [names.get(f"P{k}") or f"P{k}" for k in sorted(want - got)]
                return _fail(job, "try", f"{TRY_SECONDS // 60} 分钟里收得最全的一轮是 {len(got)} / {len(want)} 个计时点，一直没收到「{'」「'.join(miss)}」。"
                             "多轮实测每一轮都要全部收到才算，这样直接开始会一直没收全。",
                             why="可能这一次没走到这几步（比如只在某些情况下才走的分支），也可能计时点插的位置走不到。"
                                 "点「再试一次」完整做一次要测的事；还是收不到，回到「插计时点」那一步换个位置，或者删掉这一步。",
                             error=f"（收到 {len(got)} / {len(want)} 个）", missing=miss, offer_restore=job["try_fails"] >= 2)
            if got and not full(got) and len(got) < need_n:  # 只收到零星几个：不算试通（决策 124）
                job["try_fails"] = job.get("try_fails", 0) + 1
                return _fail(job, "try", f"只收到 {len(got)} / {len(want)} 个计时点，这一次没走完。点「再试一次」，然后完整做一次要测的事。",
                             why="可能刚才只做了一半（比如只让设备准备好、还没真正做那件事），或者设备还没准备好。", error=f"（收到 {len(got)} / {len(want)} 个）",
                             offer_restore=job["try_fails"] >= 2)
            if not got and rounds["seen"] and start_ids:  # 收到了计时点，但都在起点之前（比如只喊了唤醒词、没说完一句）
                job["try_fails"] = job.get("try_fails", 0) + 1
                sn = next((names.get(f"P{k}") for k in sorted(start_ids) if names.get(f"P{k}")), None) or "起点"
                return _fail(job, "try", f"收到了计时点，但一直没有起点「{sn}」。点「再试一次」，然后完整做一次要测的事。",
                             why="起点之前收到的不算这一轮（比如只喊了唤醒词，设备放了一声提示音）。完整做一次，起点之后的才算。",
                             error=f"（起点「{sn}」一次都没收到）", offer_restore=job["try_fails"] >= 2)
            if not got:
                job["try_fails"] = job.get("try_fails", 0) + 1
                odd = sum(1 for l in lines if _garbled(l))
                why = ("收到的都是乱码，多半是波特率不对（码表按 115200 读）。" if lines and odd >= len(lines) / 2 else
                       # 10-21 真测：调试口还在，但一个字节都读不到，连乐鑫烧录工具也连不上；拔插 USB 线就好了
                       "调试口一个字都没收到，连设备平常的输出都没有：多半是电脑上的 USB 口卡住了（口还在，但收不到东西）。"
                       "拔下 USB 线，等 2 秒再插回去，然后点「再试一次」。拔插后还是这样，看看有没有别的软件（比如串口监视器）占着它。"
                       if not job.get("heard") and not lines else
                       "常见原因：没触发到；串口被别的软件占着（比如串口监视器）；波特率不对（码表按 115200 读）。")
                return _fail(job, "try", f"{TRY_SECONDS // 60} 分钟里没收到计时点。点「再试一次」，然后做一次要测的事。", why=why,
                             error=f"（{TRY_SECONDS} 秒内没有计时点输出）", offer_restore=job["try_fails"] >= 2)
            job.update(status="done", log=f"试通了：收到 {len(got)} / {len(want)} 个计时点")
            # 交给多轮实测、算第 1 轮的：试通那一刻正在进行的这一轮；它没有起点（比如只喊了唤醒词，或者上一轮刚结束）时，
            # 用试一次里最近一个已经收完、带起点的那一轮（10-20 用户：唤醒后第一句被打断就在试一次里收完了、不算，得多说一句）
            tail, lf, ended = list(rounds.get("tail") or []), rounds.get("last_full"), False
            if not ins.count_marks(tail) & start_ids and lf and time.time() - lf["at"] <= CARRY_SECS:
                tail, ended = lf["lines"], True
            fsw["carry"] = {"lines": tail, "at": time.time(), "ended": ended}
            T().end("try")
            return True

        order = ["build", "flash", "try"] if start != "try" else ["try"]  # 烧完直接试一次，不再停下等人点"准备好了"

        def run():
            for name in order[order.index(start) if start in order else 0:]:
                if job.get("cancel"):  # 点了「停止测量」：后面几步不做了
                    return
                if {"build": build, "flash": flash, "ready": ready, "try": try_once}[name]() is not True:
                    return

        def crashed(e):  # 比如烧录时加载乐鑫环境出错：按当时在做的那步报失败，页面上照样能点重试
            if job["status"] not in ("running", "waiting_device"):
                return
            kind = {"flash": "burn", "try": "try"}.get(job.get("phase"), "build")
            if isinstance(e, ToolEnvError):
                return _fail(job, kind, ToolEnvError.message, error=_detail(e), cause="tool")
            _fail(job, kind, {"burn": "烧录时码表出错了，没写进设备：点「重新烧录」再试一次。",
                              "try": "试一次时码表出错了：点「再试一次」。"}.get(kind, "编译时码表出错了：点「重新编译」再试一次。"),
                  error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return job

    @app.post("/api/fsw/run")
    def fsw_run(body: FswRunBody):
        job = fsw["job"]
        if not job or job["status"] in ("running", "waiting_device") or (job["status"] == "failed" and job["err"]["kind"] == "insert") \
                or (body.from_ == "try" and not job.get("port") and not port_finder()):
            raise HTTPException(409, "还没插好计时点，或者正在做")
        return _run_from(body.from_ if body.from_ in ("build", "flash", "try") else "build")

    @app.post("/api/fsw/stop")
    def fsw_stop():
        """「停止测量」：不再烧录、试测马上停下让出调试口；正在编译的编完就停。"""
        job = fsw["job"]
        if job and job["status"] in ("running", "waiting_device", "ready_try"):
            job.update(cancel=True, status="stopped", log="已停止")
        return job or {"status": "idle"}

    @app.post("/api/fsw/drop")
    def fsw_drop(body: FswDropBody):
        """编译出错时"撤掉这处计时点，重新编译"：只撤这一处，其他的照样插着。"""
        job, a = fsw["job"], fsw["args"]
        if not job or not fsw["plan"]:
            raise HTTPException(409, "还没插好计时点")
        backup = _fsw_dirs(a)[0]
        try:
            rec = ins.drop(a.work, fsw["plan"], body.step, backup)
        except Exception as e:
            rec = {"ok": False, "message": f"撤掉这处计时点时码表出错了（{e}），点\"重新插入\"再试一次。"}
        if not rec["ok"]:
            _fail(job, "insert", rec["message"])
            return job
        fsw["plan"] = {**fsw["plan"], "inserts": [x for x in fsw["plan"]["inserts"] if x["step"] != body.step]}
        # 后面的计时点不重新编号（设备上打印的还是插入时的编号）：编号记在步骤里，多轮实测按它对上
        a.steps = [{**x, "fsw": ins.point_id(x, a.steps)} for x in a.steps if ins.point_id(x, a.steps) != body.step]
        (Path(a.project_dir) / "fsw-args.json").write_text(json.dumps(a.model_dump(), ensure_ascii=False), encoding="utf-8")
        if rec.get("ok"):
            _save_inserted(a.project_dir, a.steps, rec["steps"])
        job.update(view=ins.diff_view(rec, a.steps), total=len(rec["steps"]), dropped=(job.get("dropped") or []) + [body.step])
        return _run_from("build")

    # ---- 多轮实测：一轮轮收，收齐算一轮；没收全自动补；停止 / 接着测 / 从头重测；断开自动暂停 ----
    col = {"job": None, "lock": threading.Lock()}

    def _col_view(job):
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def _save_inserted(project_dir, steps, points):
        """这次实际插进去的步骤和计时点编号存一份（fsw-inserted.json）：工作副本还原时插入记录会删掉，
        多轮实测、码表重开后接着测都按这份收点。"""
        try:
            (Path(project_dir) / "fsw-inserted.json").write_text(
                json.dumps({"steps": steps, "points": sorted(points, key=lambda k: int(k[1:]))}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _inserted_page(body):
        """多轮实测按这次实际插进去的计时点算，不按 AI 最初找到的步骤（stds.inserted_page）。
        依次用：插入记录（record.json，真插上了哪几个点）、插好时存的 fsw-inserted.json、后台插入时的步骤、
        项目文件夹里的 fsw-args.json、网页传来的步骤；都没有就按网页传来的结果页算（不改）。"""
        steps, inserted = body.steps, None
        if body.project_dir:
            proj = Path(body.project_dir)
            saved = {}
            try:
                saved = json.loads((proj / "fsw-inserted.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            a = fsw.get("args")
            if a and Path(a.project_dir).resolve() == proj.resolve():
                steps = a.steps
            elif saved.get("steps"):
                steps = saved["steps"]
            else:
                try:
                    steps = json.loads((proj / "fsw-args.json").read_text(encoding="utf-8"))["steps"]
                except (OSError, ValueError, KeyError):
                    pass
            rec = ins.current(proj / "fsw-backup")
            if rec and rec.get("steps"):  # 插入记录里没写插了哪些点（比如只记了附加值）：不核对
                inserted = set(rec["steps"])
            elif saved.get("points"):  # 插入记录已经随还原删掉：用插好时存的那份
                inserted = set(saved["points"])
        if steps is None and inserted is None:
            return body.page
        try:
            page = stds.inserted_page(body.page, steps, inserted)
        except (KeyError, TypeError, ValueError):  # 存的步骤和结果页对不上（比如老版本留下的）：按网页传来的算
            return body.page
        if body.project_dir:
            # 一轮的结束：和放清零用的同一步（插的时候码表定的，fsw-plan.json），报告里总的那条线按它收尾
            try:
                end = (json.loads((Path(body.project_dir) / "fsw-plan.json").read_text(encoding="utf-8")).get("end") or {}).get("step")
            except (OSError, ValueError, AttributeError):
                end = None
            if end and end in (page.get("main") or []):
                page = {**page, "roundEnd": end}
        if not page["segs"]:
            raise HTTPException(409, "这次插进去的计时点连不成一段，量不出用时：回到「找关键步骤」那一步，至少留下起点和终点再插一次。")
        return page

    @app.post("/api/collect/start")
    def collect_start(body: CollectBody):
        if not 3 <= body.rounds <= 100:
            raise HTTPException(400, "轮数要在 3～100 轮之间。")
        old = col["job"]
        if old and old["status"] in ("running", "waiting_device", "paused"):
            old["_stop"] = True
            time.sleep(0.05)
        std_hash = None
        values = None
        page = _inserted_page(body) if body.page else None
        if page:  # 真找完的步骤：按代码位置对上码表自带的合理范围标准
            standard = stds.find(page.get("steps") or [])
            if body.project_dir:  # 插计时点时顺带记了附加值的计时点（插入记录里），看附加值的问题规则才用
                values = (ins.current(Path(body.project_dir) / "fsw-backup") or {}).get("values") or None
            scenario = stds.real_scenario(page, standard, values)
            std_hash = stds.digest(standard)
        elif body.demo in demos:
            scenario = demos[body.demo]["scenario"]
        else:
            raise HTTPException(400, "不知道要测什么：先找关键步骤")
        spec = meas.Spec.from_scenario(scenario)
        pending = []
        if body.resume and old:
            job, coll = old, old["_coll"]
            if old.get("suspect"):  # 停下来问过（自动校验没通过 / 某个点连着几轮不对），选了"接着测"：记下来，报告里写
                _choice(job, "continue", old["suspect"])
            with old["_lock"]:
                pending = coll.finish()  # 停下时已经收全的那一轮照算；没收全的那一轮不算
        else:
            base = Path(body.project_dir) if body.project_dir else live["data"] / "demo-runs"
            run = base / "runs" / time.strftime("%Y%m%d-%H%M%S") if body.project_dir else base / f"{body.demo}-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
            run.mkdir(parents=True, exist_ok=True)
            if page:  # 这次测的是什么（实际插进去的计时点），留一份：首页"以前测过的"按它重算
                (run / "run.json").write_text(json.dumps({"page": page, "values": values, "what": body.what or page.get("mapWhat"),
                                                          "measured_at": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False), encoding="utf-8")
            coll = meas.Collector(spec)
            job = {"rounds": [], "raw_log": str(run / "raw.log"), "need": spec.need, "_coll": coll, "_scenario": scenario,
                   "_lock": threading.Lock(),  # 读调试口的线程和"收全后等一会儿收尾"的线程都会动这一轮
                   "demo": None if page else body.demo, "_page": page, "_std": std_hash, "_values": values, "_project_dir": body.project_dir,
                   "measured_at": time.strftime("%Y-%m-%d %H:%M"),
                   # 这次真的场景：网页多轮实测页按它画线、标问题，不借演示数据
                   "lines": [{"name": l["name"], "range": l.get("range"), "slow": l.get("slow")} for l in scenario["lines"]],
                   "problems": [{"key": p["key"], "name": p.get("name", p["key"]), "level": p.get("level", "warn")}
                                for p in scenario.get("problems") or []],
                   "experience": main_experience(scenario)}
            col["job"] = job
        job.update(status="running", target=body.rounds, message="", progress=[0, len(spec.need)], last="", _stop=False, suspect=None)
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
            with job["_lock"]:
                take(coll.feed(line))

        def ask(kind, name, why):
            """测到一半看出某个计时点连着几轮不对：和第 1 轮自动校验没通过一样停下来问（看看改计时点，还是接着测），
            不只是一行提示（10-26 真测：提示了没人停，报告里那几段只有 1 轮的数）。同一个点问过、选了接着测就不再问。"""
            if (kind, name) in job.setdefault("_asked", set()):
                return
            job["_asked"].add((kind, name))
            job["suspect"] = [{"kind": kind, "name": name, "why": why}]
            job["_stop"] = True

        def take(results):
            """收尾的轮记下来；进度、提示跟着更新。"""
            job["late"] = list(coll.late)  # 晚过 1 秒窗口才到的尾巴点：不算数，报告里点名
            for r in results:
                if r.get("update"):  # 已经记下的一轮，最后一声晚到：只换数，格子颜色和轮数不变
                    k = next((i for i in range(len(job["rounds"]) - 1, -1, -1)
                              if job["rounds"][i].get("ok") and job["rounds"][i].get("n") == r["n"]), None)
                    if k is not None:
                        job["rounds"][k] = {x: v for x, v in r.items() if x != "update"}
                    continue
                if r.get("half"):  # 开始读时这一次已经过了起点：不算，提示再做一次（决策 101）
                    job["half"] = job.get("half", 0) + 1
                    job["half_hint"] = ("这一次开头没收到（码表开始听的时候它已经开始了），不算。"
                                        "刚才要是在做要测的事，再做一次；只是让设备准备好（比如唤醒）的话，不用管。")
                    continue
                if r.get("nostart"):  # 设备没记到起点：不算一轮，不出格子，报告的数据检查里写上（决策 133）
                    job["nostart"] = job.get("nostart", 0) + 1
                    job["nostart_run"] = job.get("nostart_run", 0) + 1
                    if job["nostart_run"] >= MISS_RUN:  # 试一次不等起点（唤醒那一轮本来没有），起点在这里核对：连着几轮都没记到就点名
                        sn = ((job.get("_scenario") or {}).get("start") or {}).get("name") or "起点"
                        job["half_hint"] = (f"连着 {job['nostart_run']} 次没记到起点「{sn}」，这几次都不算。要是每次都完整做了要测的事，"
                                            "多半是起点插的位置走不到：先停下，回到「插计时点」那一步换个位置。")
                    continue
                job["nostart_run"] = 0
                if r["ok"]:
                    job["half_hint"] = ""
                    job["stale_run"] = {}
                    job["miss_run"] = {}
                    # 中间的点不是这一轮的（打断回答后常见）：这一轮照样算；同一个点连着几轮都这样，多半是插的位置不对，点名
                    run = {m: job.get("gap_run", {}).get(m, 0) + 1 for m in r.get("gap") or []}
                    job["gap_run"] = run
                    worst = max(run.items(), key=lambda kv: kv[1], default=None)
                    if worst and worst[1] >= MISS_RUN:
                        job["half_hint"] = (f"「{worst[0]}」连着 {worst[1]} 轮都不在这一轮该在的位置（没打出来，或者比它前一步还早），这几轮用到它的两段空着。"
                                            "要是上一轮没有被中途打断，多半是这个计时点插的位置不对：先停下，回到「插计时点」那一步换个位置，或者删掉它。")
                        ask("gap", worst[0], job["half_hint"])
                elif not r.get("stale"):  # 没收全：同一个点连着几轮都没收到，点名（多半是这一步没走到，或者插的位置走不到）
                    run = {m: job.get("miss_run", {}).get(m, 0) + 1 for m in r.get("missing") or []}
                    job["miss_run"] = run
                    worst = max(run.items(), key=lambda kv: kv[1], default=None)
                    if worst and worst[1] >= MISS_RUN:
                        job["half_hint"] = (f"「{worst[0]}」连着 {worst[1]} 轮都没收到：多半是这一步没走到，或者计时点插的位置走不到，"
                                            "再测也一样，先停下，回到「插计时点」那一步换个位置，或者删掉它。")
                        ask("miss", worst[0], job["half_hint"])
                elif r.get("stale"):
                    run = job.setdefault("stale_run", {})
                    for m in r["missing"]:
                        run[m] = run.get(m, 0) + 1
                    worst = max(run.items(), key=lambda kv: kv[1])
                    if worst[1] >= 2:
                        name = worst[0].split("（")[0]
                        job["half_hint"] = (f"「{name}」连着 {worst[1]} 轮都记在起点之前，不像是这一轮的事，再测也一样。"
                                            "多半是这个计时点插的位置不对：先停下，回到「插计时点」那一步换个位置，或者删掉它。")
                        ask("stale", name, job["half_hint"])
                job["rounds"].append(r)
            first = next((x for x in job["rounds"] if x["ok"]), None)
            if first and not job.get("_checked") and not (coll.prev and coll.prev.get("sent") is not None and coll.prev["sent"].get("n") == first["n"]):
                # 第 1 轮收齐（晚到的最后一声也等完了）：自动校验计时点位置，不对就停下，不让后面白测（决策 158）
                job["_checked"] = True
                bad = meas.suspects(first, job["_scenario"])
                if bad:
                    job["suspect"] = bad
                    job["_stop"] = True
            job["progress"], job["last"], job["have"] = list(coll.progress()), coll.last, coll.have()
            if ok_count() >= job["target"] and not coll.prev:  # 最后一轮还在等晚到的最后一声：等完（见 flush）再停
                job["_stop"] = True

        stop = lambda: job["_stop"]
        take(pending)

        def watch_ready():
            """一轮收全后（进度已经显示收全了），下一轮一直没开始：等 ROUND_SETTLE 秒收尾，最后一轮也能算上。"""
            while not stop() and job is col["job"]:
                with job["_lock"]:
                    take(coll.flush(ROUND_SETTLE))
                time.sleep(min(0.2, ROUND_SETTLE / 4))

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
                mid = coll.started and coll.cur and not coll.done and not coll.ready  # 一轮做到一半
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

        carry = fsw.pop("carry", None) if body.source == "device" and not body.resume else None
        if carry and time.time() - carry["at"] < CARRY_SECS:
            # 试通时这一轮已经过了起点（比如唤醒后说的那一句，起点一到就试通了）：把已收到的计时点先喂进来，这一轮照样算，
            # 不再当"开头没收到"白说（真测第 29 次）。不喂结束标记：这一轮后面的点要是在换调试口时丢了，照旧算开头没收到，不算没收全
            if any((meas.parse_line(l) or (None,))[0] == coll.spec.start for l in carry["lines"]):
                for l in carry["lines"]:
                    on_line(l)
                with job["_lock"]:
                    take(coll.close_carried(ended=bool(carry.get("ended"))))  # 带过来的这一轮已经收全：马上交出，格子马上变绿
            else:  # 这一轮没有起点（只喊了唤醒词，其余收齐就试通了）：不是要测的那件事，到下一个结束标记前的都不算，也不报"开头没收到"
                coll.skip_round()

        def run_device():
            def helper_crashed(e):  # 看收尾、看安静的小线程出错：停下这次实测（已测的留着），不让最后一轮一直收不了尾
                job["_stop"] = True
                crashed(e)
            threading.Thread(target=_guarded(watch_quiet, helper_crashed), daemon=True).start()
            threading.Thread(target=_guarded(watch_ready, helper_crashed), daemon=True).start()
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
                except Exception as e:  # 拔线了：已测的留着（已经收全的那一轮照算），插回后接着测
                    with job["_lock"]:
                        take(coll.finish())
                    job["pauses"] = job.get("pauses", 0) + 1
                    opened = quiet["lines"] + len(job["rounds"]) > got0 or job.get("progress", [0])[0]
                    job.update(status="paused", error=str(e), hint="",
                               message=f"设备断开了，已测的 {ok_count()} 轮都留着：插回 USB，或设备已自动关机就按电源键开机，检测到会自动接着测。" if opened else
                               f"打不开设备的调试口（{ports[0]}）：可能被别的软件占着（比如串口监视器、另一个码表窗口），"
                               f"关掉它，码表会自动重试。已测的 {ok_count()} 轮都留着。")
                    time.sleep(wait_step)

        def run():
            try:
                (run_device if body.source == "device" else run_demo)()
            finally:
                raw.close()
                with job["_lock"]:
                    take(coll.finish())
                if job is col["job"]:
                    status = "suspect" if job.get("suspect") else "done" if ok_count() >= job["target"] else "stopped"
                    auto = status == "done" and body.source == "device" and body.project_dir  # 10 轮收齐：自己还原成原版，不靠人记得去点
                    if auto:  # 先记上"正在还原"再报测完：网页一看到测完就跟着看还原，不会读到上一次的还原结果
                        fsw["job"] = fsw["job"] or {}
                        fsw["job"]["restore"] = {"running": True, "auto": True}
                    job["status"] = status
                    _keep_rounds(job)
                    if body.source == "device":
                        T().end("collect")
                        if auto:
                            threading.Thread(target=_guarded(lambda: fsw_restore(RestoreBody(project_dir=body.project_dir), auto=True),
                                                             _auto_restore_crashed), daemon=True).start()

        def _auto_restore_crashed(e):  # 自己还原时码表出错：别一直写"正在还原"，留按钮让人再点
            if fsw["job"] is not None:
                fsw["job"]["restore"] = {"auto": True, "flashed": False, "error": _detail(e), "detail": _plain(_detail(e)),
                                         "say": "原版没烧上：还原时码表出错了，点「还原成原版」再试一次。"}

        def crashed(e):  # 上面已经停下（已测的留着）：说清楚为什么停了
            if job is col["job"]:
                job.update(message=f"实测时码表出错了，已测的 {ok_count()} 轮都留着：点「接着测」继续。", error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return _col_view(job)

    def _choice(job, choice, items):
        """停下来问的时候选了什么（接着测 / 去改计时点），存进这次的结果文件夹（choices.json），报告的数据检查里写。"""
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "choice": choice,
               "items": [{k: x.get(k) for k in ("kind", "name", "why", "seconds") if x.get(k) is not None} for x in items or []]}
        job.setdefault("choices", []).append(rec)
        try:
            (Path(job["raw_log"]).parent / "choices.json").write_text(json.dumps(job["choices"], ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, KeyError):
            pass

    class ChoiceBody(BaseModel):
        choice: str

    @app.post("/api/collect/choice")
    def collect_choice(body: ChoiceBody):
        """停下来问的时候选了"去改计时点"（接着测由 /api/collect/start resume 记）：记下来。"""
        job = col["job"]
        if not job or not job.get("suspect"):
            raise HTTPException(409, "现在没有在问")
        if body.choice not in ("fix", "continue"):
            raise HTTPException(400, "只能选 fix 或 continue")
        _choice(job, body.choice, job["suspect"])
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

    def _start_lag(raw, sc):
        """核对过起点比人真正做完晚多少（比如用 Mac 录音核对）：记在项目文件夹的 calib.json，
        起点还是同一个计时点（按名字对）才用（决策 135）。"""
        if not raw:
            return None
        try:
            lag = json.loads((Path(raw).parent.parent.parent / "calib.json").read_text(encoding="utf-8")).get("start_lag")
        except (OSError, ValueError):
            return None
        name = (sc.get("point_names") or {}).get((sc.get("start") or {}).get("point"))
        return lag if lag and name and lag.get("point_name") == name else None

    def _keep_rounds(job):
        """真测（有项目文件夹）测完：把收全的每一轮存在原始日志旁边，以后和它比都读这份（meas.save_rounds）。"""
        if job.get("_page") is None or not job.get("_project_dir"):
            return
        try:
            meas.save_rounds(Path(job["raw_log"]).parent, job.get("rounds") or [])
        except OSError:
            pass

    @app.get("/api/report")
    def report(demo: str | None = None):
        """报告用的判断：这次收齐的轮，按固定规则算（判快慢、两把尺子、结论标签），不用大模型。
        demo=…：这套演示数据测过就用测的，没测过就用演示数据本身的数。"""
        job = col["job"]
        ok = [r for r in (job or {}).get("rounds", []) if r["ok"]]
        same = job and (demo is None or job.get("demo") == demo)
        if ok and same:
            sc, raw = job["_scenario"], job["raw_log"]
            if job.get("_page") is not None and job.get("_project_dir"):  # 真测：体验标准按设备类别从标准库挂
                sc = job["_scenario"] = _judge_scenario(job)
                if job.get("status") in ("done", "stopped"):
                    _keep_rounds(job)  # 晚到的最后一声会改已交出的那一轮：打开报告时再存一次，存的和报告用的一样
                    if "_prev" not in job:
                        job["_prev"] = _prev_run(job, sc, ok)
        elif demo in demos:
            sc, ok, raw = demos[demo]["scenario"], _demo_rounds(demos[demo]), None
        else:
            raise HTTPException(404, "还没有测完的轮")
        prev = (job or {}).get("_prev") if ok and same else None
        sc, ok, merged = jdg.merge_overruns(sc, ok)
        out = jdg.judge(sc, ok, prev)
        out["merged"] = merged
        sc = {**sc, "first_run": not (prev and prev.get("segs") is not None)}  # 没有上次可比：第一次测
        keep = ("name", "start", "segments", "lines", "experiences", "problems", "standard", "matched", "point_names", "points",
                "first_run", "exp_skip")
        allr = (job or {}).get("rounds", []) if ok and same else []
        quality = {"ok": len(ok), "stale": len([r for r in allr if r.get("stale")]),
                   "missing": len([r for r in allr if not r.get("ok") and not r.get("stale")]), "half": (job or {}).get("half", 0) if raw else 0,
                   "nostart": (job or {}).get("nostart", 0) if raw else 0,
                   "startName": (sc.get("start") or {}).get("name", ""),
                   "gap": [{"n": r["n"], "names": r["gap"]} for r in ok if r.get("gap")],
                   "late": _late_names((job or {}).get("late") or []) if raw else [],
                   "choices": ((job or {}).get("choices") or []) if raw else []}
        out["checks"] = (out.get("checks") or []) + _run_checks(job, quality) if raw else out.get("checks") or []
        return {**out, "rounds": ok, "raw_log": raw, "scenario": {**{k: sc.get(k) for k in keep}, "experience": main_experience(sc)},
                "quality": quality, "start_lag": _start_lag(raw, sc), "code": code_ctx(job) if raw else {},
                "prev": prev,
                "measured_at": (job or {}).get("measured_at") if ok and same else None}

    def _late_names(late):
        """晚到的尾巴点按名字数次数：[{name, n, how}]。"""
        got = {}
        for x in late:
            k = (x.get("name"), x.get("how"))
            got[k] = got.get(k, 0) + 1
        return [{"name": n, "how": h, "n": c} for (n, h), c in got.items()]

    def _run_checks(job, quality):
        """这次测量过程里要提醒的（报告最上面的数据检查一起列）：开头没收到的半轮、晚到的尾巴点、停下来问过选了什么、
        插计时点时码表没把握的地方（每轮结束是哪一步说不准、结构看不懂没核对的文件）。"""
        out = []
        if quality.get("half"):
            out.append({"kind": "half", "why": f"有 {quality['half']} 次开头没收到（码表开始听的时候它已经开始了），没算进轮数，也不影响数据"})
        for x in quality.get("late") or []:
            out.append({"kind": "late", "name": x["name"],
                        "why": f"「{x['name']}」有 {x['n']} 次在每轮清零 1 秒以后才到，没算进那一轮"
                               f"（用到它的段那几轮按清零前最后那次算，可能偏短）"})
        for c in quality.get("choices") or []:
            names = "、".join(f"「{i['name']}」" for i in c.get("items") or [] if i.get("name"))
            out.append({"kind": "choice", "choice": c["choice"],
                        "why": f"{c['at'][11:16]} 测量停下来问过（{names}不对劲），选了"
                               + ("接着测：用到它的段这次的数要打折扣看" if c["choice"] == "continue" else "去改计时点")})
        try:
            plan = json.loads((Path(job["_project_dir"]) / "fsw-plan.json").read_text(encoding="utf-8")) if job.get("_project_dir") else {}
        except (OSError, ValueError):
            plan = {}
        for f in plan.get("fixes") or []:
            if f.get("kind") == "reset_end_unsure":
                out.append({"kind": "plan", "why": f"插计时点时：每轮结束是哪一步说不准（{f.get('why')}），清零用的是 AI 给的位置"})
        for u in plan.get("unchecked") or []:
            out.append({"kind": "plan", "why": f"插计时点时：{u.get('file')} 的结构码表看不懂，这里插的 {'、'.join(u.get('steps') or [])} 没核对位置"})
        return out

    def _prev_run(job, sc, ok):
        """改完再测（任务 22）：和上次比。上次 = 同一个项目、计时点一样、收齐 10 轮以上的最近一次。
        返回 {"run", "measured_at", "n", "segs": {段: 中位数}, "lines": {线: 中位数}}；比不了返回 {"why": 一句话}。"""
        if len(ok) < rl.MIN_ROUNDS:
            return {"why": f"这次只测了 {len(ok)} 轮，不到 {rl.MIN_ROUNDS} 轮，不和上次比"}
        proj, cur = Path(job["_project_dir"]), Path(job["raw_log"]).parent.name
        names = [l["name"] for l in sc["lines"]]
        at = rl.at_now(sc)
        sigs = [rl.line_sig(sc, l, at) for l in sc["lines"]]
        other = None
        for log in sorted(proj.glob("runs/*/raw.log"), reverse=True):
            run = log.parent
            if run.name >= cur:
                continue
            meta = _run_meta(proj, run)
            if not meta or not meta.get("page"):
                continue
            try:
                psc = stds.real_scenario(meta["page"], stds.find(meta["page"].get("steps") or []), meta.get("values"))
                rounds = meas.run_rounds(run, meas.Spec.from_scenario(psc))
            except Exception:  # 老数据格式对不上：这一次不拿来比
                continue
            if len(rounds) < rl.MIN_ROUNDS:
                continue
            # 两次的段、线按计时点在代码里的位置对（重新找步骤后编号会重排，不能按 P7-P8 这种编号比）；
            # 码表新加了线、段时，两次都有的照样比；一段都对不上才不比
            pat = rl.at_now(psc)
            pts = rl.pair_segments(psc["segments"], pat, sc["segments"], at)
            pl = rl.pair_lines([l["name"] for l in psc["lines"]], [rl.line_sig(psc, l, pat) for l in psc["lines"]], names, sigs)
            if not pts:
                other = other or meta.get("measured_at") or run.name
                continue
            def iqr(v):  # 一次测量的正常波动：中间一半轮次的跨度（第 75 百分位减第 25 百分位）
                v = sorted(v)
                q = lambda p: v[0] if len(v) == 1 else v[int(p * (len(v) - 1))] + (v[min(int(p * (len(v) - 1)) + 1, len(v) - 1)] - v[int(p * (len(v) - 1))]) * (p * (len(v) - 1) % 1)
                return q(.75) - q(.25)

            def seg_vals(rs, k):
                return [r["segs"][k] for r in rs if r["segs"].get(k) is not None]

            def line_vals(rs, i):
                return [r["ends"][i] for r in rs if i < len(r["ends"]) and r["ends"][i] is not None]
            segs, noise = {}, {}
            for k in psc["segments"]:
                if k not in pts:
                    continue
                v, ck = seg_vals(rounds, k), pts[k]
                if v:
                    segs[ck] = round(jdg.med(v), 6)
                    c = seg_vals(ok, ck)
                    noise[ck] = round(max(iqr(v), iqr(c) if c else 0), 6)  # 两次各自的正常波动，取大的
            lines, noise_l = {}, {}
            for i, l in enumerate(psc["lines"]):
                if l["name"] not in pl:
                    continue
                v, cn = line_vals(rounds, i), pl[l["name"]]  # 结果用这次的线名
                if v:
                    lines[cn] = round(jdg.med(v), 6)
                    c = line_vals(ok, names.index(cn))
                    noise_l[cn] = round(max(iqr(v), iqr(c) if c else 0), 6)
            return {"run": run.name, "measured_at": meta.get("measured_at") or run.name, "n": len(rounds), "segs": segs, "lines": lines,
                    "noise": noise, "noise_lines": noise_l}
        return {"why": f"计时点和上次（{other}）不一样，不和上次比"} if other else None

    def _judge_scenario(job):
        """这份报告按什么判：真找完的步骤 + 按设备类别从标准库挂的体验标准（合理范围清单 10-19 去掉了，项目文件夹里留着的老清单不读）。"""
        page = job["_page"]
        return stds.with_default_experience(stds.real_scenario(page, stds.find(page.get("steps") or []), job.get("_values")))

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
            default = folder.is_absolute() and folder.resolve() == Path(_export_dir(body.demo)).expanduser().resolve()
            if not folder.is_absolute() or not (folder.is_dir() or default):  # 默认文件夹（新数据目录里还没有）可以新建；别的要已有
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
        # 有"第一下感觉到的结果"那条线时，最后一条（到全部做完）看内容长短、不判快慢：首页的主数用第一下那条（真测第 40 次：显示成"声音放完 10.11 秒"）
        ls = sc.get("lines") or []
        i = next((k for k, l in enumerate(ls) if l.get("first")), len(ls) - 1)
        ok = [r for r in meas.run_rounds(run, meas.Spec.from_scenario(sc)) if i < len(r["ends"]) and r["ends"][i] is not None]
        end = ls[i].get("end") if ls and i < len(ls) - 1 else None
        # 留 4 位小数：网页再取两位，和报告一样（真测第 41 次：1.325206 先四舍五入成 1.325，网页显示 1.32、报告 1.33）
        return (round(jdg.med([r["ends"][i] for r in ok]), 4), len(ok), end) if ok else (None, 0, end)

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
                    m, n, end = _run_med(proj, log.parent, meta)
                except Exception:
                    continue
                if m is not None:
                    done.append((meta, m, n, end))
            if not done:
                continue
            meta, now, n, end = done[-1]
            full = [d for d in done[:-1] if d[2] >= 10]  # 不足 10 轮（跑流程）的不拿来比，也不当"上次"
            page = meta["page"]
            metric = end or (page.get("measure") or "").split("→")[-1].strip() or "终点"
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
        """多轮实测每轮做什么：按你写的"测什么"挑模板（对话类用固定的一组句子，按轮数取前几句；别的按起点的说法，比如"按一下按键"）。"""
        t = next(t for t in ROUNDS if not t["match"] or re.search(t["match"], what))
        fill = lambda v: [fill(x) for x in v] if isinstance(v, list) else str(v).replace("{start}", start.strip() or "触发一次")
        return {"template": t["name"], **{k: fill(v) for k, v in t["fields"].items()}}

    @app.get("/api/report/export-dir")
    def report_export_dir(demo: str | None = None):
        return {"dir": _export_dir(demo)}

    exported = set()  # 导出过的文件（可能存在结果文件夹以外，比如源码旁边）：也能"在访达里显示"
    allowed_roots = lambda: [Path(live["data"]).resolve(), Path(data_dir).resolve()]

    @app.post("/api/report/open")
    def report_open(body: PathBody):
        """导出完点「打开看看」：用浏览器打开刚导出的那份报告。只开这次导出过的文件（可能存在源码旁边，不在结果文件夹里）。"""
        p = Path(body.path).expanduser().resolve()
        if p not in exported or not p.is_file():
            raise HTTPException(404, "找不到这份报告：可能被挪走或删了，重新导出一份")
        (opener or _open_file)(str(p))
        return {"ok": True}

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
        # 和报告用同一套判断（10-20 修）：同样的体验标准、同样带上次的数，"比上次慢"的建议改才有卡片，
        # 也不会因为少算了问题，误以为和上次一样而沿用旧建议
        sc0, prev = job0["_scenario"], None
        if job0.get("_page") is not None and job0.get("_project_dir"):
            sc0 = job0["_scenario"] = _judge_scenario(job0)
            if "_prev" not in job0:
                job0["_prev"] = _prev_run(job0, sc0, ok)
            prev = job0["_prev"]
        sc, ok, _ = jdg.merge_overruns(sc0, ok)
        j = jdg.judge(sc, ok, prev)
        if body.project_dir and (old := advs.saved_for(Path(body.project_dir) / "advice", j, sc)):
            adv["job"] = {**old, "status": "done", "seconds": 0, "called": False}  # 要改的问题和上次一样（比如只改了清单）：不再调用大模型
            return adv["job"]
        _, model = _model()
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

        def crashed(e):
            job.update(status="failed", kind="other", message="写建议时码表出错了，点\"重试\"再写一次。", error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
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
                    if v.get("status") in ("done", "stopped", "suspect", "idle"):
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
    def fsw_restore(body: RestoreBody | None = None, auto: bool = False):
        """还原成原版：源码放回去（和你原来的源码逐字节比对），设备插着就把后台编好的原版烧回去。
        码表重开过（内存里没有这次的记录）：按项目文件夹里存的插入记录还原。
        auto：多轮实测收齐后码表自己还原（不算人点了一次）。"""
        a = fsw["args"]
        if body and body.project_dir and (not a or Path(a.project_dir).resolve() != Path(body.project_dir).resolve()):
            try:
                a = FswInsertBody(**json.loads((Path(body.project_dir) / "fsw-args.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                a = None
        if not a:
            if auto:  # 没插过计时点（设备上本来就不是计时版）：不用还原，也不提示
                (fsw["job"] or {}).pop("restore", None)
                return {}
            raise HTTPException(409, "还没插过计时点，不用还原")
        backup, orig_fw, _ = _fsw_dirs(a)
        T().begin("restore")
        if not auto:
            T().count("clicks")
        if fsw["job"]:
            fsw["job"]["cancel"] = True  # 还在等设备烧计时版的，不烧了
        ws.ensure_clean(a.project_dir)  # 插入记录和副本对不上（源码改过、副本重新复制过）就重新复制，不拿旧备份盖
        try:
            pj = json.loads((Path(a.project_dir) / "project.json").read_text(encoding="utf-8"))
            source = pj.get("root") or pj["source"]
        except (OSError, ValueError, KeyError):
            source = None
        cmp = ins.compare(a.work, source) if source and Path(source).is_dir() else {"same": None, "diff": [], "files": 0}
        out = {"same": cmp["same"], "diff": cmp["diff"][:10], "files": cmp["files"], "flashed": False, "port": None, "waiting": False}
        ports = port_finder()
        job = fsw["job"] or {}
        fsw["job"] = job
        job["restore"] = {"running": True, "auto": auto}  # 网页据此显示"正在还原"，烧完换成结果

        def done(o):  # 测完还原：计时版那次的状态还是"试通了"（网页按它认这次真测的报告），只记还原结果
            job["restore"] = dict(o, auto=auto)
            if job.get("status") != "done":
                job["status"] = "restored"
        older = _orig_older(a.project_dir)

        def burn(port):
            for k in range(6):  # 刚测完调试口可能还没放开、或设备正在重连：等一下再烧，别马上报错（决策 129）
                try:
                    r = device.flash(orig_fw, port, lambda pct, msg: None)
                except ToolEnvError as e:
                    r = {"ok": False, "cause": "tool", "message": e.message, "error": e.detail}
                except Exception as e:  # 烧录时码表自己出错：源码已经还原了，说清楚原版没烧上，不能让接口直接报错
                    traceback.print_exc()
                    r = {"ok": False, "cause": "crash", "error": _detail(e)}
                if r.get("ok") or not re.search(r"busy|Could not open|doesn't exist|Errno 16|not configured|Errno 6|Could not configure", str(r.get("error") or ""), re.I):
                    break
                time.sleep(wait_step * 3)
                port = (port_finder() or [port])[0]
            out.update(flashed=bool(r.get("ok")), port=port, error=r.get("error"), waiting=False)
            if r.get("ok"):
                _on_device(a.project_dir, False)
                out["say"] = "已还原：源码放回原样，原版已经烧回设备。" if not older else \
                    "已还原：设备上烧回的是你改源码之前那份原版（改过的源码还没编出原版）；码表里的副本已经是你现在的源码。"
            else:
                busy = re.search(r"busy|Errno 16", str(r.get("error") or ""), re.I)  # 设备还在重连（not configured）已经在上面等过了
                out["say"] = ("源码已还原，原版没烧上：" + r["message"] if r.get("cause") == "tool" else
                              "源码已还原，原版没烧上：烧录时码表出错了，再点一次「还原成原版」。" if r.get("cause") == "crash" else
                              "源码已还原，原版没烧上：调试口被别的程序占着。关掉它（比如串口监视器）再点一次「还原成原版」。" if busy else
                              "源码已还原，原版没烧上：看看线插好没有，再点一次「还原成原版」。")
                out["detail"] = _plain(r.get("error"))  # 原话放在详情里，不堆在提示里
            done(out)
            T().end("restore")

        if ports and orig_fw.exists():
            burn(ports[0])
        elif orig_fw.exists():  # 拔了线：源码先还原，说清楚；插上线就自动把原版烧回去（决策 109）
            out.update(waiting=True, say="源码已还原。设备没插着，原版还没烧回去：插上设备就自动烧，不用再点。")
            done(out)

            def wait_and_burn():
                t_end = time.time() + 1800
                while time.time() < t_end and job.get("restore", {}).get("waiting"):
                    p2 = port_finder()
                    if p2:
                        return burn(p2[0])
                    time.sleep(wait_step)

            def burn_crashed(e):
                out.update(waiting=False, say="源码已还原，原版没烧上：烧录时码表出错了，再点一次「还原成原版」。", detail=_plain(_detail(e)))
                done(out)
            threading.Thread(target=_guarded(wait_and_burn, burn_crashed), daemon=True).start()
        else:
            out["say"] = "源码已还原。没找到编好的原版固件，设备上的计时版要等下次编好原版再烧回去。"
            done(out)
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

        def crashed(e):
            job.update(state="failed", message="安装中途码表出错了，点\"重试\"接着装。", log=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
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

    def _identify(body, known=None, flow=None):
        provider, model = _model()
        job = {"state": "running", "what": body.what, "provider": provider, "found": list(known or []), "read": 0,
               "last": "", "seconds": 0, "started": time.time(), "stage": "chain"}
        with ident["lock"]:
            ident["job"], ident["args"] = job, body

        def progress(p):
            job.update(found=p["found"], at=p["at"], read=p["read"], last=p["last"], seconds=p["seconds"],
                       stage=p.get("stage", job.get("stage")))  # 理清流程 / 选计时点

        def carry_over():
            """改完再测（任务 22）：源码变了、但计时点那几处代码没动：把上次的步骤和插法挪到新行号上存好，下面直接用，不调用大模型。"""
            if not (body.reuse and not known and body.project_dir and body.fingerprint and cfg().reuse_last):
                return
            f = idf._cache_file(body.project_dir, body.fingerprint, body.what, provider)
            if f.exists():
                return
            c = cy.carry(body.project_dir, body.work, body.what, provider)
            if not c["ok"]:
                if c["changed"]:
                    job["carry_changed"] = c["changed"]
                return
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({**c["result"], "what": body.what, "provider": provider}, ensure_ascii=False, indent=1), encoding="utf-8")
            (Path(body.project_dir) / "fsw-plan.json").write_text(json.dumps(
                {"fingerprint": body.fingerprint, "steps": c["plan_steps"], "reply": c["plan"], "ver": ins.PLAN_VER}, ensure_ascii=False), encoding="utf-8")
            job["carried"] = {"moved": c["moved"]}

        def run():
            carry_over()
            try:
                r = idf.identify_cached(model, body.work, body.what, body.project_dir, body.fingerprint, provider,
                                        reuse=body.reuse and not known, chip=body.chip or "", known=known,
                                        reuse_flow=body.reuse or known is not None,  # 重试：理清的流程已存下，只重跑选计时点
                                        flow=flow, on_progress=progress)
            except Exception as e:  # 码表自己出错也要说清楚，不能一直转圈
                r = {"ok": False, "kind": "other", "message": f"找关键步骤时码表出错了：{e}", "found": job["found"]}
            r.pop("reply", None)
            r.pop("chain_reply", None)
            ident["flow"] = r.pop("flow", None) if not r.get("ok") else None  # 第二次失败：重试时带上理清的流程
            job.update({k: v for k, v in r.items() if k in ("page", "found", "kind", "message", "reused", "summary", "doubts", "stage")})
            if r.get("error"):
                job["error"] = r["error"]["detail"]  # 页面"详情"里给原话（已去掉密钥）
                job["error_info"] = r["error"]
            job["seconds"] = r.get("seconds", round(time.time() - job["started"], 1))
            job["state"] = "done" if r.get("ok") else "failed"
            if not r.get("reused") and r.get("calls", 1):
                T().count("llm_calls", r.get("calls", 1))  # 一次识别最多调用两次：理清流程、选计时点
            if r.get("ok"):
                T().end("identify", reused=bool(r.get("reused")))
                T().begin("check")

        def crashed(e):
            if job["state"] == "running":
                job.update(state="failed", kind="other", message="找关键步骤时码表出错了，点\"重试\"再找一次。", error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return job

    @app.post("/api/identify/start")
    def identify_start(body: IdentifyBody):
        if not Path(body.work).is_dir():
            raise HTTPException(400, "找不到复制好的源码，请回首页重新选一次固件源码。")
        if not body.what.strip():
            raise HTTPException(400, "先写要测从什么到什么。")
        c = _clean_work(body.work, body.project_dir)  # 上次没还原：大模型要读没插计时点的副本，行号才和原代码对得上
        if not c["ok"]:
            raise HTTPException(409, c["message"])
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
        return _identify(ident["args"], known=job.get("found"), flow=ident.get("flow"))  # 已找到的告诉大模型，接着找；理清的流程不再重理

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
        c = _clean_work(body.work)  # 插过计时点又回来加步骤：读没插计时点的副本，行号才对得上
        if not c["ok"]:
            raise HTTPException(409, c["message"])
        return idf.add_step(model, body.work, body.sentence.strip(), what=body.what or "")

    @app.get("/api/env")
    def env_status(cc: bool = True):
        """电脑上的环境：装了哪些乐鑫编译工具、本机 Claude Code 登没登录、插着哪些设备。都不用大模型、不花钱。"""
        return {"idf": idf_finder(), "claude_code": cc_status() if cc else None, "ports": port_finder()}

    # ---- 设备上是哪种芯片：选板子时核对，芯片对不上的板子不让选（10-21 用户定）----
    chip_lock = threading.Lock()

    def _chips_file():
        return live["data"] / "devices.json"

    def _chips():
        try:
            return json.loads(_chips_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _key(port):
        return getattr(device, "port_key", lambda p: p)(port)

    def _remember_chip(port, chip):
        chip = chip_name(chip)
        if not (port and chip):
            return
        d = _chips()
        d[_key(port)] = chip
        try:
            _chips_file().parent.mkdir(parents=True, exist_ok=True)
            _chips_file().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    @app.get("/api/device/chip")
    def device_chip():
        """插着的设备记下过是哪种芯片没有（读过、或者烧录成功过）。不碰设备。"""
        ports = port_finder()
        if not ports:
            return {"port": None, "chip": None}
        return {"port": ports[0], "chip": _chips().get(_key(ports[0]))}

    @app.post("/api/device/chip")
    def device_chip_read():
        """读一下插着的设备是哪种芯片（设备会重启一下），记下来。码表正在用调试口（试一次、多轮实测、烧录）时不读。"""
        ports = port_finder()
        if not ports:
            return {"port": None, "chip": None, "error": "没插设备"}
        port = ports[0]
        known = _chips().get(_key(port))
        if known:
            return {"port": port, "chip": known}
        job = fsw.get("job") or {}
        if EspIdf._OPEN or (job.get("status") in ("running", "waiting_device") and job.get("phase") in ("flash", "try")):
            return {"port": port, "chip": None, "busy": True}
        if not chip_lock.acquire(blocking=False):
            return {"port": port, "chip": None, "busy": True}
        try:
            r = getattr(device, "read_chip", lambda p: {"chip": None, "error": "这种设备读不了芯片"})(port)
        finally:
            chip_lock.release()
        if r.get("chip"):
            _remember_chip(port, r["chip"])
        return {"port": port, "chip": chip_name(r.get("chip")), "error": r.get("error")}

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
        if (r.get("status") == "changed" or r.get("copied")) and bg["job"] and bg["job"].get("work") == r.get("work"):
            bg["job"] = None  # 源码改过（或副本重新复制过）：上次在后台编的原版不算数，要按改过的源码再编（决策 134）
        if r.get("status") == "changed" and fsw["args"] and fsw["args"].work == r.get("work"):
            fsw["plan"] = None  # 上次的插入方案是按旧源码定的行号
        if r.get("ok") and r.get("work"):  # 最近选的源码：网页没存过页面进度（流程不是从这个网页走的）时，按它和各任务接上（见 /api/ui）
            last_src.update(r=r, body=body.model_dump(include={"path", "upload"}, exclude_none=True), at=time.time())
        if r.get("project_dir"):
            if not (T().path and T().path.parent == Path(r["project_dir"]) and "identify" not in T().data["stages"]):
                tline["cur"] = tln.Timeline(Path(r["project_dir"]) / "timeline.json")  # 新的一次测量
                T().begin("source")
            T().count("clicks")
        return r

    def server_flow():
        """后台自己知道的流程进度（不靠网页存的）：最近选的源码，和给它做的找关键步骤、插点烧录、多轮实测走到哪一步。
        10-25：流程是按接口走完的（没有网页存过进度），新开网页只看自己存的那份，停在首页、像回到了老界面，只好从头再走一遍。"""
        r = last_src.get("r")
        if not r:
            return None
        work, proj = r.get("work"), r.get("project_dir") or ""
        ia, fa = ident["args"], fsw["args"]
        ij, fj, cj = ident["job"] or {}, fsw["job"] or {}, col["job"] or {}
        what = getattr(ia, "what", "") if getattr(ia, "work", None) == work else ""
        stage = 0
        if what and ij.get("state") in ("running", "done"):
            stage = 1
        if stage and getattr(fa, "work", None) == work and fj.get("status") not in (None, "idle"):
            stage = 2
        if stage == 2 and cj.get("status") not in (None, "idle") and not cj.get("demo") and proj and str(cj.get("raw_log") or "").startswith(proj):
            stage = 3
        body = last_src.get("body") or {}
        info = r.get("info") or {}
        label = body.get("path") or ""
        return {"work": work, "stage": stage, "page": ["home", "steps", "flash", "collect"][stage], "what": what,
                "fsw": fj.get("status") if stage >= 2 else None, "at": last_src.get("at"),
                "REAL": {**info, "body": body, "label": label, "work": work, "project_dir": r.get("project_dir"),
                         "fingerprint": r.get("fingerprint"), "ai": []},
                "hSrc": label, "pName": (info.get("name") or "") + (f"（{info['board']}）" if info.get("board") else ""),
                "hBoardQ": info.get("board") or "", "bg": (bg["job"] or {}).get("work") == work}

    # ---- 页面进度：刷新、新开标签页时按它接着做（D 组） ----
    # 网页把"选了哪份源码、要测什么、走到哪一步、核对时改过什么"这些只在网页里的东西存到这里（只在内存，码表重开就没了）；
    # 后台任务的进度不在这里，网页打开时自己去问各任务。演示数据和真流程分开存，互不覆盖。
    ui = {}

    @app.post("/api/ui")
    async def ui_save(request: Request, key: str = ""):
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "页面进度太大，没存")
        try:
            data = json.loads(raw or b"null")
        except ValueError:
            raise HTTPException(400, "页面进度不是 JSON")
        if not isinstance(data, dict):
            raise HTTPException(400, "页面进度不是 JSON 对象")
        ui[key[:40]] = data
        return {"ok": True}

    @app.get("/api/ui")
    def ui_get(key: str = ""):
        """页面进度，加上各后台任务是给哪份复制好的源码做的：网页只接上同一份源码的任务。"""
        work = lambda a: getattr(a, "work", None)
        return {"ui": ui.get(key[:40]), "jobs": {"identify": work(ident["args"]), "fsw": work(fsw["args"]),
                                                 "bgbuild": (bg["job"] or {}).get("work")},
                "server": server_flow() if not key else None}  # 真流程才有（演示数据的页面进度用 key 分开存）

    @app.middleware("http")
    async def no_stale_page(request, call_next):
        """网页文件每次都让浏览器先问一下有没有新的，升级码表后不会还用旧代码。"""
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
