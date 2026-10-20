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
    from fsw import instrument as ins
    assert all(x["after_line"] >= 0 for x in ins._json_block(c["plan"])["inserts"])
    plan = ins.parse_plan(c["plan"], new, c["plan_steps"])                 # 插入时照常核对：格式对、每处都找得到
    assert plan["ok"], plan.get("message")


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


def test_report_compares_with_last_full_run_of_same_points(tmp_path):
    """报告和上次比：同一个项目、计时点一样、收齐 10 轮以上的最近一次；第一次不比；这次不到 10 轮说清不比。"""
    import shutil as _sh
    from fastapi.testclient import TestClient
    from fsw.web.app import create_app
    from fsw import standards as stds
    from tests.test_advise_api import real_log
    from tests.test_collect_api import FakeSerialDevice, wait as cwait
    from tests.test_standards import PAGE
    proj = tmp_path / "data" / "xiaozhi-1"

    def measure(rounds):
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                  cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.x"],
                                  device=FakeSerialDevice(real_log(rounds)), wait_step=0.01))
        c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": rounds, "project_dir": str(proj)})
        cwait(c, lambda j: j["status"] == "done")
        return c.get("/api/report").json()

    first = measure(20)
    assert first["prev"] is None                                            # 第一次：不比
    for i, run in enumerate(sorted((proj / "runs").iterdir())):             # 同一秒测的会写进同一个文件夹：挪开
        run.rename(proj / "runs" / f"2000010{i}-000000")
    second = measure(20)
    p = second["prev"]
    assert p["run"] == "20000100-000000" and p["n"] == 20
    assert set(p["segs"]) == {g["k"] for g in second["segments"]} and p["lines"][second["lines"][-1]["name"]] == second["lines"][-1]["med"]
    for i, run in enumerate(sorted(d for d in (proj / "runs").iterdir() if not d.name.startswith("2000"))):
        run.rename(proj / "runs" / f"2000020{i}-000000")
    few = measure(5)
    assert "不到 10 轮" in few["prev"]["why"]


def test_carry_still_works_after_reselecting_changed_source(setup, tmp_path):  # noqa: F811
    """真机走查时撞到的：改了源码、回首页重新选一次，码表把上次的插入方案和原版文件作废了，沿用无从比对。
    现在作废前留一份（fsw-last/），照样能比对、沿用。"""
    c, r, *_, src = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    from pathlib import Path
    proj = Path(r["project_dir"])
    (proj / "identify").mkdir(exist_ok=True)
    (proj / "identify" / "old.json").write_text(json.dumps({
        "ok": True, "what": WHAT, "provider": "x",
        "steps": [{"id": s["p"], "name": s["n"], "file": s["path"], "line": s["l"]} for s in STEPS]}, ensure_ascii=False), encoding="utf-8")
    prepend(src / "main" / "rfid_task.c", 1)
    r2 = c.post("/api/source/inspect", json={"path": str(src)}).json()
    assert r2["status"] == "changed" and not (proj / "fsw-plan.json").exists()      # 码表照旧作废
    got = cy.carry(proj, r2["work"], WHAT, "x")
    assert got["ok"] and {s["id"]: s["line"] for s in got["result"]["steps"]} == {"P1": 89, "P2": 132, "P5": 55}


def test_report_retest_button_and_wording_for_real_runs():
    """真机走查截图：报告底部「改完了，再测一次」点进的是演示用的旧页面；顶上写"回答这条"不像人话；原始日志那里还说"提示用新范围更新"。"""
    from tests.test_boss_short import JS, grab
    entry = grab("retestEntry")
    assert 'data-rtreal="1"' in entry and "data-retest=" in entry                   # 真测走新流程，演示数据照旧
    assert "e.target.closest('[data-rtreal]')){retestFrom(" in JS
    assert "repScene().lines" in grab("prevLine")  # 线名用报告里的说法（"喇叭出第一声"），不用"回答这条"
    assert "报告顶上会提示用新范围更新" not in JS


