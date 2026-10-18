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



def _talk(pairs):
    """要测的这条线上的步骤；找步骤时 AI 会给"第一下/最后一下感觉到的结果"标身份（felt）和怎么感觉到的（kind）。"""
    felt = {"喇叭出第一声": ("first", "声音"), "喇叭最后一声": ("last", "声音")}
    return [{"p": p, "n": n, "line": "talk", **({"felt": felt[n][0], "kind": felt[n][1]} if n in felt else {})} for p, n in pairs]

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


def test_experience_standard_follows_the_device_category_else_nielsen():
    # 10-18 用户定：AI 读完代码顺手标设备类别，对上体验标准库里的类别用该类标准，对不上用尼尔森三档
    from fsw import standards as stds
    sc = {"lines": [{"name": "回答这条", "segments": ["s"]}], "segments": {"s": {"from": "P1", "to": "P2", "name": "x"}},
          "point_names": {"P1": "起点", "P2": "终点"}, "point_kinds": {"P2": "声音"}, "experiences": [], "category": "语音对话"}
    e = stds.with_default_experience(sc)["experiences"]
    assert e[0]["line"] == "回答这条" and e[0]["fair"] == 2.0 and "T/CESA" in e[0]["source"] and e[0]["category"] == "语音对话"
    door = stds.with_default_experience({**sc, "category": "门禁"})["experiences"]
    assert door[0]["fair"] == 1.0 and "浙江" in door[0]["source"]
    other = stds.with_default_experience({**sc, "category": "其他"})["experiences"]
    assert other[0]["fair"] == 1.0 and "尼尔森" in other[0]["source"] and other[0]["category"] == "通用"
    assert stds.with_default_experience({**sc, "point_kinds": {}})["experiences"] == []  # 终点没标"感觉到的结果"（老的识别结果）不套


def test_start_later_than_the_person_skips_the_experience_standard():
    # 第 25 次：AI 把起点放在"收到服务器听写文字"，少算了上传和听写，拿"语音输入结束起算"的标准比会判轻
    from fsw import standards as stds
    from fsw.identify import PROMPT
    sc = {"lines": [{"name": "回答这条", "segments": ["s"]}], "segments": {"s": {"from": "P1", "to": "P2", "name": "x"}},
          "point_names": {"P1": "起点", "P2": "终点"}, "point_kinds": {"P2": "声音"}, "experiences": [], "category": "语音对话",
          "start_late": True, "start_note": "上传录音、服务器听写"}
    out = stds.with_default_experience(sc)
    assert out["experiences"] == [] and "上传录音、服务器听写" in out["exp_skip"] and "不拿体验标准比" in out["exp_skip"]
    page = {"steps": _talk([("P1", "收到听写"), ("P2", "喇叭出声")]), "segs": [{"a": "P1", "b": "P2", "t": "等"}], "fsegs": [],
            "ln": {"talk": "回答这条"}, "startLate": True, "startNote": "上传录音"}
    assert stds.real_scenario(page, None)["start_late"] is True
    assert '"late"' in PROMPT and "设备自己能察觉人做完" in PROMPT  # 有设备自己察觉的点时优先拿它当起点（通用说法，不点名任何设备）


def test_touch_category_uses_google_inp():
    # 第 25 次：触屏例子落到尼尔森 1 秒太松；按键/触摸后屏幕或灯反应用谷歌 INP 0.2/0.5 秒
    from fsw import standards as stds
    ref = stds.reference("按键或触摸后屏幕/灯反应")
    assert ref["fair"] == 0.5 and ref["good"] == 0.2 and "web.dev" in ref["source"]


def test_whole_chain_gets_a_first_sound_line_and_only_it_gets_the_reference():
    # 亲测第 11 次：全程的终点是"喇叭最后一声"，回答越长越久；1.5 秒参考值说的是"说完到听到回答开头"
    from fsw import standards as stds
    steps = _talk([("P1", "设备听出你说完"), ("P2", "收到开口通知"), ("P3", "喇叭出第一声"), ("P4", "喇叭最后一声")])
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
    steps = _talk([("P1", "收到听写文字"), ("P2", "收到开口通知"), ("P3", "喇叭出第一声"), ("P4", "喇叭最后一声")])
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
    steps = _talk([("P1", "收到听写文字"), ("P2", "收到开口通知"), ("P3", "喇叭出第一声"), ("P4", "喇叭最后一声")])
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
    steps = _talk([("P1", "收到听写文字"), ("P2", "喇叭出第一声"), ("P3", "服务器说完"), ("P4", "喇叭最后一声")])
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


