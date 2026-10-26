"""多轮实测的收点规则（定版排查 C 组）。

- C1：按这次实际插进去的计时点收点、出报告（不测的顺带线、删掉的、编译出错撤掉的不要，你自己加的要），
  "以前测过的"重算也按它；
- C2：一轮在下一轮开始或收全后等一会儿才收尾，每个点取起点之后的第一次（每次都记的点在起点前先记过一次也不丢）；
- C3：试一次和正式实测同一个标准（全部收到才算），正式实测里同一个点连着几轮没收到就点名。
全用假模型、假设备。
"""

import json
import time
from pathlib import Path

import pytest

from fsw import identify as idf
from fsw import instrument as ins
from fsw import judge as jdg
from fsw import measure as meas
from fsw import standards as stds
from fsw.llm import FakeLLM
from tests.identify_fixtures import DOOR_REPLY, door_source
from tests.test_collect_api import FakeSerialDevice, make  # noqa: F401
from tests.test_collect_api import wait as wait_col
from tests.test_fsw_api import FakeDevice, run_to_try, setup, start_insert  # noqa: F401
from tests.test_fsw_api import wait as wait_fsw
from tests.test_instrument import PLAN, reply
from tests.fixtures import XZ  # 小智那份标准：只当测试素材（10-18 起码表不自带任何设备的标准）


@pytest.fixture
def door_page(tmp_path):
    """门禁刷卡：AI 找到 5 步（P3 亮灯响一声是顺带的那条线）。"""
    return idf.identify(FakeLLM(DOOR_REPLY), door_source(tmp_path / "door"), "从刷卡到开门")["page"]


def fsw_steps(page, drop=(), face=True, added=()):
    """网页 fswSteps() 的样子：核对后的步骤（不测顺带线、删掉的不在里面，你加的接在后面）。"""
    rows = [s for s in page["steps"] if face or s["line"] != "face"] + list(added)
    return [{"p": s["p"], "n": s["n"], "path": s.get("path") or s["f"], "l": s["l"], "line": s["line"]}
            for s in rows if s["p"] not in drop]


def rounds_log(points, n=5, gap=100_000):
    """设备输出：每轮 RESET，再按给的先后打出这些计时点；最后再开始一轮，好让最后一轮收尾。"""
    out = []
    for r in range(n):
        base = 10_000_000 * (r + 1)
        out += [f"FSW,RESET,{base},0"] + [f"FSW,{p},{base + (k + 1) * gap},0" for k, p in enumerate(points)]
    return out + [f"FSW,RESET,{10_000_000 * (n + 1)},0"]


def spec_of(page):
    return meas.Spec.from_scenario(stds.real_scenario(page, stds.find(page["steps"], [XZ])))


# ---------- C1：按实际插进去的计时点收点 ----------
def test_not_measuring_the_side_line_still_completes_rounds(door_page):
    """不测顺带那条线：P3 没插，AI 最初找到的那份要 P3，每轮都"没收全"；按插进去的算就收得全。"""
    old = spec_of(door_page)
    assert "P3" in old.need and not [r for r in meas.collect(rounds_log(["P1", "P2", "P4", "P5"]), old) if r["ok"]]  # 旧问题
    page = stds.inserted_page(door_page, fsw_steps(door_page, face=False))
    spec = spec_of(page)
    assert spec.need == ["P1", "P2", "P4", "P5"] and len(spec.lines) == 1
    rs = meas.collect(rounds_log(["P1", "P2", "P4", "P5"]), spec)
    assert len(rs) == 5 and all(r["ok"] for r in rs)


def test_deleted_step_joins_the_two_segments_around_it(door_page):
    """删掉 P4（收到服务器校验结果）：P2→P4、P4→P5 接成一段 P2→P5，线照样从起点量到终点。"""
    page = stds.inserted_page(door_page, fsw_steps(door_page, drop={"P4"}))
    sc = stds.real_scenario(page, None)
    assert set(sc["segments"]) == {"P1-P2", "P2-P5", "P2-P3"}
    assert sc["segments"]["P2-P5"]["name"] == "服务器校验并返回＋电磁锁动作"
    assert sc["lines"][-1]["segments"] == ["P1-P2", "P2-P5"]
    rs = meas.collect(rounds_log(["P1", "P2", "P3", "P5"]), meas.Spec.from_scenario(sc))
    assert len(rs) == 5 and all(r["ok"] for r in rs) and rs[0]["ends"] == [pytest.approx(0.2), pytest.approx(0.3)]


