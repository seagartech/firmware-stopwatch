"""任务 22 改完再测：改了代码、但计时点那几处没动，就沿用上次的步骤和插法（行号跟着挪），不调用大模型；动了就重做并说出哪几处。"""

import json
import shutil

from fsw import carry as cy
from tests.test_fsw_api import setup, start_insert, wait  # noqa: F401  （测试夹具）
from tests.test_instrument import STEPS

WHAT = "从刷卡到开门"


def inserted(setup):  # noqa: F811
    c, r, *_ , src = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    proj = r["project_dir"]
    from pathlib import Path
    (Path(proj) / "identify").mkdir(exist_ok=True)
    (Path(proj) / "identify" / "old.json").write_text(json.dumps({
        "ok": True, "what": WHAT, "provider": "x",
        "steps": [{"id": s["p"], "name": s["n"], "file": s["path"], "line": s["l"]} for s in STEPS]}, ensure_ascii=False), encoding="utf-8")
    return Path(proj), src


def changed_copy(src, tmp_path, edit):
    new = tmp_path / "new"
    shutil.copytree(src, new)
    edit(new)
    return new


def prepend(path, n):
    t = path.read_text(encoding="utf-8")
    path.write_text("// 改了代码\n" * n + t, encoding="utf-8")


def test_code_above_points_changed_lines_move_and_no_llm(setup, tmp_path):  # noqa: F811
    proj, src = inserted(setup)
    new = changed_copy(src, tmp_path, lambda d: prepend(d / "main" / "rfid_task.c", 2))
    c = cy.carry(proj, new, WHAT, "x")
    assert c["ok"] and c["moved"] >= 2
    lines = {s["id"]: s["line"] for s in c["result"]["steps"]}
    assert lines == {"P1": 90, "P2": 133, "P5": 55}                       # 上面加了 2 行：rfid 的两处往下挪 2 行，门锁那处不动
    assert cy.sig(c["plan_steps"]) == [("P1", "main/rfid_task.c", 90), ("P2", "main/rfid_task.c", 133), ("P5", "main/lock_ctrl.c", 55)]
    assert all(x["after_line"] >= 0 for x in json.loads(c["plan"])["inserts"])


def test_code_at_a_point_changed_redo_and_say_which(setup, tmp_path):  # noqa: F811
    proj, src = inserted(setup)

    def edit(d):
        p = d / "main" / "rfid_task.c"
        lines = p.read_text(encoding="utf-8").splitlines()
        lines[130] = lines[130] + "  // 这一行改过"                         # 卡号读完（第 131 行）那一处
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    c = cy.carry(proj, changed_copy(src, tmp_path, edit), WHAT, "x")
    assert not c["ok"] and "卡号读完" in c["changed"]


def test_nothing_to_carry_without_last_plan_or_a_different_target(setup, tmp_path):  # noqa: F811
    proj, src = inserted(setup)
    assert not cy.carry(proj, src, "从按键到亮灯", "x")["ok"]                # 要测的不一样
    (proj / "fsw-plan.json").unlink()
    assert not cy.carry(proj, src, WHAT, "x")["ok"]


def test_identify_uses_carry_and_does_not_call_llm(tmp_path, monkeypatch):
    """找关键步骤：第一次照常找（假模型）；改了源码再找，carry 说计时点那几处没动 → 用挪过行号的结果和插法，大模型一次都不调。"""
    import json as _j
    from pathlib import Path
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import DOOR_REPLY, door_source
    from tests.test_identify_api import wait as iwait
    calls = []

    class Once(FakeLLM):
        def investigate(self, *a, **k):
            calls.append(1)
            assert len(calls) == 1, "改完再测不该再调用大模型"
            return super().investigate(*a, **k)
    llm = Once(DOOR_REPLY)
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: llm,
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [], idf_finder=lambda: []))
    c.post("/api/settings", json={"llm_provider": "cc"})
    src = door_source(tmp_path / "door")
    r = c.post("/api/source/inspect", json={"path": str(src)}).json()
    body = {"work": r["work"], "project_dir": r["project_dir"], "fingerprint": r["fingerprint"], "what": WHAT, "reuse": True}
    c.post("/api/identify/start", json=body)
    first = iwait(c)
    assert first["state"] == "done" and len(calls) == 1
    old = next(f for f in (Path(r["project_dir"]) / "identify").glob("*.json"))
    res = _j.loads(old.read_text(encoding="utf-8"))
    res["steps"] = [{**x, "line": x["line"] + 2} for x in res["steps"]]
    monkeypatch.setattr(cy, "carry", lambda *a: {"ok": True, "result": res, "plan": '{"inserts": []}', "moved": 2,
                                                  "plan_steps": [{**s, "l": s["l"] + 2} for s in STEPS]})
    prepend(src / "main" / "rfid_task.c", 2)                                     # 改了代码
    r2 = c.post("/api/source/inspect", json={"path": str(src)}).json()
    assert r2["fingerprint"] != r["fingerprint"]
    c.post("/api/identify/start", json={**body, "work": r2["work"], "fingerprint": r2["fingerprint"]})
    j = iwait(c)
    assert j["state"] == "done", (j.get("message"), j.get("kind"))
    assert j["reused"] and j["carried"]["moved"] == 2 and len(calls) == 1
    plan = _j.loads((Path(r["project_dir"]) / "fsw-plan.json").read_text(encoding="utf-8"))
    assert plan["fingerprint"] == r2["fingerprint"] and cy.sig(plan["steps"])[0] == ("P1", "main/rfid_task.c", 90)
