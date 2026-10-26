"""10-25 真机全流程第 1 轮："整段回复播放 -5.72 秒（先后反了）"。

日志只保留那次的计时事件（tests/fixtures/fullflow_wake_round）：唤醒提示音走了和回复同一句输出，起点之前打出了
"首段输出 / 末段输出 / 放空"三点；"记最后一次"的点原来放宽到起点前半秒，提示音的末段输出被当成这一轮回复的。
码表应当：一段的终点只认这一段起头那一步之后的那次；拿不到就当这一轮缺这个点，不拿上一段声音顶替。
"""

import json
from pathlib import Path

from fsw import measure as meas
from fsw import standards as stds

HERE = Path(__file__).parent / "fixtures" / "fullflow_wake_round"
CARRIED = 13  # 试一次交给多轮实测的那几行（到 LATE,P3 为止）


def scenario():
    d = json.loads((HERE / "run.json").read_text(encoding="utf-8"))
    page = d["page"]
    return stds.real_scenario(page, stds.find(page.get("steps") or []), d.get("values"))


def lines():
    return (HERE / "raw.txt").read_text(encoding="utf-8").splitlines()


def replay(log, carried=CARRIED, ended=False):
    """和网页一样：试一次带过来的那几行先喂、马上交出（close_carried），后面的接着喂。"""
    sc = scenario()
    c = meas.Collector(meas.Spec.from_scenario(sc))
    out = []
    for l in log[:carried]:
        out += c.feed(l)
    out += c.close_carried(ended=ended)
    for l in log[carried:]:
        out += c.feed(l)
    out += c.finish()
    return sc, meas.merge_updates(out)


def test_saved_round_no_longer_takes_the_beep_as_the_reply_end():
    # 试一次读到结束标记才交出（ended）：这一轮回复的末段输出那行原日志里没有，空着，不拿提示音那次（原来 -5.718244）
    sc, out = replay(lines(), ended=True)
    first = next(r for r in out if r.get("ok"))
    assert first["segs"]["P7-P8"] is None
    assert first["segs"]["P5-P7"] == 0.949068  # 首段输出是回复真正出声那次（34.975542），不是提示音（29.251947）
    assert meas.suspects(first, sc) == []
    last = set(sc.get("last_points") or [])
    assert all(v is None or v >= 0 for k, v in first["segs"].items() if sc["segments"][k]["from"] not in last)


def test_saved_log_as_it_was_carried_gives_no_negative_round():
    # 原来的交法（试一次读到 LATE,P3 就停、没读到结束标记）：下一次结束标记时这一轮不再带着 -5.72 秒交出
    sc, out = replay(lines() + ["FSW,RESET,49000000,0"])
    assert not any(r.get("ok") and (r["segs"].get("P7-P8") or 0) < 0 for r in out)
    assert all(meas.suspects(r, sc) == [] for r in out if r.get("ok"))


def test_reply_end_counts_when_it_comes_after_the_reply_start():
    # 修好的固件：起点之前的"记最后一次"不打印；这一轮结束时补打回复真正的末段输出，试一次读到结束标记才停
    log = [l for l in lines()[:CARRIED] if not l.startswith(("FSW,P8,", "FSW,P9,"))] + ["FSW,LATE,P8,36900000,40"]
    log += ["FSW,P9,37100000,0"]
    sc, out = replay(log, carried=len(log), ended=True)
    first = next(r for r in out if r.get("ok"))
    assert first["segs"]["P7-P8"] == round(36.9 - 34.975542, 6)
    assert meas.suspects(first, sc) == []


def test_keep_last_point_before_its_segment_start_is_missing_not_negative():
    sc = scenario()
    c = meas.Collector(meas.Spec.from_scenario(sc))
    for l in ["FSW,RESET,1000000,0", "FSW,P8,1700000,1",  # 起点前 0.3 秒的提示音末段：在原来的半秒放宽里
              "FSW,P1,2000000,0", "FSW,P2,2010000,0", "FSW,P4,3000000,0", "FSW,P5,3100000,0",
              "FSW,LATE,P3,2900000,40", "FSW,P7,3500000,0", "FSW,P10,6000000,0", "FSW,P9,6100000,0"]:
        c.feed(l)
    assert c._after("P8") == []
    c.feed("FSW,LATE,P8,5900000,30")
    assert [o[0] for o in c._after("P8")] == [5900000]


def test_start_is_the_last_one_before_the_first_felt_result():
    # 唤醒词说完判一次"说完"，真正说完又判一次：起点取收到识别文字之前的最后一次
    sc = scenario()
    c = meas.Collector(meas.Spec.from_scenario(sc))
    out = []
    for l in ["FSW,RESET,1000000,0", "FSW,P1,2000000,0", "FSW,P2,2010000,0", "FSW,P1,5000000,0",
              "FSW,P4,5600000,0", "FSW,P5,5700000,0", "FSW,LATE,P3,5550000,90", "FSW,P7,6000000,0",
              "FSW,P10,8000000,0", "FSW,LATE,P8,7900000,20", "FSW,P9,8100000,0", "FSW,RESET,8200000,0"]:
        out += c.feed(l)
    out += c.finish()
    first = next(r for r in meas.merge_updates(out) if r.get("ok"))
    assert first["t"][1] == 5.0
    k = next(i for i, l in enumerate(sc["lines"]) if l.get("first"))  # 主指标那条线（10-26 起次要的线排在它前面）
    assert first["ends"][k] == 0.6  # 说完到收到识别文字（不是从唤醒词说完算的 3.6 秒）