def test_inserted_record_wins_over_the_steps_list(door_page):
    """编译出错撤掉了 P4：网页的步骤可能还带着它，插入记录里没有就不要。"""
    page = stds.inserted_page(door_page, fsw_steps(door_page), inserted={"P1", "P2", "P3", "P5"})
    assert spec_of(page).need == ["P1", "P2", "P3", "P5"]


def test_your_own_step_is_measured_and_in_the_report(door_page):
    """你自己加的 U1 插成 P21：进场景（从起点到这一步记一段）、要收这个点，报告里有名字和位置。"""
    mine = {"p": "U1", "n": "门磁报门开", "f": "door.c", "path": "main/door.c", "l": 30, "line": "talk"}
    page = stds.inserted_page(door_page, fsw_steps(door_page, added=[mine]))
    sc = stds.real_scenario(page, None)
    assert sc["segments"]["P1-P21"]["name"] == "门磁报门开"
    assert sc["point_names"]["P21"] == "门磁报门开" and sc["points"]["P21"] == {"n": "门磁报门开", "f": "door.c", "l": 30}
    spec = meas.Spec.from_scenario(sc)
    assert "P21" in spec.need
    r = meas.collect(rounds_log(["P1", "P2", "P3", "P4", "P5", "P21"]), spec)[0]
    assert r["ok"] and r["segs"]["P1-P21"] == pytest.approx(0.5)
    assert any(g["k"] == "P1-P21" for g in jdg.judge(sc, [r])["segments"])


def test_dropped_step_keeps_the_numbers_of_the_rest():
    """撤掉 U1（P21）后 U2 还是 P22（设备上打印的是插入时的编号）。"""
    steps = [{"p": "P1"}, {"p": "U1"}, {"p": "U2"}]
    kept = [{**x, "fsw": ins.point_id(x, steps)} for x in steps if ins.point_id(x, steps) != "P21"]
    assert [ins.point_id(x, kept) for x in kept] == ["P1", "P22"]


class TryAndStream(FakeDevice):
    """试一次读一段、多轮实测一直读：同一个假设备。"""

    def __init__(self, try_lines, col_lines):
        super().__init__(lines=try_lines)
        self.col = FakeSerialDevice(col_lines)

    def stream(self, port, on_line, stop):
        return self.col.stream(port, on_line, stop)


