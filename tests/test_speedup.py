"""码表提速：插计时点提前（决策 96）。找完关键步骤、人核对步骤时，后台先按这些步骤生成插法；人没改步骤就直接用，改了就作废重新生成。

假模型、假编译，不真调用大模型。
"""

import time

import pytest
from fastapi.testclient import TestClient

from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.identify_fixtures import door_source
from tests.test_fsw_api import fake_build_output, wait as fwait
from tests.test_instrument import STEPS, reply as plan_reply


@pytest.fixture
def ins_app(tmp_path):
    made = []

    def factory(p, k):
        made.append(FakeLLM(plan_reply()))
        return made[-1]

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=factory,
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [],
                              bg_builder=builder, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    return c, r, made


def ins_body(r, steps=STEPS):
    return {"work": r["work"], "project_dir": r["project_dir"], "steps": steps, "chip": "ESP32-S3"}


def prep_wait(c, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/fsw/prepare").json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def test_plan_prepared_while_checking_steps_then_used(ins_app):
    c, r, made = ins_app
    assert c.post("/api/fsw/prepare", json=ins_body(r)).json()["status"] in ("running", "ready")
    assert prep_wait(c)["status"] == "ready"
    c.post("/api/fsw/insert", json=ins_body(r))
    j = fwait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted" and j["prepared"] and len(made) == 1  # 点确认不用再等大模型
    assert j["calls"] == 1


def test_changed_steps_discard_the_prepared_plan(ins_app):
    c, r, made = ins_app
    c.post("/api/fsw/prepare", json=ins_body(r))
    prep_wait(c)
    changed = [dict(STEPS[0], n="读到卡（改过名）")] + STEPS[1:]
    c.post("/api/fsw/insert", json=ins_body(r, changed))
    j = fwait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted" and j["redo"] and not j.get("prepared") and len(made) == 2
    assert j["calls"] == 2 and "作废" in j["redo"]  # 两次都算进调用次数，页面说清为什么重做


def test_prepare_twice_with_same_steps_calls_once(ins_app):
    c, r, made = ins_app
    c.post("/api/fsw/prepare", json=ins_body(r))
    c.post("/api/fsw/prepare", json=ins_body(r))
    prep_wait(c)
    assert len(made) == 1


def test_no_prepare_when_last_plan_can_be_reused(ins_app):
    c, r, made = ins_app
    c.post("/api/fsw/insert", json=ins_body(r))
    fwait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert len(made) == 1
    assert c.post("/api/fsw/prepare", json=ins_body(r)).json()["status"] == "reuse"  # 上次的插法能用：不提前调用
    assert len(made) == 1