def test_felt_roles_split_lines_for_any_device(tmp_path):
    # 通用：刷卡开门也一样，AI 标了"第一下感觉到的结果"（锁开始动）就多一条到那一步的线；不是声音不套对话参考值
    import json
    from fsw import identify as idf, standards as stds
    (tmp_path / "main").mkdir()
    (tmp_path / "main" / "door.c").write_text("\n".join(f"line {i}" for i in range(1, 60)), encoding="utf-8")
    step = lambda i, n, line, **k: {"id": f"P{i}", "name": n, "file": "main/door.c", "line": 10 * i, "line_of": "start" if i == 1 else "main",
                                    "sure": "高", **k}
    data = {"category": "智能门锁", "start": {"name": "读到卡"}, "main": {"name": "开门这条", "end": "门开到位"},
            "steps": [step(1, "读到卡号", 10), step(2, "校验通过", 20), step(3, "锁开始动", 30, felt="first", kind="动作"),
                      step(4, "门开到位", 40, felt="last", kind="动作")],
            "segments": [{"from": "P1", "to": "P2", "name": "校验"}, {"from": "P2", "to": "P3", "name": "驱动锁"},
                         {"from": "P3", "to": "P4", "name": "开门"}]}
    r = idf.parse("```json\n" + json.dumps(data, ensure_ascii=False) + "\n```", tmp_path)
    assert [s["felt"] for s in r["steps"]] == ["", "", "first", "last"]
    page = idf.to_page(r, "全程")
    assert page["category"] == "智能门锁" and "- 智能门锁：" in idf.build_prompt("全程")  # 类别从体验标准库来，写进找步骤的要求
    sc = stds.with_default_experience(stds.real_scenario(page, None))
    assert [l["name"] for l in sc["lines"]] == ["到锁开始动", "到门开到位"] and sc["lines"][0]["first"]
    assert [(e["line"], e["fair"]) for e in sc["experiences"]] == [("到锁开始动", 0.8)]


def test_stopwatch_ships_no_device_standard_only_the_generic_reference():
    # 10-18 用户：通用代码不迁就某一个固件。码表不再自带小智那份标准（范围、问题规则、要记的附加值都按它来）
    from fsw import standards as stds
    assert stds.load_all() == [] and stds.find([{"p": "P1", "n": "x", "path": "main/application.cc", "l": 1}]) is None
    assert stds.reference("语音对话")["fair"] == 2.0 and stds.reference("没见过的")["short"] == "尼尔森三档"
    assert all(c["source"].count("http") >= 1 for c in stds.categories())  # 每一类都写了出处链接


def test_side_line_that_branches_before_the_start_is_measured_on_its_own():
    # 第 26 次触屏例子：副线"按下变色"发生在起点"收到点击（松手）"之前，不能把整条主线拼在它前面
    from fsw import standards as stds
    steps = [{"p": "P0", "n": "按下", "line": "face"}, {"p": "P1", "n": "收到点击", "line": "start"},
             {"p": "P2", "n": "新画面首块", "line": "talk", "felt": "first", "kind": "画面"},
             {"p": "P3", "n": "画面传完", "line": "talk", "felt": "last", "kind": "画面"}, {"p": "P4", "n": "按下变色", "line": "face"}]
    page = {"steps": steps, "segs": [{"a": "P1", "b": "P2", "t": "画"}, {"a": "P2", "b": "P3", "t": "传"}],
            "fsegs": [{"a": "P0", "b": "P4", "t": "变色"}], "ln": {"talk": "换画面", "face": "按下变色"}}
    sc = stds.real_scenario(page, None)
    side = next(l for l in sc["lines"] if l["name"] == "按下变色")
    assert side["segments"] == ["P0-P4"]
    spec = m.Spec.from_scenario(sc)
    cur = {"P0": (0, 0), "P4": (30_000, 0), "P1": (100_000, 0), "P2": (250_000, 0), "P3": (400_000, 0)}
    names = [l["name"] for l in sc["lines"]]
    ends = dict(zip(names, m.compute(cur, spec)["ends"]))
    assert ends["按下变色"] == 0.03 and ends["到新画面首块"] == 0.15


def test_two_points_on_the_same_code_line_are_flagged(tmp_path):
    from fsw import identify
    (tmp_path / "a.c").write_text("x();\ny();\n")
    st = [{"id": "P1", "name": "按下变色", "file": "a.c", "line": 2, "where": "after"},
          {"id": "P2", "name": "新画面", "file": "a.c", "line": 2, "where": "after"},
          {"id": "P3", "name": "起点", "file": "a.c", "line": 1, "where": "after"}]
    r = identify.parse("```json\n" + json.dumps({"steps": st}) + "\n```", tmp_path)
    p2 = r["steps"][1]
    assert p2["sure"] == "低" and "按下变色" in p2["doubt"] and r["steps"][0]["sure"] != "低" and r["steps"][2]["sure"] != "低"


def test_act_text_keeps_only_what_the_person_does():
    js = open("fsw/web/static/app.js", encoding="utf-8").read()
    line = next(x for x in js.splitlines() if x.startswith("const actClean="))
    import subprocess
    out = subprocess.run(["node", "-e", line.split("  //")[0] + "console.log(['喊唤醒词，等提示音响后说一句话','刷一次卡'].map(actClean).join('|'))"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "喊唤醒词，说一句话|刷一次卡"
