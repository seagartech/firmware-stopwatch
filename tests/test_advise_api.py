"""任务 17 的网页后台：测完后让 AI 写"为什么、怎么改"（只读代码），失败能重试；没测完不让写。假模型、假设备。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_standards import PAGE

GOOD = {"fixes": [{"when": ["main"], "seg": "P2-P5", "why": "大模型想回答 1.35 秒，占六成。", "impact": "等 2 秒多才开口。",
                   "opts": [{"name": "换快一点的模型", "how": "固件不改，服务器换模型", "size": "固件 0 行", "rk": "回答质量可能变",
                             "effect": "约 1.7 秒（估算）", "cost": "可能没那么聪明", "work": "小", "wn": "改服务器配置", "who": "服务器方", "rec": 1},
                            {"name": "想好一句就说", "how": "服务器边想边说", "size": "固件 0 行", "rk": "服务器改动较大",
                             "effect": "约 1.8 秒（估算）", "cost": "服务器要改", "work": "中", "wn": "改出话方式", "who": "服务器方"}],
                   "rec": "先换模型。", "dev": {"data": "P2→P5 中位数 1.35 秒", "cause": "服务器在想", "change": "不改固件",
                                               "size": "0 行", "rk": "回答质量", "verify": "同样 20 句再测"}}],
        "fine": "设备自己的几段都正常。"}


def real_log(rounds=20):
    log = []
    for r in range(rounds):
        b = 10_000_000 * (r + 1)
        t = {"P1": 0, "P2": 0.75, "P3": 1.15, "P4": 1.162, "P5": 2.1, "P6": 2.11, "P7": 2.16, "P8": 2.17, "P9": 2.175}
        log += [f"FSW,RESET,{b},0"] + [f"FSW,{p},{b + 100_000 + int(v * 1e6)},0" for p, v in t.items()]
    return log


@pytest.fixture
def run(tmp_path):
    def _run(replies):
        it = iter(replies)
        made = []

        def factory(p, k):
            m = FakeLLM(next(it))
            made.append(m)
            return m
        c = TestClient(create_app(data_dir=tmp_path / "d", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                  cc_status=lambda: {"found": True, "logged_in": True},
                                  port_finder=lambda: ["/dev/cu.usbmodem1101"], device=FakeSerialDevice(real_log()),
                                  llm_factory=factory, wait_step=0.01))
        c.post("/api/settings", json={"llm_provider": "cc"})
        work = tmp_path / "work"
        work.mkdir()
        c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(tmp_path / "p")})
        wait(c, lambda j: j["status"] == "done")
        return c, made, work
    return _run


def advice_wait(c, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/advise").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def reply(d):
    return "```json\n" + json.dumps(d, ensure_ascii=False) + "\n```"


def test_ai_writes_cards_for_this_run(run, tmp_path):
    c, made, work = run([reply(GOOD)])
    c.post("/api/advise/start", json={"work": str(work), "steps": PAGE["steps"], "what": "从说完话到开口回答",
                                      "project_dir": str(tmp_path / "p")})
    j = advice_wait(c)
    assert j["status"] == "done" and j["fixes"][0]["when"] == ["main"] and j["fine"]
    assert "大模型想回答" in made[0].prompts[0] or "服务器" in made[0].prompts[0]
    assert (tmp_path / "p" / "advice" / "reply.txt").exists()


def test_bad_reply_then_retry(run, tmp_path):
    c, made, work = run(["没给 json", reply(GOOD)])
    body = {"work": str(work), "steps": PAGE["steps"], "project_dir": str(tmp_path / "p")}
    c.post("/api/advise/start", json=body)
    j = advice_wait(c)
    assert j["status"] == "failed" and "格式不对" in j["message"]
    c.post("/api/advise/start", json=body)
    assert advice_wait(c)["status"] == "done"


def test_no_run_no_advice(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path / "d", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: []))
    assert c.post("/api/advise/start", json={"work": str(tmp_path)}).status_code == 409


def test_reads_the_original_source_not_the_copy_with_timing_points(run, tmp_path):
    """插过计时点的那份行号会变；写建议时读你原来的源码（只读）。"""
    c, made, work = run([reply(GOOD)])
    orig = tmp_path / "orig"
    orig.mkdir()
    (tmp_path / "p").mkdir(exist_ok=True)
    (tmp_path / "p" / "project.json").write_text(json.dumps({"source": str(orig)}), encoding="utf-8")
    seen = []
    made_before = len(made)
    c.post("/api/advise/start", json={"work": str(work), "steps": PAGE["steps"], "project_dir": str(tmp_path / "p")})
    advice_wait(c)
    assert c.get("/api/advise").json()["root"] == str(orig)
