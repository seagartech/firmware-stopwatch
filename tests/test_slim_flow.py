"""精简流程（决策 158）：第 1 轮收齐后自动校验计时点位置；经过网络或服务器的一段短得不合常理就停下，
不让后面白测；人看过后可以忽略、接着测。全是假设备，不真读串口。"""

import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw import measure as m
from fsw.web.app import create_app
from tests.test_collect_api import FakeSerialDevice

DEMO = Path(__file__).resolve().parent.parent / "fsw/web/demo"
DOOR = json.loads((DEMO / "door.json").read_text(encoding="utf-8"))


def door_lines(check_first, rounds=12):
    d = copy.deepcopy(DOOR)
    d["result"]["segments"]["check"][0] = check_first  # 第 1 轮"服务器校验并返回"多久
    return list(m.demo_lines(d, rounds))


def test_reversed_segment_is_suspect_but_a_very_short_server_segment_is_not():
    sc = DOOR["scenario"]
    assert m.suspects({"segs": {"check": -0.2}}, sc) == [
        {"segment": "check", "name": "服务器校验并返回", "seconds": -0.2, "why": "这段终点比起点先记到，两个计时点的先后对不上"}]
    assert m.suspects({"segs": {"check": 0.001}}, sc) == []  # 服务器连着发两条消息：真的只有一两毫秒


def test_device_segments_of_a_few_ms_are_fine():
    # "亮灯响一声"在设备里，几毫秒很正常：不校验
    assert m.suspects({"segs": {"check": 0.4, "beep": 0.004, "read": None}}, DOOR["scenario"]) == []


@pytest.fixture
def make(tmp_path):
    def _make(lines):
        dev = FakeSerialDevice(lines)
        return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                     cc_status=lambda: {"found": True, "logged_in": True},
                                     port_finder=lambda: ["/dev/cu.usbmodem1101"], device=dev, wait_step=0.01))
    return _make


def wait(c, until, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get("/api/collect").json()
        if until(j):
            return j
        time.sleep(0.02)
    raise AssertionError(j)


def test_first_round_check_stops_measuring(make):
    c = make(door_lines(-0.2))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    j = wait(c, lambda j: j["status"] not in ("running", "waiting_device"))
    assert j["status"] == "suspect" and j["suspect"][0]["name"] == "服务器校验并返回"
    assert len([r for r in j["rounds"] if r["ok"]]) == 1  # 第 1 轮收齐就停，没接着白测


def test_ignore_and_resume_does_not_check_again(make):
    c = make(door_lines(-0.2))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    wait(c, lambda j: j["status"] == "suspect")
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10, "resume": True})
    j = wait(c, lambda j: j["status"] == "done")
    assert j["suspect"] is None and len([r for r in j["rounds"] if r["ok"]]) == 10


def test_normal_first_round_runs_through(make):
    c = make(door_lines(0.4))
    c.post("/api/collect/start", json={"demo": "door", "source": "device", "rounds": 10})
    j = wait(c, lambda j: j["status"] not in ("running", "waiting_device"))
    assert j["status"] == "done" and not j.get("suspect")