def test_collect_after_insert_uses_what_was_inserted(setup, door_page):
    """网页传的是 AI 最初找到的那份（5 步），实际只插了 P1、P2、P5：按插进去的收，报告、"以前测过的"也一样。"""
    c, r, *_ = setup(device=TryAndStream(None, rounds_log(["P1", "P2", "P5"], n=3)))
    start_insert(c, r)
    wait_fsw(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    c.post("/api/collect/start", json={"page": door_page, "source": "device", "rounds": 3, "project_dir": r["project_dir"]})
    j = wait_col(c, lambda j: j["status"] in ("done", "stopped") or len(j["rounds"]) >= 3)
    assert j["need"] == ["P1", "P2", "P5"] and [x["ok"] for x in j["rounds"]] == [True, True, True]
    rep = c.get("/api/report").json()
    assert set(rep["scenario"]["points"]) == {"P1", "P2", "P5"} and rep["n"] == 3
    run = json.loads((Path(j["raw_log"]).parent / "run.json").read_text(encoding="utf-8"))
    assert [s["p"] for s in run["page"]["steps"]] == ["P1", "P2", "P5"]  # 重算用的是插进去的那份
    h = next(x for x in c.get("/api/history").json() if x["project_dir"] == r["project_dir"])
    assert h["rounds"] == 3


# ---------- C2：一轮的收尾时机 ----------
FACE = {"start": {"point": "P1", "name": "做完"},
        "segments": {"a": {"from": "P1", "to": "P2", "name": "处理"}, "b": {"from": "P2", "to": "P3", "name": "回应"},
                     "f": {"from": "P1", "to": "P4", "name": "换提示"}},
        "lines": [{"name": "提示线", "segments": ["f"]}, {"name": "回应线", "segments": ["a", "b"]}],
        "problems": [{"key": "rev", "name": "提示比回应还晚", "level": "warn",
                      "rule": {"type": "line_order", "first": "提示线", "second": "回应线"}}]}


def mark_log(n=20):
    """P4 每次都记：起点前先记一次；每 4 轮有 1 轮它比 P3 还晚（真问题）。"""
    lines = []
    for r in range(n):
        base = 10_000_000 * (r + 1)
        lines += [f"FSW,RESET,{base},0", f"FSW,P4,{base + 50_000},0", f"FSW,P1,{base + 2_000_000},0",
                  f"FSW,P2,{base + 2_600_000},0"]
        if r % 4:
            lines += [f"FSW,P4,{base + 2_900_000},0", f"FSW,P3,{base + 3_200_000},0"]
        else:
            lines += [f"FSW,P3,{base + 3_200_000},0", f"FSW,P4,{base + 3_500_000},0"]
    return lines + ["FSW,RESET,999000000,0"]


def test_every_time_mark_arriving_last_is_judged_not_dropped():
    """旧的：每个点到过一次就收尾，P4 起点前那次算到过，真正那次最晚到时已收尾 → 5 轮判成"开始之前的"丢掉、先后反了判不出。"""
    rs = meas.collect(mark_log(), meas.Spec.from_scenario(FACE))
    ok = [r for r in rs if r["ok"]]
    assert len(ok) == 20 and not [r for r in rs if r.get("stale")]
    assert [r["n"] for r in ok if "rev" in r["probs"]] == [1, 5, 9, 13, 17]
    j = jdg.judge({**FACE, "experience": None}, ok)
    assert next(x for x in j["lines"] if x["name"] == "提示线")["max"] == pytest.approx(1.5)


def test_progress_says_complete_right_away_and_closes_later():
    """起点之后每个点都到了，进度马上显示收全；收尾等下一轮开始，或收全后等一会儿（flush），停下时也照算。"""
    spec = meas.Spec.from_scenario(FACE)
    c = meas.Collector(spec)
    out = []
    for l in mark_log(1)[:-1][:5]:  # 第 1 轮：P3 到了，P4 还只有起点之前那次
        out += c.feed(l)
    assert out == [] and c.progress() == (3, 4) and not c.ready
    out += c.feed("FSW,P4,13500000,0")
    assert out == [] and c.ready and c.progress() == (4, 4)  # 收全了：马上显示，不用等收尾
    assert c.flush(settle=60) == []  # 还没等够
    r = c.flush()
    assert r[0]["ok"] and r[0]["probs"] == ["rev"] and c.progress() == (0, 4)
    c2 = meas.Collector(spec)
    for l in mark_log(1)[:-1]:
        c2.feed(l)
    assert c2.finish()[0]["ok"]  # 停下时已经收全的那一轮照算


def test_only_marks_before_the_start_is_still_stale():
    """记到的只有起点之前那次（插的位置不对）：下一轮开始时照旧判成"记到的是这一轮开始之前的"。"""
    spec = meas.Spec.from_scenario(FACE)
    rs = meas.collect(["FSW,RESET,0,0", "FSW,P4,10000,0", "FSW,P1,2000000,0", "FSW,P2,2100000,0", "FSW,P3,2200000,0",
                       "FSW,RESET,9000000,0"], spec)
    assert rs[0]["stale"] and "记到的是这一轮开始之前的" in rs[0]["missing"][0]


def test_last_round_on_the_device_closes_without_a_next_round(make):  # noqa: F811
    """最后一轮收全后设备不再开始下一轮：等一会儿收尾，测满就停。"""
    log = rounds_log(["P1", "P2"], n=3)[:-1]
    c, _ = make(device=FakeSerialDevice(log))
    page = {"segs": [{"a": "P1", "b": "P2"}], "steps": [{"p": "P1", "n": "起"}, {"p": "P2", "n": "止"}]}
    t = time.time()
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3})
    j = wait_col(c, lambda j: j["status"] == "done", timeout=10)
    assert len([r for r in j["rounds"] if r["ok"]]) == 3 and time.time() - t < 8


