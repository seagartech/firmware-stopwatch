"""试一次在喊唤醒词那一轮就试通了（这一轮没有起点）：多轮实测跳过这一轮，但它的结束标记在试一次时已经过去，
唤醒后说的第一句不能被一起跳过（10-19 真测 runs/20261020-002640：第一句整句被扔、格子不变绿，用户又说了一遍）。"""

import json
from pathlib import Path

import pytest

from fsw import measure as meas
from fsw import standards as std

SC = {"format": "fsw-scenario/1", "name": "对话", "start": {"point": "P1", "name": "说完"},
      "segments": {"P1-P2": {"name": "听写", "from": "P1", "to": "P2"}, "P2-P3": {"name": "想回答", "from": "P2", "to": "P3"},
                   "P3-P4": {"name": "出声", "from": "P3", "to": "P4"}},
      "lines": [{"name": "线", "end": "出声", "segments": ["P1-P2", "P2-P3", "P3-P4"]}], "experiences": [], "problems": []}


def ask(b):
    return [f"FSW,P1,{b},0", f"FSW,P2,{b + 400_000},0", f"FSW,P3,{b + 900_000},0", f"FSW,P4,{b + 1_000_000},0"]


def run(lines, skip=True):
    c = meas.Collector(meas.Spec.from_scenario(SC))
    if skip:
        c.skip_round()
    out = []
    for l in lines:
        out += c.feed(l)
    return [r for r in out + c.finish() if r.get("ok")]


def test_first_question_right_after_wake_round_counts():
    got = run(ask(20_000_000) + ["FSW,RESET,22000000,0"] + ask(25_000_000))   # 第一句前面没有结束标记
    assert [round(r["segs"]["P1-P2"], 3) for r in got] == [0.4, 0.4] and len(got) == 2


def test_wake_round_alone_is_not_a_round():
    assert run(["FSW,P2,1000000,0", "FSW,P3,1500000,0", "FSW,P4,1600000,0", "FSW,RESET,2000000,0"]) == []


def test_start_without_most_of_the_round_does_not_begin_one():
    assert run(["FSW,P1,1000000,0", "FSW,RESET,2000000,0"]) == []          # 只有起点（比如杂音）：不当新的一轮


REAL = Path.home() / "firmware-stopwatch-data" / "xiaozhi-899bf4" / "runs" / "20261020-002640"  # 只在用户那台 Mac 上有


@pytest.mark.skipif(not REAL.exists(), reason="这台电脑上没有 10-19 那次真测的原始日志")
def test_real_log_first_sentence_after_wake_is_round_1():
    m = json.loads((REAL / "run.json").read_text(encoding="utf-8"))
    sc = std.real_scenario(m["page"], std.find(m["page"].get("steps") or []))
    c = meas.Collector(meas.Spec.from_scenario(sc))
    c.skip_round()                                                       # 和那次一样：试一次带过来的那一轮没有起点
    out = []
    for l in (REAL / "raw.log").read_text(encoding="utf-8").splitlines():
        out += c.feed(l)
    got = [r for r in meas.merge_updates(out) if r.get("ok")]
    assert len(got) == 4                                                 # 修之前这份日志实测只算了 3 轮
    assert round(got[0]["segs"]["P1-P2"], 3) == 0.399                    # 第 1 轮就是 53.5 秒那一句（53.496 → 53.895）
    assert [r["t"][1] for r in got[1:4]] == [60.746, 66.436, 71.696]    # 后面几轮和实测时一样
