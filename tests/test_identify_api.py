"""任务 13 的网页后台：开始找、边找边看进度、出错重试、用上次的结果、AI 推荐、添加步骤。只用假模型。"""

import time

import pytest
from fastapi.testclient import TestClient

from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.identify_fixtures import DOOR_REPLY, door_source


@pytest.fixture
def make(tmp_path):
    def _make(llm, **kw):
        made = []

        def factory(provider, key):
            made.append(provider)
            return llm() if callable(llm) else llm
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=factory,
                                  cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [],
                                  idf_finder=lambda: [], **kw))
        c.post("/api/settings", json={"llm_provider": "cc"})
        return c, made
    return _make


def prepared(c, tmp_path):
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    assert r["ok"]
    return r


def wait(c, until=("done", "failed"), timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/identify").json()
        if j["state"] in until:
            return j
        time.sleep(0.05)
    raise AssertionError(j)


def test_find_steps_in_background(make, tmp_path):
    c, made = make(FakeLLM(DOOR_REPLY, chunk=30))
    src = prepared(c, tmp_path)
    j = c.post("/api/identify/start", json={"work": src["work"], "project_dir": src["project_dir"],
                                            "fingerprint": src["fingerprint"], "what": "从刷卡到开门"}).json()
    assert j["state"] == "running"
    j = wait(c)
    assert j["state"] == "done" and j["found"][0] == "读到卡" and j["seconds"] >= 0
    assert [s["p"] for s in j["page"]["steps"]] == ["P1", "P2", "P3", "P4", "P5"]
    assert j["provider"] == "claude-code" and made == ["claude-code"]


def test_no_model_saved_is_refused(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                              cc_status=lambda: {"found": False, "logged_in": False}, port_finder=lambda: []))
    r = c.post("/api/identify/start", json={"work": str(tmp_path), "what": "从刷卡到开门"})
    assert r.status_code == 409 and "设置" in r.json()["detail"]


def test_failure_then_retry_keeps_found(make, tmp_path):
    tries = []

    class Flaky(FakeLLM):
        def investigate(self, prompt, root, on_text=None, on_tool=None):
            tries.append(prompt)
            if len(tries) == 1:
                on_text("找到：读到卡（main/rfid_task.c:88）\n")
                raise self.fail("offline")
            return super().investigate(prompt, root, on_text, on_tool)
    m = Flaky(DOOR_REPLY)
    c, _ = make(m)
    src = prepared(c, tmp_path)
    c.post("/api/identify/start", json={"work": src["work"], "what": "从刷卡到开门"})
    j = wait(c)
    assert j["state"] == "failed" and j["kind"] == "offline" and j["found"] == ["读到卡"]
    j = c.post("/api/identify/retry").json()
    j = wait(c)
    assert j["state"] == "done" and "读到卡" in tries[1] and "接着找" in tries[1]


def test_reuse_last_result_when_nothing_changed(make, tmp_path):
    calls = []

    class Count(FakeLLM):
        def investigate(self, *a, **k):
            calls.append(1)
            return super().investigate(*a, **k)
    c, _ = make(Count(DOOR_REPLY))
    src = prepared(c, tmp_path)
    body = {"work": src["work"], "project_dir": src["project_dir"], "fingerprint": src["fingerprint"],
            "what": "从刷卡到开门", "reuse": True}
    c.post("/api/identify/start", json=body)
    wait(c)
    c.post("/api/identify/start", json=body)
    j = wait(c)
    assert j["state"] == "done" and j["reused"] and len(calls) == 1


def test_suggest_and_add_step(make, tmp_path):
    replies = iter(['["从刷卡到开门", "从刷卡到亮灯响一声", "从开机到连上服务器"]',
                    '```json\n{"name": "门锁到位", "file": "main/lock_ctrl.c", "line": 55, "why": "电磁锁动作", "sure": "中"}\n```'])
    c, _ = make(lambda: FakeLLM(next(replies)))
    src = prepared(c, tmp_path)
    s = c.post("/api/suggest", json={"work": src["work"], "name": src["info"]["name"]}).json()
    assert s["phrases"][0] == "从刷卡到开门"
    a = c.post("/api/identify/add", json={"work": src["work"], "sentence": "门锁到位", "what": "从刷卡到开门"}).json()
    assert a["ok"] and a["l"] == 55


def test_identify_never_changes_source(make, tmp_path):
    from fsw.workspace.workspace import fingerprint
    c, _ = make(FakeLLM(DOOR_REPLY))
    src = prepared(c, tmp_path)
    before = fingerprint(tmp_path / "door")
    c.post("/api/identify/start", json={"work": src["work"], "what": "从刷卡到开门"})
    wait(c)
    assert fingerprint(tmp_path / "door") == before and fingerprint(src["work"]) == src["fingerprint"]
