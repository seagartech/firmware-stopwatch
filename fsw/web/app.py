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
from fsw.platforms.espidf import EspIdf, ToolEnvError, find_idf, find_ports, pick_idf
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
CARRY_SECS = 30  # 试通后这么久内开始多轮实测，才接着算试通时正在进行的那一轮
ROUND_SETTLE = 1.5  # 多轮实测：一轮收全后，下一轮一直没开始，再等这么久就收尾（最后一轮、或设备不再开始下一轮）
MISS_RUN = 3       # 多轮实测：同一个计时点连着这么多轮没收到，就在提示里点名


class RestoreBody(BaseModel):
    project_dir: str | None = None   # 码表重开过：按项目文件夹里存的插入记录还原


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
            c = ws.ensure_clean(body.project_dir)  # 上次插过的先还原；插入记录和副本对不上就重新复制，不拿旧备份盖新副本
            if not c["ok"]:
                return _fail(job, "insert", c["message"])
            plan_text = {}

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
                    job["log"] = "用你核对步骤时 AI 提前准备好的插法"
                else:
                    job.update(prepared=False, log="提前准备的那次没成，重新生成")
                    try:
                        job["calls"] += 1
                        text = _model()[1].investigate(ins.build_prompt(steps, ins.default_header(body.work), needs), body.work,
                                                       on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
                    except llms.LLMError as e:
                        return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。", error=e.detail)
            else:
                job["calls"] += 1
                try:
                    text = Tap().investigate(ins.build_prompt(steps, ins.default_header(body.work), needs), body.work,
                                             on_tool=lambda d: job.update(log=f"AI 在读代码：{d}"))
                except llms.LLMError as e:
                    return _fail(job, "insert", f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。", error=e.detail)
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
            job.update(status="inserted", view=ins.diff_view(rec, steps), notes=plan["notes"], log="插好了，先看改动",
                       total=len(rec["steps"]))
            T().end("insert", reused=bool(cached), prepared=bool(job.get("prepared")))
            T().count("llm_calls", job.get("calls", 0))
            T().begin("confirm")

        def crashed(e):
            if job["status"] == "running":
                _fail(job, "insert", "插计时点时码表出错了，点\"重新插入\"再试一次。", error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
        return job

    def _run_from(start):
        job, a = fsw["job"], fsw["args"]
        _, orig_fw, fsw_fw = _fsw_dirs(a)
        job.update(status="running", err=None, cancel=False, gen=job.get("gen", 0) + 1)  # 第几趟：上一趟还没停的"试一次"见到就停，让出调试口

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
                step = ins.locate_error(r.get("error"), rec, a.work)
                s = next((x for x in a.steps if ins.point_id(x, a.steps) == step), None)
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
            if r.get("cause") == "tool":
                return _fail(job, "burn", r["message"], error=r.get("error"), cause="tool")
            if not r.get("ok"):
                return _fail(job, "burn", "烧录中断", error=r.get("error"))
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
                       try_started=time.time(), dev_seen=None if fresh else time.time(), boot_quiet=False)
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
            rounds = {"cur": set(), "best": set(), "seen": set()}  # 这一轮收到的、收得最多的那一轮、收到过的
            full = lambda got: got >= want - start_ids
            shown = lambda got: got | (rounds["seen"] & start_ids)
            job["missing"] = []

            def feed(line):
                p = meas.parse_line(line)
                if line.strip().startswith("FSW,RESET"):
                    rounds["prev"] = (rounds["cur"], p[1] if p else None)
                    rounds["cur"] = set()
                    rounds["tail"] = []
                    return
                if p or meas.LATE.search(line):
                    rounds.setdefault("tail", []).append(line)  # 这一轮到现在的计时点：试通时交给多轮实测，这一轮接着算（不用再多说一句）
                got = ins.count_marks([line]) & want
                rounds["seen"] |= got
                pv = rounds.get("prev")
                # 设备回到聆听（结束标记）后 1 秒内、下一轮还没开始时才放完的最后一声：补上就收齐的，算上一轮的（和多轮实测同一个做法）
                if got and not rounds["cur"] and pv and pv[0] and pv[1] is not None and p and 0 <= p[1] - pv[1] <= meas.LATE_US and full(pv[0] | got) and not full(pv[0]):
                    pv[0].update(got)
                    rounds["best"] = set(pv[0])
                    return
                rounds["cur"] |= got
                if (full(rounds["cur"]), len(rounds["cur"])) >= (full(rounds["best"]), len(rounds["best"])):
                    rounds["best"] = set(rounds["cur"])

            def on_line(line):
                if superseded():
                    return
                if not job.get("dev_seen") and _plain(line):
                    job["dev_seen"] = time.time()  # 设备开机、有输出了：页面上从"设备正在重启"换成"现在做一次要测的事"
                feed(line)
                # 页面显示收得最多的那一轮：唤醒那一轮（没有起点）结束后计数不清回 0，免得像又从头来了一遍
                cur = shown(rounds["cur"] if (full(rounds["cur"]), len(rounds["cur"])) >= (full(rounds["best"]), len(rounds["best"])) else rounds["best"])
                job["got"] = sorted(cur)
                job["missing"] = [names.get(f"P{k}") or f"P{k}" for k in sorted(want - cur)] if cur else []
                _log(job, line[:300])

            def enough():  # 有一轮除起点外全部收到才停；差几个就接着听，页面上告诉人这一轮还差哪几步
                return full(rounds["best"])
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
                    return superseded() or enough() or reopen["hit"]
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
            if not got:
                job["try_fails"] = job.get("try_fails", 0) + 1
                odd = sum(1 for l in lines if _garbled(l))
                why = ("收到的都是乱码，多半是波特率不对（码表按 115200 读）。" if lines and odd >= len(lines) / 2 else
                       "常见原因：没触发到；串口被别的软件占着（比如串口监视器）；波特率不对（码表按 115200 读）。")
                return _fail(job, "try", f"{TRY_SECONDS // 60} 分钟里没收到计时点。点「再试一次」，然后做一次要测的事。", why=why,
                             error=f"（{TRY_SECONDS} 秒内没有计时点输出）", offer_restore=job["try_fails"] >= 2)
            job.update(status="done", log=f"试通了：收到 {len(got)} / {len(want)} 个计时点")
            fsw["carry"] = {"lines": list(rounds.get("tail") or []), "at": time.time()}  # 试通那一刻正在进行的这一轮
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
        if not page["segs"]:
            raise HTTPException(409, "这次插进去的计时点连不成一段，量不出用时：回到「找关键步骤」那一步，至少留下起点和终点再插一次。")
        return page

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
        page = _inserted_page(body) if body.page else None
        if page:  # 真找完的步骤：按代码位置对上码表自带的合理范围标准
            standard = stds.find(page.get("steps") or [])
            if body.project_dir:  # 插计时点时顺带记了附加值的计时点（插入记录里），看附加值的问题规则才用
                values = (ins.current(Path(body.project_dir) / "fsw-backup") or {}).get("values") or None
            scenario = stds.real_scenario(page, standard, values)
            if body.project_dir:  # 项目有合理范围清单：测的时候也按它画范围
                try:
                    lst = rl.read(body.project_dir)
                except rl.ListFormatError:
                    lst = None
                if lst:
                    scenario = rl.apply(scenario, lst)
            std_hash = stds.digest(standard)
        elif body.demo in demos:
            scenario = demos[body.demo]["scenario"]
        else:
            raise HTTPException(400, "不知道要测什么：先找关键步骤")
        spec = meas.Spec.from_scenario(scenario)
        pending = []
        if body.resume and old:
            job, coll = old, old["_coll"]
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

        def take(results):
            """收尾的轮记下来；进度、提示跟着更新。"""
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
                elif not r.get("stale"):  # 没收全：同一个点连着几轮都没收到，点名（多半是这一步没走到，或者插的位置走不到）
                    run = {m: job.get("miss_run", {}).get(m, 0) + 1 for m in r.get("missing") or []}
                    job["miss_run"] = run
                    worst = max(run.items(), key=lambda kv: kv[1], default=None)
                    if worst and worst[1] >= MISS_RUN:
                        job["half_hint"] = (f"「{worst[0]}」连着 {worst[1]} 轮都没收到：多半是这一步没走到，或者计时点插的位置走不到，"
                                            "再测也一样，先停下，回到「插计时点」那一步换个位置，或者删掉它。")
                elif r.get("stale"):
                    run = job.setdefault("stale_run", {})
                    for m in r["missing"]:
                        run[m] = run.get(m, 0) + 1
                    worst = max(run.items(), key=lambda kv: kv[1])
                    if worst[1] >= 2:
                        name = worst[0].split("（")[0]
                        job["half_hint"] = (f"「{name}」连着 {worst[1]} 轮都记在起点之前，不像是这一轮的事，再测也一样。"
                                            "多半是这个计时点插的位置不对：先停下，回到「插计时点」那一步换个位置，或者删掉它。")
                job["rounds"].append(r)
            first = next((x for x in job["rounds"] if x["ok"]), None)
            if first and not job.get("_checked") and not (coll.prev and coll.prev.get("sent") is not None and coll.prev["sent"].get("n") == first["n"]):
                # 第 1 轮收齐（晚到的最后一声也等完了）：自动校验计时点位置，不对就停下，不让后面白测（决策 158）
                job["_checked"] = True
                bad = meas.suspects(first, job["_scenario"])
                if bad:
                    job["suspect"] = bad
                    job["_stop"] = True
            job["progress"], job["last"] = list(coll.progress()), coll.last
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
                    job["status"] = "suspect" if job.get("suspect") else "done" if ok_count() >= job["target"] else "stopped"
                    if body.source == "device":
                        T().end("collect")

        def crashed(e):  # 上面已经停下（已测的留着）：说清楚为什么停了
            if job is col["job"]:
                job.update(message=f"实测时码表出错了，已测的 {ok_count()} 轮都留着：点「接着测」继续。", error=_detail(e))
        threading.Thread(target=_guarded(run, crashed), daemon=True).start()
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

    @app.get("/api/report")
    def report(demo: str | None = None):
        """报告用的判断：这次收齐的轮，按固定规则算（判快慢、两把尺子、结论标签），不用大模型。
        demo=…：这套演示数据测过就用测的，没测过就用演示数据本身的数。"""
        job = col["job"]
        ok = [r for r in (job or {}).get("rounds", []) if r["ok"]]
        same = job and (demo is None or job.get("demo") == demo)
        if ok and same:
            sc, raw = job["_scenario"], job["raw_log"]
            if job.get("_page") is not None and job.get("_project_dir"):  # 真测：每次都按项目现在的合理范围清单判
                sc = job["_scenario"] = _judge_scenario(job, ok)
                if job.get("status") in ("done", "stopped") and "_prev" not in job:
                    job["_prev"] = _prev_run(job, sc, ok)
        elif demo in demos:
            sc, ok, raw = demos[demo]["scenario"], _demo_rounds(demos[demo]), None
        else:
            raise HTTPException(404, "还没有测完的轮")
        out = jdg.judge(sc, ok)
        keep = ("name", "start", "segments", "lines", "experiences", "problems", "standard", "matched", "point_names", "points", "range_list",
                "first_run", "exp_skip")
        allr = (job or {}).get("rounds", []) if ok and same else []
        quality = {"ok": len(ok), "stale": len([r for r in allr if r.get("stale")]),
                   "missing": len([r for r in allr if not r.get("ok") and not r.get("stale")]), "half": (job or {}).get("half", 0) if raw else 0,
                   "nostart": (job or {}).get("nostart", 0) if raw else 0,
                   "startName": (sc.get("start") or {}).get("name", "")}
        return {**out, "rounds": ok, "raw_log": raw, "scenario": {**{k: sc.get(k) for k in keep}, "experience": main_experience(sc)},
                "timing": _timing() if raw else None, "quality": quality, "start_lag": _start_lag(raw, sc),
                "list_filled": (job or {}).get("_filled") if ok and same else None,
                "prev": (job or {}).get("_prev") if ok and same else None,
                "measured_at": (job or {}).get("measured_at") if ok and same else None}

    def _prev_run(job, sc, ok):
        """改完再测（任务 22）：和上次比。上次 = 同一个项目、计时点一样、收齐 10 轮以上的最近一次。
        返回 {"run", "measured_at", "n", "segs": {段: 中位数}, "lines": {线: 中位数}}；比不了返回 {"why": 一句话}。"""
        if len(ok) < rl.MIN_ROUNDS:
            return {"why": f"这次只测了 {len(ok)} 轮，不到 {rl.MIN_ROUNDS} 轮，不和上次比"}
        proj, cur = Path(job["_project_dir"]), Path(job["raw_log"]).parent.name
        pts = {(g.get("from"), g.get("to")): k for k, g in sc["segments"].items()}
        names = [l["name"] for l in sc["lines"]]
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
                rounds = [r for r in meas.collect(log.read_text(encoding="utf-8", errors="replace").splitlines(),
                                                  meas.Spec.from_scenario(psc)) if r["ok"]]
            except Exception:  # 老数据格式对不上：这一次不拿来比
                continue
            if len(rounds) < rl.MIN_ROUNDS:
                continue
            # 码表新加了线、段（比如表情线、到第一声那条）时，两次都有的段和线照样比；一段都对不上才不比
            if not {(g.get("from"), g.get("to")) for g in psc["segments"].values()} & set(pts):
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
            for k, g in psc["segments"].items():
                if (g.get("from"), g.get("to")) not in pts:
                    continue
                v, ck = seg_vals(rounds, k), pts[(g.get("from"), g.get("to"))]
                if v:
                    segs[ck] = round(jdg.med(v), 6)
                    c = seg_vals(ok, ck)
                    noise[ck] = round(max(iqr(v), iqr(c) if c else 0), 6)  # 两次各自的正常波动，取大的
            lines, noise_l = {}, {}
            for i, l in enumerate(psc["lines"]):
                if l["name"] not in names:
                    continue
                v = line_vals(rounds, i)
                if v:
                    lines[l["name"]] = round(jdg.med(v), 6)
                    c = line_vals(ok, names.index(l["name"]))
                    noise_l[l["name"]] = round(max(iqr(v), iqr(c) if c else 0), 6)
            return {"run": run.name, "measured_at": meta.get("measured_at") or run.name, "n": len(rounds), "segs": segs, "lines": lines,
                    "noise": noise, "noise_lines": noise_l}
        return {"why": f"计时点和上次（{other}）不一样，不和上次比"} if other else None

    def _judge_scenario(job, ok):
        """这份报告按什么判：项目有合理范围清单就按清单；测满 10 轮、清单还没有或有空着的，按这次实测自动填上（只填空的）。"""
        page, d = job["_page"], Path(job["_project_dir"])
        sc = stds.real_scenario(page, stds.find(page.get("steps") or []), job.get("_values"))
        try:
            lst = rl.read(d)
        except rl.ListFormatError as e:
            return {**sc, "range_list": {"file": rl.FILENAME, "broken": str(e), "text": "合理范围清单格式不对，这次按码表自带的范围判"}}
        if job.get("status") in ("done", "stopped"):
            try:
                name = json.loads((d / "project.json").read_text(encoding="utf-8")).get("name")
            except (OSError, ValueError):
                name = None
            fresh = not lst or not any(x.get("range") for x in list(lst["segments"].values()) + lst["lines"])
            lst, filled = rl.fill(lst, sc, ok, project=name or d.name)
            if filled:
                rl.save(d, lst)
                job["_filled"] = filled
                job["_fresh"] = fresh  # 填之前清单里一个数都没有：这次就是第一次测
                job["_filled_vals"] = {x["name"]: (x.get("range"), x.get("slow"))
                                       for x in list(lst["segments"].values()) + lst["lines"] if x["name"] in filled}
        out = rl.apply(sc, lst) if lst else sc
        auto = job.get("_filled_vals") or {}
        if auto:  # 合理范围就是拿这次的轮填的：不拿它再判这次（只会全"正常"），这次快慢只按体验标准看，下次再测和它比；手动改过的照改过的判
            for x in list(out["segments"].values()) + out["lines"]:
                if x.get("name") in auto and (x.get("range"), x.get("slow")) == auto[x["name"]]:
                    x.update(range=None, slow=None, new=True)
            # 填之前清单里没有任何数才算第一次测（按条数比会错：同名的段和线、没测到的段都会让条数对不上，真测第 29 次）；只补了新出现的几项照常判其余的
            out["first_run"] = bool(job.get("_fresh"))
        return stds.with_default_experience(out)

    def _list_dir(project_dir=None):
        """清单在哪个项目：给了项目文件夹就用它（只限码表结果文件夹里，设置页进来的）；没给就是这份报告对应的项目。"""
        if project_dir:
            d = Path(project_dir).expanduser().resolve()
            if Path(live["data"]).resolve() not in d.parents:
                raise HTTPException(403, "只能打开码表结果文件夹里的项目")
            return d
        job = col["job"]
        if not job or not job.get("_project_dir") or job.get("_page") is None:
            raise HTTPException(409, "先测完一次，才有这个项目的合理范围清单")
        return Path(job["_project_dir"])

    @app.get("/api/rangelists")
    def rangelists():
        """设置页"合理范围清单"：每个测过的项目一行，有没有清单、几项、上次改的时间。"""
        out = []
        for pj in sorted(Path(live["data"]).glob("*/project.json")):
            d = pj.parent
            if not any(d.glob("runs/*/raw.log")):
                continue
            try:
                info = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            row = {"project_dir": str(d), "name": info.get("name", d.name) + (f"（{info['board']}）" if info.get("board") else ""),
                   "has": False, "items": 0, "changed": None, "broken": None}
            p = rl.path_of(d)
            if p.exists():
                row.update(has=True, changed=time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime)))
                try:
                    lst = rl.read(d)
                    row["items"] = len(lst["segments"]) + len(lst["lines"])
                except rl.ListFormatError as e:
                    row["broken"] = str(e)
            out.append(row)
        return out

    def _list_out(d):
        try:
            lst = rl.read(d)
        except rl.ListFormatError as e:
            return {"path": str(rl.path_of(d)), "broken": str(e), "errors": [{"line": n, "msg": m} for n, m in e.errors]}
        if lst is None:
            raise HTTPException(404, f"这个项目还没有合理范围清单：测满 {rl.MIN_ROUNDS} 轮后码表按实测自动建")
        return {"path": str(rl.path_of(d)), "list": lst}

    @app.get("/api/rangelist")
    def rangelist_get(project_dir: str | None = None):
        """合理范围清单页：给的项目（设置页进来）或这份报告对应项目的清单。"""
        return _list_out(_list_dir(project_dir))

    class ListEditBody(BaseModel):
        kind: str                       # segment / line
        key: str                        # 分段编号 / 测量线名字
        range: list[float | None] | None = None
        slow: float | None = None
        note: str = ""
        fair: float | None = None       # 体验标准：超过多少秒算差，不设就空着
        source: str = ""
        project_dir: str | None = None  # 设置页进来时是哪个项目

    @app.post("/api/rangelist/edit")
    def rangelist_edit(body: ListEditBody):
        """改一段或一条线的数（清单页的「修改」）；改完报告自动按新的判。"""
        d = _list_dir(body.project_dir)
        try:
            lst = rl.read(d)
        except rl.ListFormatError as e:
            raise HTTPException(409, str(e))
        if lst is None:
            raise HTTPException(404, "这个项目还没有合理范围清单")
        rng = None if not body.range or body.range[0] is None else [body.range[0], body.range[1] if len(body.range) > 1 else None]
        try:
            if body.kind == "segment":
                rl.edit_segment(lst, body.key, rng, body.slow, body.note)
            elif body.kind == "line":
                rl.edit_line(lst, body.key, rng, body.slow, body.fair, body.source)
            else:
                raise HTTPException(400, f"不认识的修改：{body.kind}")
        except rl.ListRuleError as e:
            raise HTTPException(400, str(e))
        rl.save(d, lst)
        return _list_out(d)

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
        lines = (run / "raw.log").read_text(encoding="utf-8", errors="replace").splitlines()
        # 有"第一下感觉到的结果"那条线时，最后一条（到全部做完）看内容长短、不判快慢：首页的主数用第一下那条（真测第 40 次：显示成"声音放完 10.11 秒"）
        ls = sc.get("lines") or []
        i = next((k for k, l in enumerate(ls) if l.get("first")), len(ls) - 1)
        ok = [r for r in meas.collect(lines, meas.Spec.from_scenario(sc)) if r["ok"] and r["ends"][i] is not None]
        end = ls[i].get("end") if ls and i < len(ls) - 1 else None
        return (round(jdg.med([r["ends"][i] for r in ok]), 3), len(ok), end) if ok else (None, 0, end)

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
        """多轮实测每轮做什么：按你写的"测什么"挑模板（对话类用固定的 20 句；别的按起点的说法，比如"按一下按键"）。"""
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
        sc = job0["_scenario"]
        j = jdg.judge(sc, ok)
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

            def burn_crashed(e):
                out.update(waiting=False, say="源码已还原，原版没烧上：烧录时码表出错了，再点一次「还原成原版」。", detail=_plain(_detail(e)))
                job.update(status="restored", restore=dict(out))
            threading.Thread(target=_guarded(wait_and_burn, burn_crashed), daemon=True).start()
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

    def _identify(body, known=None):
        provider, model = _model()
        job = {"state": "running", "what": body.what, "provider": provider, "found": list(known or []), "read": 0,
               "last": "", "seconds": 0, "started": time.time()}
        with ident["lock"]:
            ident["job"], ident["args"] = job, body

        def progress(p):
            job.update(found=p["found"], at=p["at"], read=p["read"], last=p["last"], seconds=p["seconds"])

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
        c = _clean_work(body.work)  # 插过计时点又回来加步骤：读没插计时点的副本，行号才对得上
        if not c["ok"]:
            raise HTTPException(409, c["message"])
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
        if (r.get("status") == "changed" or r.get("copied")) and bg["job"] and bg["job"].get("work") == r.get("work"):
            bg["job"] = None  # 源码改过（或副本重新复制过）：上次在后台编的原版不算数，要按改过的源码再编（决策 134）
        if r.get("status") == "changed" and fsw["args"] and fsw["args"].work == r.get("work"):
            fsw["plan"] = None  # 上次的插入方案是按旧源码定的行号
        if r.get("project_dir"):
            if not (T().path and T().path.parent == Path(r["project_dir"]) and "identify" not in T().data["stages"]):
                tline["cur"] = tln.Timeline(Path(r["project_dir"]) / "timeline.json")  # 新的一次测量
                T().begin("source")
            T().count("clicks")
        return r

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
                                                 "bgbuild": (bg["job"] or {}).get("work")}}

    @app.middleware("http")
    async def no_stale_page(request, call_next):
        """网页文件每次都让浏览器先问一下有没有新的，升级码表后不会还用旧代码。"""
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