def test_user_asks_1017_noise_retest_label_advice_reuse_short_totals(run_ok=None):
    """10-17 用户：①和上次比：差值要同时大于 0.1 秒、大于上次的 10% 才写快了 / 慢了，否则写"持平"；②再测时步骤条不写"第一次测"；③问题没变沿用上次的"为什么、怎么改"；④两行合计名字改短、各段放悬停。"""
    from tests.test_boss_short import JS, grab
    c = grab("cmpTxt")
    assert "Math.abs(d)<=Math.max(.1,Math.abs(prev)*.1)" in c and "持平" in c  # 和后台 judge.vs_prev 同一条规则
    assert "flow==='retest'||remeasure?'改完再测':'第一次测'" in JS and "if(!REAL)return;remeasure=true;" in JS
    assert "沿用 ${htmlEsc(ADV.at)} 写的" in JS
    rows = next(x for x in JS.splitlines() if "const lineRows=" in x)
    assert 'class="tipname" title=' in rows and "合计</b>：" not in rows


def test_advice_signature_ignores_which_rounds_and_seconds():
    """问题没变就沿用：签名只看是哪几个问题、什么级别，不看哪几轮、几秒。"""
    from fsw import advise as advs
    a = {"key": "spiky:P1-P2", "seg": "P1-P2", "level": "建议改", "rounds": [5], "what": "偶尔特别慢（第 5 轮）"}
    b = {**a, "rounds": [3, 8], "what": "偶尔特别慢（第 3、8 轮）"}
    import unittest.mock as m
    with m.patch.object(advs, "problems", side_effect=[[a], [b]]):
        assert advs.signature({}, {}) == advs.signature({}, {})


def test_compare_noise_is_in_prev_payload(tmp_path):
    """和上次比带上"正常波动"：两次各自中间一半轮次的跨度，取大的。"""
    from fastapi.testclient import TestClient
    from fsw.web.app import create_app
    from tests.test_advise_api import real_log
    from tests.test_collect_api import FakeSerialDevice, wait as cwait
    from tests.test_standards import PAGE
    proj = tmp_path / "data" / "p"

    def measure():
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                  cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.x"],
                                  device=FakeSerialDevice(real_log(20)), wait_step=0.01))
        c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(proj)})
        cwait(c, lambda j: j["status"] == "done")
        return c.get("/api/report").json()
    measure()
    for i, run in enumerate(sorted((proj / "runs").iterdir())):
        run.rename(proj / "runs" / f"2000010{i}-000000")
    p = measure()["prev"]
    assert set(p["noise"]) == set(p["segs"]) and set(p["noise_lines"]) == set(p["lines"])
    assert all(v >= 0 for v in p["noise"].values())


def test_lines_follow_the_plan_when_saved_steps_are_from_another_version(setup, tmp_path):  # noqa: F811
    """升级走查撞到：存下的"找关键步骤"结果是更晚那次（行号多 1），留下的插入方案和原版是更早那次。
    挪行号以插入方案为准，步骤和插法对得上，插入时才能沿用、不调用大模型。"""
    proj, src = inserted(setup)
    (proj / "identify" / "old.json").write_text(json.dumps({
        "ok": True, "what": WHAT, "provider": "x",
        "steps": [{"id": s["p"], "name": s["n"], "file": s["path"], "line": s["l"] + 1} for s in STEPS]}, ensure_ascii=False), encoding="utf-8")
    new = changed_copy(src, tmp_path, lambda d: prepend(d / "main" / "rfid_task.c", 2))
    c = cy.carry(proj, new, WHAT, "x")
    lines = {s["id"]: s["line"] for s in c["result"]["steps"]}
    assert lines == {"P1": 90, "P2": 133, "P5": 55}
    assert [(p, l) for p, _, l in cy.sig(c["plan_steps"])] == [(k, v) for k, v in lines.items()]