# ---------- C3：试一次和正式实测同一个标准 ----------
PLAN5 = {**PLAN, "inserts": PLAN["inserts"] + [
    {"step": "include", "file": "main/ui_led.c", "after_line": 0, "lines": ['#include "fw_stopwatch.h"']},
    {"step": "P3", "file": "main/ui_led.c", "after_line": 42, "lines": ["    // 计时点 P3：亮灯响一声", "    FSW_MARK_ONCE(3, 0);"]},
    {"step": "include", "file": "main/net_auth.c", "after_line": 0, "lines": ['#include "fw_stopwatch.h"']},
    {"step": "P4", "file": "main/net_auth.c", "after_line": 207, "lines": ["        // 计时点 P4：收到服务器校验结果", "        FSW_MARK_ONCE(4, 0);"]},
]}


def test_try_once_needs_every_point_and_names_the_missing_one(setup, door_page):
    """插了 5 个点，试一次只收到 4 个：旧的算试通（正式实测却每轮没收全）；现在不算试通，说差的是哪一个。"""
    lines = ["boot ok", "FSW,RESET,1,0", "FSW,P1,10,0", "FSW,P2,20,0", "FSW,P3,25,0", "FSW,P5,30,0"]
    c, r, *_ = setup(device=FakeDevice(lines=lines), llm_reply=reply(PLAN5))
    c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": fsw_steps(door_page),
                                    "chip": "ESP32-S3"})
    wait_fsw(c, lambda j: j["status"] in ("inserted", "failed"))
    j = run_to_try(c)
    assert j["status"] == "failed" and j["err"]["kind"] == "try"
    assert "4 / 5" in j["err"]["message"] and "「收到服务器校验结果」" in j["err"]["message"]
    assert j["err"]["missing"] == ["收到服务器校验结果"]


def test_same_point_missing_round_after_round_is_named(make):  # noqa: F811
    """正式实测：同一个点连着 3 轮没收到，提示里点名。"""
    page = {"segs": [{"a": "P1", "b": "P2"}, {"a": "P2", "b": "P3"}],
            "steps": [{"p": "P1", "n": "刷卡"}, {"p": "P2", "n": "核对完"}, {"p": "P3", "n": "开锁"}]}
    c, _ = make(device=FakeSerialDevice(rounds_log(["P1", "P2"], n=3)))
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3})
    j = wait_col(c, lambda j: len(j["rounds"]) >= 3)
    assert "「开锁」连着 3 轮都没收到" in j["half_hint"]
    c.post("/api/collect/stop")


