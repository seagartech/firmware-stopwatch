"""本地网页后台：给网页送页面、假数据、步骤条状态和设备输出。

这一版只用演示数据；真读源码、真编译、真串口后面逐个接进来。
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fsw.config import load_config
from fsw.scenario import to_page
from fsw.workspace.picker import native_pick
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


class PickBody(BaseModel):
    kind: str = "dir"


TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class SyncBody(BaseModel):
    reached: int
    viewing: str | None = None
    error: str | None = None
    skipped: list[str] = []


def create_app(data_dir=None, picker=None):
    app = FastAPI(title="固件码表")
    demos = load_demos()
    data_dir = Path(data_dir) if data_dir else load_config().data_dir
    uploads = ascii_work_root(data_dir) / "_uploads"
    picker = picker or native_pick
    state = {"flow": None, "demo": None, "settings": None}

    def settings():
        if state["settings"] is None:
            cfg = load_config()
            state["settings"] = {
                "llm_provider": cfg.llm_provider,
                "has_key": {
                    "claude-api": bool(cfg.claude_api_key),
                    "deepseek": bool(cfg.deepseek_api_key),
                },
                "idf_path": cfg.idf_path or "",
                "serial_port": cfg.serial_port or "",
                "data_dir": str(cfg.data_dir),
            }
        return state["settings"]

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
        return FileResponse(STATIC / "index.html")

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
    def save_settings(body: dict):
        s = settings()
        for k in ("llm_provider", "idf_path", "serial_port", "data_dir"):
            if k in body:
                s[k] = body[k] or None if k == "llm_provider" else body[k]
        if body.get("key_entered") and s["llm_provider"] in s["has_key"]:
            s["has_key"][s["llm_provider"]] = True
        return snapshot()

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
        unpack = ascii_work_root(data_dir) / "_unpack" / hashlib.sha1(str(path).encode()).hexdigest()[:10]
        return prepare(path, data_dir=data_dir, board=body.board, unpack_to=unpack)

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

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
