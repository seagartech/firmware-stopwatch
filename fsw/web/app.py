"""本地网页后台：给网页送页面、假数据、步骤条状态和设备输出。

这一版只用演示数据；真读源码、真编译、真串口后面逐个接进来。
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fsw.config import load_config
from fsw.web.flow import Flow, FlowError

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DEMO = HERE / "demo"


def load_demos():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(DEMO.glob("*.json"))}


class StartBody(BaseModel):
    kind: str
    demo: str


class StepBody(BaseModel):
    step: str
    message: str | None = None
    skip: list[str] = []


def device_lines(demo, rounds, start=0):
    """按演示数据生成设备调试口会打出来的计时点行。"""
    points = []
    for line in demo["lines"]:
        t, chain = 0.0, []
        for key in line["segments"]:
            seg = demo["segments"][key]
            a, b = [p.strip() for p in seg["points"].split("→")]
            chain.append((a, b, key))
        points.append(chain)
    for r in range(start, start + rounds):
        base = 5_000_000 + r * 7_000_000
        yield "FSW,RESET"
        marks = {}
        for chain in points:
            t = 0.0
            marks.setdefault(chain[0][0], 0.0)
            for a, b, key in chain:
                t += demo["segments"][key]["values"][r % 20]
                marks.setdefault(b, t)
        for p, t in sorted(marks.items(), key=lambda x: x[1]):
            yield f"FSW,{p},{base + round(t * 1_000_000)},0"


def create_app():
    app = FastAPI(title="固件码表")
    demos = load_demos()
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

    @app.get("/api/device/stream")
    async def device_stream(demo: str, rounds: int = 1, start: int = 0, interval: float = 0.05):
        if demo not in demos:
            raise HTTPException(404, "没有这套演示数据")

        async def gen():
            for line in device_lines(demos[demo], rounds, start):
                yield f"data: {line}\n\n"
                if interval:
                    await asyncio.sleep(interval)
            yield "data: END\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