def test_inserted_steps_survive_the_record_being_restored_away(make, door_page, tmp_path):  # noqa: F811
    """工作副本还原后插入记录（record.json）没了、码表也重开过：按插好时存的 fsw-inserted.json 收点；
    这份读不了就按网页传来的算，不出错。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fsw-inserted.json").write_text(json.dumps({"steps": fsw_steps(door_page), "points": ["P1", "P2", "P3", "P5"]}),
                                            encoding="utf-8")
    c, _ = make(device=FakeSerialDevice(rounds_log(["P1", "P2", "P3", "P5"], n=3)))
    j = c.post("/api/collect/start", json={"page": door_page, "source": "device", "rounds": 3, "project_dir": str(proj)}).json()
    assert j["need"] == ["P1", "P2", "P3", "P5"]
    c.post("/api/collect/stop")
    (proj / "fsw-inserted.json").write_text("{坏的", encoding="utf-8")
    j = c.post("/api/collect/start", json={"page": door_page, "source": "device", "rounds": 3, "project_dir": str(proj)}).json()
    assert j["need"] == ["P1", "P2", "P3", "P4", "P5"]
    c.post("/api/collect/stop")


def test_collect_done_restores_the_original_by_itself(setup, door_page):
    """10 轮收齐：码表自己还原（源码放回、原版烧回），不靠人记得去点；计时版那次仍算试通了，报告照常。"""
    dev = TryAndStream(None, rounds_log(["P1", "P2", "P5"], n=3))
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait_fsw(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    c.post("/api/collect/start", json={"page": door_page, "source": "device", "rounds": 3, "project_dir": r["project_dir"]})
    wait_col(c, lambda j: j["status"] == "done")
    j = wait_fsw(c, lambda j: j.get("restore") and not j["restore"].get("running"))
    assert j["status"] == "done" and j["restore"]["auto"] and j["restore"]["flashed"] and j["restore"]["same"]
    assert dev.flashed[-1].endswith("firmware/orig")
    assert not json.loads((Path(r["project_dir"]) / "on-device.json").read_text(encoding="utf-8"))["fsw"]


def test_stopped_half_way_does_not_restore(setup, door_page):
    """中途停下：可能还要接着测，不自己还原，留按钮。"""
    dev = TryAndStream(None, rounds_log(["P1", "P2", "P5"], n=1))
    c, r, *_ = setup(device=dev)
    start_insert(c, r)
    wait_fsw(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    c.post("/api/collect/start", json={"page": door_page, "source": "device", "rounds": 3, "project_dir": r["project_dir"]})
    wait_col(c, lambda j: len(j["rounds"]) >= 1)
    c.post("/api/collect/stop")
    wait_col(c, lambda j: j["status"] == "stopped")
    time.sleep(0.2)
    assert "restore" not in c.get("/api/fsw").json() and not dev.flashed[-1].endswith("firmware/orig")


def test_point_wrong_round_after_round_pauses_and_asks(make):  # noqa: F811
    """测到一半同一个点连着几轮不对：和第 1 轮自动校验没通过一样停下来问（不只是一行提示）；
    选"接着测"记下来、不再为同一个点停；报告的数据检查里写选了什么（10-26 真测：提示了没人停，那几段只有 1 轮的数）。"""
    page = {"segs": [{"a": "P1", "b": "P2"}, {"a": "P2", "b": "P3"}],
            "steps": [{"p": "P1", "n": "刷卡"}, {"p": "P2", "n": "核对完"}, {"p": "P3", "n": "开锁"}]}
    dev = FakeSerialDevice(rounds_log(["P1", "P2"], n=3) + rounds_log(["P1", "P2", "P3"], n=4))
    c, _ = make(device=dev)
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3})
    j = wait_col(c, lambda j: j["status"] == "suspect")
    assert j["suspect"][0]["kind"] == "miss" and j["suspect"][0]["name"] == "开锁"
    assert "连着 3 轮都没收到" in j["suspect"][0]["why"]
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3, "resume": True})
    j = wait_col(c, lambda j: j["status"] in ("done", "suspect", "stopped"))
    assert j["status"] == "done" and j["choices"][0]["choice"] == "continue"
    assert json.loads((Path(j["raw_log"]).parent / "choices.json").read_text(encoding="utf-8"))[0]["items"][0]["name"] == "开锁"
    rep = c.get("/api/report").json()
    assert any(x["kind"] == "choice" and "接着测" in x["why"] for x in rep["checks"])


def test_fix_choice_is_recorded(make):  # noqa: F811
    page = {"segs": [{"a": "P1", "b": "P2"}, {"a": "P2", "b": "P3"}],
            "steps": [{"p": "P1", "n": "刷卡"}, {"p": "P2", "n": "核对完"}, {"p": "P3", "n": "开锁"}]}
    c, _ = make(device=FakeSerialDevice(rounds_log(["P1", "P2"], n=3)))
    assert c.post("/api/collect/choice", json={"choice": "fix"}).status_code == 409
    c.post("/api/collect/start", json={"page": page, "source": "device", "rounds": 3})
    wait_col(c, lambda j: j["status"] == "suspect")
    j = c.post("/api/collect/choice", json={"choice": "fix"}).json()
    assert j["choices"][-1]["choice"] == "fix"
