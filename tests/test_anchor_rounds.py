"""10-26 真测（8781，20261026-132839）：一轮按起点分，不按设备清零分。清零（回到等待）之后还会到这一轮的点——
最后几段声音、真正放完、回到聆听——原来被算进下一轮，报告出了 -1.6～-2.3 秒的线；全程量到 TTS 停止，不是回复放完。"""

import json
from pathlib import Path

from fsw import judge as jdg
from fsw import measure as meas
from fsw import standards as stds

FX = Path(__file__).parent / "fixtures"


def _run132839():
    d = json.loads((FX / "run_132839_page.json").read_text(encoding="utf-8"))
    sc = stds.with_default_experience(stds.real_scenario(d["page"], stds.find(d["page"].get("steps") or []), d.get("values")))
    lines = (FX / "run_132839_raw.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    rounds = [r for r in meas.collect(lines, meas.Spec.from_scenario(sc)) if r["ok"]]
    sc2, rounds2, merged = jdg.merge_overruns(sc, rounds)
    return sc, sc2, rounds2, jdg.judge(sc2, rounds2, None)


def test_real_run_has_no_negative_line_or_segment_and_measures_to_reply_done():
    sc, sc2, rounds, out = _run132839()
    assert len(rounds) == 10                                   # 试一次那轮没有清零标记，也按起点分出来
    names = [l["name"] for l in sc["lines"]]
    assert "全程：说话结束 → 回复放完" in names                 # 全程按要测的那件事起名，量到回复放完
    for r in rounds:
        assert all(v is None or v >= 0 for v in r["ends"]), r
        assert all(v is None or v >= 0 for v in r["segs"].values()), r
    L = {l["name"]: l for l in out["lines"]}
    whole = L["全程：说话结束 → 回复放完"]
    assert 3.0 <= whole["med"] <= 3.2 and whole["count"] == 10 and not whole.get("bad")
    side = L["聆听恢复这条"]
    assert abs(side["med"] - 3.106) < 0.01 and side["count"] == 8   # 说话结束 → 恢复聆听，清零后到的回到聆听算回这一轮
    assert abs(L["到显示识别结果"]["med"] - 0.404) < 0.002
    assert not [c for c in out["checks"] if c["kind"] in ("bad", "bad_line")]
    # 尾部放完 / 整段播放两段并排、差不多的两行合成一段，量到每轮最晚的那个（末段输出、放空、协议结束）
    segs = sc["segments"]
    assert not ({"P8-P10", "P8-P9"} & set(segs)) and set(segs["P8-P11"]["to_any"]) == {"P10", "P11", "P9"}
    assert "P1-P11" not in segs                                 # 从起点到放完的记录段就是全程这条，不另列
    first = next(l for l in out["lines"] if l["name"] == "到显示识别结果")
    assert out["best_fix"]["line"] == first["name"]            # 判快慢的还是第一下感觉到的结果那条
    assert "lead" not in out                                    # 顺带那条比结果晚，不写"先给的反应早多少"


def _spec():
    sc = {"format": "fsw-scenario/1", "name": "t", "start": {"point": "P1", "name": "说完"},
          "segments": {"P1-P2": {"name": "到结果", "from": "P1", "to": "P2"}, "P2-P3": {"name": "放回复", "from": "P2", "to": "P3"},
                       "P2-P4": {"name": "回到聆听", "from": "P2", "to": "P4"}},
          "lines": [{"name": "聆听", "end": "聆听", "segments": ["P1-P2", "P2-P4"]},
                    {"name": "到结果", "end": "结果", "first": True, "segments": ["P1-P2"]},
                    {"name": "全程", "end": "放完", "segments": ["P1-P2", "P2-P3"]}],
          "experiences": [], "problems": [], "last_points": ["P3"], "optional": ["P4"]}
    return meas.Spec.from_scenario(sc)


def _log(tail_last=True):
    s = lambda p, t: f"FSW,{p},{int(t * 1e6)},0"
    out = ["FSW,RESET,1000000,0", s("P1", 2.0), s("P2", 2.5), s("P3", 3.0), s("P3", 3.8),   # 回复中途放空一次（3.0），最后一次 3.8
           "FSW,RESET,4000000,0", "FSW,LATE,P3,4300000,1", s("P4", 4.4),                    # 清零后才补打的最后一声、回到聆听：算第 1 轮
           s("P1", 6.0), s("P1", 7.0), s("P2", 7.4), s("P3", 8.0), "FSW,RESET,8500000,0"]  # 第 2 轮起点取结果之前的最后一次（7.0）
    return out + ([s("P4", 8.6)] if tail_last else [])


def _ends(rounds):
    return [[None if v is None else round(v, 3) for v in r["ends"]] for r in rounds if r["ok"]]


def test_points_after_reset_land_in_their_own_round_and_last_points_take_the_last():
    assert _ends(meas.collect(_log(), _spec())) == [[2.4, 0.5, 2.3], [1.6, 0.4, 1.0]]


def test_last_round_runs_to_end_of_data_or_leaves_the_missing_end_empty():
    assert _ends(meas.collect(_log(tail_last=False), _spec())) == [[2.4, 0.5, 2.3], [None, 0.4, 1.0]]


def test_old_rule_rounds_file_is_recomputed(tmp_path):
    (tmp_path / "raw.log").write_text("\n".join(_log()), encoding="utf-8")
    (tmp_path / meas.ROUNDS_FILE).write_text(json.dumps({"rounds": [{"n": 1, "segs": {}, "ends": [-1.9, 0.5, 2.0]}]}), encoding="utf-8")
    got = meas.run_rounds(tmp_path, _spec())
    assert _ends(got) == [[2.4, 0.5, 2.3], [1.6, 0.4, 1.0]]
    meas.save_rounds(tmp_path, got)
    assert json.loads((tmp_path / meas.ROUNDS_FILE).read_text(encoding="utf-8"))["rule"] == meas.ROUND_RULE


def test_negative_values_are_flagged_and_kept_out_of_conclusions():
    sc = {"segments": {"A": {"name": "甲", "from": "P1", "to": "P2", "owner": "固件", "side": "device"},
                       "B": {"name": "乙", "from": "P2", "to": "P3", "owner": "固件", "side": "device"},
                       "C": {"name": "丙", "from": "P1", "to": "P4", "owner": "固件", "side": "device"}},
          "lines": [{"name": "旁", "end": "旁", "segments": ["C"]}, {"name": "主", "end": "主", "segments": ["A", "B"]}],
          "experiences": [], "problems": []}
    rounds = [{"n": i + 1, "segs": {"A": 0.3, "B": 0.5 if i else -0.2, "C": -1.8}, "ends": [-1.8, 0.8]} for i in range(10)]
    out = jdg.judge(sc, rounds)
    seg = {g["k"]: g for g in out["segments"]}
    assert seg["B"]["verdict"] == jdg.BAD and seg["B"]["bad_rounds"] == [1]
    assert seg["C"]["verdict"] == jdg.BAD
    assert out["biggest"] == "A" and out["best_fix"]["k"] == "A"     # 有负数的段不当最大头、不当最值得优化
    line = {l["name"]: l for l in out["lines"]}
    assert line["旁"]["verdict"] == jdg.BAD and "line:旁" not in {x["k"] for x in out["labels"]}
    assert out["first"]["line"] == "主" and "lead" not in out
    kinds = [c["kind"] for c in out["checks"]]
    assert kinds.count("bad") == 2 and kinds.count("bad_line") == 1
    assert all("数据有问题" in c["why"] for c in out["checks"])


def test_plan_resets_at_protocol_end_and_flushes_the_measured_end_candidates():
    """插点和报告一致：清零放在一轮结束那一步（服务器说完、切回聆听），全程量到的几个终点里可能在它之后的（末段输出、放空）
    都在清零时补打（LATE），按起点分轮时算回这一轮。"""
    from fsw import instrument as ins
    page = json.loads((FX / "run_132839_page.json").read_text(encoding="utf-8"))["page"]
    steps = page["steps"]
    assert ins.round_end(steps)["p"] == page["roundEnd"] == "P9"
    tail = {s["p"] for s in ins.tail_points(steps, {"segs": page["segs"] + page.get("fsegs", [])})}
    sc = stds.real_scenario(page, None)
    later = set(sc["segments"]["P8-P11"]["to_any"]) - {page["roundEnd"]}
    assert later <= tail