def test_home_page_has_no_what_input_and_starts_with_one_button():
    js = (Path(__file__).resolve().parent.parent / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert 'id="h-what"' not in js and 'data-autostart="1"' in js
    assert "let colVar='b',colN=AUTO_ROUNDS" in js and "const AUTO_ROUNDS=10" in js


def _page_with_touch_start():
    # 点屏幕开始（P1）只在点屏幕时才走；语音一轮从"你说完"（P2）起
    steps = [{"p": "P1", "n": "点屏幕开始", "line": "talk", "opt": True},
             {"p": "P2", "n": "你说完", "line": "talk"},
             {"p": "P3", "n": "收到回答", "line": "talk"},
             {"p": "P4", "n": "开口", "line": "talk"}]
    segs = [{"a": "P1", "b": "P2", "t": "听你说"}, {"a": "P2", "b": "P3", "t": "服务器", "out": True},
            {"a": "P3", "b": "P4", "t": "放声音"}]
    return {"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "开口这条"}}


def test_trigger_only_point_is_not_on_the_line_and_not_required():
    from fsw import standards as stds
    page = stds.inserted_page(_page_with_touch_start())
    assert [g["a"] for g in page["segs"]] == ["P2", "P3"]  # 线从每轮都经过的那一步起
    sc = stds.real_scenario(page, None)
    assert sc["start"]["point"] == "P2" and sc["optional"] == ["P1"]
    spec = m.Spec.from_scenario(sc)
    assert "P1" not in spec.need and {"P2", "P3", "P4"} <= set(spec.need)


def test_identify_marks_trigger_only_steps_optional():
    from fsw import identify
    assert '"always"' in identify.PROMPT
    r = {"steps": [{"id": "P1", "name": "点屏幕", "file": "a.c", "line": 1, "line_of": "main", "why": "", "sure": "高",
                    "code": "", "loc_ok": True, "where": "", "once": "", "func": "", "doubt": "", "always": False},
                   {"id": "P2", "name": "你说完", "file": "a.c", "line": 2, "line_of": "main", "why": "", "sure": "高",
                    "code": "", "loc_ok": True, "where": "", "once": "", "func": "", "doubt": "", "always": True}],
         "segments": [{"from": "P1", "to": "P2", "name": "x"}]}
    steps = identify.to_page(r, "全程")["steps"]
    assert steps[0].get("opt") is True and "opt" not in steps[1]



def _talk_spec():
    sc = {"start": {"point": "P5", "name": "你说完"},
          "segments": {"a": {"from": "P5", "to": "P6", "name": "服务器"}, "b": {"from": "P6", "to": "P7", "name": "放声音"}},
          "lines": [{"name": "开口这条", "segments": ["a", "b"]}]}
    return m.Spec.from_scenario(sc)


def test_last_sound_ending_after_the_round_marker_counts_for_its_own_round():
    # 实测顺序：…P6 → RESET → P5（下一轮）→ P7（上一轮最后一声放完，晚 0.13 秒）→ P6 …
    us = lambda s: f"{int(s * 1e6)}"
    lines = ["FSW,RESET," + us(0) + ",0",
             "FSW,P5," + us(1.0) + ",0", "FSW,P6," + us(2.0) + ",0", "FSW,RESET," + us(4.0) + ",0",
             "FSW,P5," + us(4.05) + ",0", "FSW,P7," + us(4.13) + ",0", "FSW,P6," + us(5.0) + ",0",
             "FSW,RESET," + us(7.0) + ",0", "FSW,P7," + us(7.1) + ",0", "FSW,RESET," + us(9.0) + ",0"]
    c = m.Collector(_talk_spec())
    out = []
    for l in lines:
        out += c.feed(l)
    ok = [r for r in out if r.get("ok")]
    assert len(ok) == 2
    assert [round(x, 2) for x in ok[0]["ends"]] == [3.13] and [round(x, 2) for x in ok[1]["ends"]] == [3.05]


def test_leftover_end_of_a_round_already_judged_is_not_taken_by_the_next_round():
    # 唤醒回应那一轮没有起点（不算一轮），它最后一声放完晚到：不能被下一轮当成自己的终点
    us = lambda s: f"{int(s * 1e6)}"
    lines = ["FSW,RESET," + us(0) + ",0", "FSW,P6," + us(1.0) + ",0", "FSW,RESET," + us(3.0) + ",0",
             "FSW,P5," + us(3.05) + ",0", "FSW,P7," + us(3.1) + ",0", "FSW,P6," + us(4.0) + ",0", "FSW,P7," + us(5.0) + ",0",
             "FSW,RESET," + us(6.0) + ",0"]
    out = []
    c = m.Collector(_talk_spec())
    for l in lines:
        out += c.feed(l)
    ok = [r for r in out if r.get("ok")]
    assert len(ok) == 1 and round(ok[0]["ends"][0], 2) == 1.95


def test_last_sound_from_before_the_start_does_not_block_the_late_tail():
    # 亲测第 7 轮起：这一轮开头先记到一次"最后一声"（唤醒回应放完），真正的最后一声在结束标记之后 0.13 秒才到。
    # 不能因为"最后一声"已经有一次就不等尾巴、判成"记到的是这一轮开始之前的"
    us = lambda s: f"{int(s * 1e6)}"
    lines = ["FSW,RESET," + us(0) + ",0", "FSW,P5," + us(1.0) + ",0", "FSW,P6," + us(2.0) + ",0", "FSW,P7," + us(3.0) + ",0",
             "FSW,RESET," + us(4.0) + ",0", "FSW,P7," + us(4.1) + ",0",
             "FSW,P5," + us(6.0) + ",0", "FSW,P6," + us(7.0) + ",0", "FSW,RESET," + us(9.0) + ",0", "FSW,P7," + us(9.13) + ",0",
             "FSW,RESET," + us(12.0) + ",0"]
    c = m.Collector(_talk_spec())
    out = []
    for l in lines:
        out += c.feed(l)
    assert not [r for r in out if r.get("ok") is False]
    ok = [r for r in out if r.get("ok")]
    assert len(ok) == 2 and round(ok[1]["ends"][0], 2) == 3.13


def test_found_list_ignores_half_written_last_line():
    from fsw import identify
    assert identify.found_names("找到：听到唤醒词（a.cc:1）\n找到：设") == ["听到唤醒词"]
    got = []

    class LLM:
        def investigate(self, prompt, root, on_text, on_tool):
            for piece in ["找到：听", "到唤醒词（a.cc:1）\n找到：设", "备听到你停了（b.cc:2）\n"]:
                on_text(piece)
            return "```json\n{}\n```"
    identify.identify(LLM(), ".", "全程", on_progress=lambda p: got.append(p["found"]))
    assert got[-1] == ["听到唤醒词", "设备听到你停了"]


# 亲测第 9 次（小灵）的原始计时点，第 5 轮起：第 6 轮回答中途播放卡了一下，多打了一次 P10（喇叭最后一声）
REAL9 = """RESET,63794499 P10,63929815 P1,67792209 P4,67797493 P5,67808822 P2,68518562 P3,68519451 P6,68921469 P7,68928223
P8,68929822 P9,70604169 RESET,70604916 P10,70729825 P1,74260329 P4,74263473 P5,74266290 P2,75557807 P3,75558734 P6,75931468
P7,75938576 P8,75939846 P9,77196188 RESET,77196857 P10,77319831 P1,80411278 P4,80414096 P5,80417397 P2,81190070 P3,81190964
P6,81697723 P7,81704603 P8,81709809 P10,82069812 P9,84775270 RESET,84776372 P10,84909814 P1,88550912 P4,88553741 P5,88557050
P2,89380240 P3,89381145 P6,89763188 P7,89770094 P8,89779871 P9,91270466 RESET,91271410 P10,91399813 P1,95937161 P4,95938663
P5,95941214 P2,96765791 P3,96766662 P6,97202890 P7,97209229 P8,97209814 P9,98946558 RESET,98947221 P10,99009861 P1,103312839
P4,103315727 P5,103319002 P2,104218078 P3,104218953 P6,104594319 P7,104609255 P8,104619812 P9,107593904 RESET,107594744
P10,107739859 RESET,120000000"""


def test_end_point_printed_twice_mid_answer_does_not_shift_later_rounds():
    order = ["P1", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
    sc = {"start": {"point": "P1", "name": "收到听写文字"}, "optional": ["P2", "P3"],
          "segments": {f"s{i}": {"from": a, "to": b, "name": f"{a}-{b}"} for i, (a, b) in enumerate(zip(order, order[1:]))},
          "lines": [{"name": "回答这条", "segments": [f"s{i}" for i in range(len(order) - 1)]}]}
    c = m.Collector(m.Spec.from_scenario(sc))
    out = []
    for x in REAL9.split():
        k, us = x.split(",")
        out += c.feed(f"FSW,{k},{us},0")
    assert not [r for r in out if r.get("ok") is False]
    ends = [round(r["ends"][0], 2) for r in out if r.get("ok")]
    assert ends == [2.94, 3.06, 4.5, 2.85, 3.07, 4.43]  # 第 6 轮（水在几度结冰）是 4.5 秒，不是 1.66


def test_side_line_points_not_every_round_stay_on_the_side_line():
    # 亲测第 11 次：表情的两个点被标"不一定每轮都有"（服务器不一定每轮发表情），整条表情线被去掉了
    from fsw import standards as stds
    pg = _page_with_touch_start()
    pg["steps"] += [{"p": "P5", "n": "收到表情", "line": "face", "opt": True},
                    {"p": "P6", "n": "换上表情", "line": "face", "opt": True}]
    pg["fsegs"] = [{"a": "P2", "b": "P5", "t": "等表情"}, {"a": "P5", "b": "P6", "t": "换表情"}]
    page = stds.inserted_page(pg)
    assert [(g["a"], g["b"]) for g in page["fsegs"]] == [("P2", "P5"), ("P5", "P6")]
    spec = m.Spec.from_scenario(stds.real_scenario(page, None))
    assert len(spec.lines) == 2 and "P6" not in spec.need  # 没换表情的那一轮照样收齐，只是表情线那一轮算不出来
    cur = {"P2": (1_000_000, 0), "P3": (2_000_000, 0), "P4": (3_000_000, 0)}
    assert None in m.compute(cur, spec)["ends"]


def test_voice_main_line_gets_the_reference_experience_standard():
    from fsw import standards as stds
    sc = {"lines": [{"name": "回答这条", "segments": ["s"]}], "segments": {"s": {"from": "P1", "to": "P2", "name": "x"}},
          "point_names": {"P1": "设备听出你说完", "P2": "喇叭出第一声"}, "experiences": [], "start": {"point": "P1", "name": "设备听出你说完"}}
    e = stds.with_default_experience(sc)["experiences"]
    assert e[0]["line"] == "回答这条" and e[0]["fair"] == 1.5 and e[0]["source"]
    door = {**sc, "point_names": {"P1": "刷卡", "P2": "开门"}}
    assert stds.with_default_experience(door)["experiences"] == []  # 不是语音的不乱套
    late = {**sc, "point_names": {"P1": "收到听写文字", "P2": "喇叭出第一声"}, "start": {"point": "P1", "name": "收到听写文字"}}
    assert stds.with_default_experience(late)["experiences"] == []  # 10-18 真机：起点在听写之后，少算了一段，不拿 1.5 秒判



def test_whole_chain_gets_a_first_sound_line_and_only_it_gets_the_reference():
    # 亲测第 11 次：全程的终点是"喇叭最后一声"，回答越长越久；1.5 秒参考值说的是"说完到听到回答开头"
    from fsw import standards as stds
    steps = [{"p": p, "n": n, "line": "talk"} for p, n in
             [("P1", "设备听出你说完"), ("P2", "收到开口通知"), ("P3", "喇叭出第一声"), ("P4", "喇叭最后一声")]]
    segs = [{"a": "P1", "b": "P2", "t": "服务器"}, {"a": "P2", "b": "P3", "t": "等声音"}, {"a": "P3", "b": "P4", "t": "边收边放"}]
    sc = stds.with_default_experience(stds.real_scenario({"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "回答这条"}}, None))
    names = [l["name"] for l in sc["lines"]]
    assert names == ["到喇叭出第一声", "到喇叭最后一声"] and sc["lines"][0]["segments"] == ["P1-P2", "P2-P3"]
    assert [e["line"] for e in sc["experiences"]] == ["到喇叭出第一声"]
    spec = m.Spec.from_scenario(sc)
    cur = {"P1": (0, 0), "P2": (1_000_000, 0), "P3": (1_800_000, 0), "P4": (4_000_000, 0)}
    assert m.compute(cur, spec)["ends"] == [1.8, 4.0]
    from fsw import judge as jdg
    rounds = [{"n": i + 1, **m.compute(cur, spec)} for i in range(10)]
    first = jdg.judge(sc, rounds)["lines"][0]
    assert first["exp"] == "差" and first["combo"] == "体验差：慢在「服务器」（1.00 秒，占 56%）"


def test_playing_the_answer_is_not_flagged_as_occasionally_slow():
    # 亲测第 12 次："边收边放，直到服务器说完"被标"偶尔特别慢"：那段长短看回答说了多少
    from fsw import standards as stds, judge as jdg
    steps = [{"p": p, "n": n, "line": "talk"} for p, n in
             [("P1", "收到听写文字"), ("P2", "收到开口通知"), ("P3", "喇叭出第一声"), ("P4", "喇叭最后一声")]]
    segs = [{"a": "P1", "b": "P2", "t": "服务器"}, {"a": "P2", "b": "P3", "t": "等声音"}, {"a": "P3", "b": "P4", "t": "边收边放"}]
    sc = stds.real_scenario({"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "回答这条"}}, None)
    spec = m.Spec.from_scenario(sc)
    rounds = []
    for i in range(10):
        play = 5.0 if i in (2, 8) else 1.5
        cur = {"P1": (0, 0), "P2": (1_000_000, 0), "P3": (1_800_000, 0), "P4": (int((1.8 + play) * 1e6), 0)}
        rounds.append({"n": i + 1, **m.compute(cur, spec)})
    assert not [x for x in jdg.judge(sc, rounds)["labels"] if x["kind"] == "spiky"]


def test_playing_the_answer_is_not_judged_by_range_and_every_line_is_listed():
    # 亲测第 19 次：再测时"边收边放"按上次填的范围判"偏慢"、到最后一声那条也按范围判：长短看回答说了多少字，不判快慢
    from fsw import standards as stds, judge as jdg
    steps = [{"p": p, "n": n, "line": "talk"} for p, n in
             [("P1", "收到听写文字"), ("P2", "收到开口通知"), ("P3", "喇叭出第一声"), ("P4", "喇叭最后一声")]]
    segs = [{"a": "P1", "b": "P2", "t": "服务器"}, {"a": "P2", "b": "P3", "t": "等声音"}, {"a": "P3", "b": "P4", "t": "边收边放"}]
    sc = stds.real_scenario({"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "回答这条"}}, None)
    for k in sc["segments"]:
        sc["segments"][k]["range"] = [0.1, 1.0]
    for l in sc["lines"]:
        l["range"], l["slow"] = [0.5, 2.0], 3.0
    spec = m.Spec.from_scenario(sc)
    cur = lambda i: {"P1": (0, 0), "P2": (500_000, 0), "P3": (900_000, 0), "P4": (4_000_000 + i * 300_000, 0)}  # 回答有长有短
    out = jdg.judge(sc, [{"n": i + 1, **m.compute(cur(i), spec)} for i in range(10)])
    seg = {g["k"]: g["verdict"] for g in out["segments"]}
    assert seg["P3-P4"] == jdg.ANSWER and seg["P1-P2"] == "正常"
    last = out["lines"][-1]
    assert last["verdict"] == jdg.ANSWER and last["combo"] == jdg.ANSWER_TXT
    labels = {x["line"]: x for x in out["labels"] if x["kind"] == "line"}
    assert set(labels) == {"到喇叭出第一声", "到喇叭最后一声"}  # 没问题的线也列（收在"另有 N 条不用改"里）
    assert labels["到喇叭最后一声"]["level"] == "可以不改" and labels["到喇叭最后一声"]["why"] == jdg.ANSWER_TXT


def test_steady_segment_while_playing_is_still_judged():
    # 亲测第 20 次："放完设备里剩下的声音"每轮 0.12～0.14 秒，跟回答长短无关，照常按范围判
    from fsw import standards as stds, judge as jdg
    steps = [{"p": p, "n": n, "line": "talk"} for p, n in
             [("P1", "收到听写文字"), ("P2", "喇叭出第一声"), ("P3", "服务器说完"), ("P4", "喇叭最后一声")]]
    segs = [{"a": "P1", "b": "P2", "t": "等声音"}, {"a": "P2", "b": "P3", "t": "边收边放"}, {"a": "P3", "b": "P4", "t": "放完剩下的"}]
    sc = stds.real_scenario({"steps": steps, "segs": segs, "fsegs": [], "ln": {"talk": "回答这条"}}, None)
    for k in sc["segments"]:
        sc["segments"][k]["range"] = [0.05, 0.2]
    spec = m.Spec.from_scenario(sc)
    rounds = []
    for i in range(10):
        play = 2.0 + i * 0.5
        cur = {"P1": (0, 0), "P2": (100_000, 0), "P3": (int((0.1 + play) * 1e6), 0), "P4": (int((0.23 + play) * 1e6), 0)}
        rounds.append({"n": i + 1, **m.compute(cur, spec)})
    seg = {g["k"]: g["verdict"] for g in jdg.judge(sc, rounds)["segments"]}
    assert seg["P2-P3"] == jdg.ANSWER and seg["P3-P4"] == "正常"


def test_retest_entry_goes_to_the_one_page_run_too():
    # 10-18 真机：「改完了，再测一次」还走老的一页页流程，单页测量只接在首页「开始」上
    from tests.test_boss_short import JS
    f = JS.split("async function retestFrom(")[1].split("\n}")[0].split("\nconst ")[0]
    assert "autoStart()" in f and "hStage=2" not in f


def test_no_range_yet_does_not_say_slower_than_it_should_be():
    # 10-18 真机：第一次测、体验可以，却写"比这套做法该有的慢，可能有隐患"：没有合理范围，没有可比的
    from fsw import judge as jdg
    assert jdg.combo("可以", False, "没有范围") == "体验可以；还没有合理范围，先只记用时"
