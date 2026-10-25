"""10-25 晚 8767 全流程（tests/fixtures/fullflow_multi_sentence）查出的三件事：

1. 起点之前就一直逐帧在跑的两步（音频帧交编码、Opus 包入发送队列）各取起点之后第一次，不是同一帧，那一段量成 -0.15 秒：
   识别时码表去掉这种步骤上的点，前后两点直接连成一段。
2. "到回到聆听"把从首次出声分出去的三段都加起来，多算 0.19 秒：线的用时只算通到终点的那一串。
3. 回复分 3 句播，末段输出、播放队列放空一轮报了好几次，只取了第一句的：取这一轮最后那次；放空改成每次都记。
识别重放用存下的大模型回答，不调用大模型。
"""

import json
from pathlib import Path

from fsw import identify as idf
from fsw import measure as meas
from fsw import standards as stds
from fsw import structure

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"
CARRIED = 22  # 试一次交给多轮实测的那几行（到 LATE,P6 为止）
TRUE_TOTAL = 12.950772  # 说话结束 10.302180 → 回到聆听 23.252952


def saved():
    return json.loads((HERE / "identify.json").read_text(encoding="utf-8"))


def reparse():
    d = saved()
    return idf.parse(d["reply"], str(SRC), chain=d["chain"], strict=True)


def scenario(page):
    return stds.real_scenario(page, stds.find(page.get("steps") or []))


def replay(sc, log, carried):
    c = meas.Collector(meas.Spec.from_scenario(sc))
    out = []
    for l in log[:carried]:
        out += c.feed(l)
    out += c.close_carried(ended=True)
    for l in log[carried:]:
        out += c.feed(l)
    out += c.finish()
    return [r for r in meas.merge_updates(out) if r.get("ok")]


def old_page():
    return json.loads((HERE / "run.json").read_text(encoding="utf-8"))["page"]


def log(name="raw.txt"):
    return (HERE / name).read_text(encoding="utf-8").splitlines()


# ---- 1. 起点之前就在跑的流水线中间那几步 ----

def test_identify_drops_points_on_steps_streaming_before_the_start():
    r = reparse()
    assert r["ok"]
    steps = {s["id"]: s for s in r["steps"]}
    assert not any(s.get("chain_step") in (3, 4) for s in r["steps"])  # 逐帧切帧交编码、编码入队：起点前就一直在跑
    pairs = [(g["from"], g["to"]) for g in r["segments"]]
    assert ("P2", "P5") in pairs and not any(a in ("P3", "P4") or b in ("P3", "P4") for a, b in pairs)
    assert next(g for g in r["segments"] if (g["from"], g["to"]) == ("P2", "P5"))["name"] == "说完事件回调到上传首包"
    assert any("音频帧交编码" in x and "同一份数据" in x for x in r["seg_repairs"])
    # 回复那边逐包的几步（起点之后才开始）照旧在
    assert {"P10", "P11", "P12", "P13", "P14"} <= set(steps)
    assert structure.check_order(structure.Project(str(SRC)), r["steps"], r["segments"]) == []


def test_steps_before_first_role_that_run_once_are_kept():
    chain = [{"n": 1, "role": []}, {"n": 2, "repeat": "once", "role": []}, {"n": 3, "repeat": "repeat", "role": []},
             {"n": 4, "repeat": "repeat", "role": ["上传首包"]}, {"n": 5, "repeat": "repeat", "role": []}]
    steps = [{"id": f"P{n}", "name": f"s{n}", "chain_step": n, "line_of": "start" if n == 1 else "main"} for n in range(1, 6)]
    out, notes = idf.drop_stream_points(steps, chain)
    assert [s["id"] for s in out] == ["P1", "P2", "P4", "P5"]  # 只去第 3 步：在起点和第一个标了角色的步骤之间、反复执行、没角色
    assert len(notes) == 1
    side = [dict(steps[2], line_of="side")]
    assert idf.drop_stream_points(side, chain)[0] == side  # 顺带那条线不管


def test_reused_identify_result_is_reordered_by_current_rules_without_model(tmp_path):
    d = saved()
    old = {"ok": True, "strict": True, "reply": d["reply"], "chain": d["chain"], "what": d["what"],
           "steps": [{"id": "P3", "name": "旧的", "file": "x", "line": 1, "line_of": "main", "once": "first"}], "segments": []}
    f = idf._cache_file(tmp_path, "fp", d["what"], "p")
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    class NoModel:
        def investigate(self, *a, **k):
            raise AssertionError("不该调用大模型")

    r = idf.identify_cached(NoModel(), str(SRC), d["what"], tmp_path, "fp", "p")
    assert r["reused"] and r["calls"] == 0
    ids = [s["p"] for s in r["page"]["steps"]]
    assert "P3" not in ids and "P4" not in ids and "P5" in ids


# ---- 2. 线的用时不把分出去的几段加起来 ----

def test_line_total_is_start_to_end_not_sum_of_branches():
    sc = scenario(old_page())
    (r,) = replay(sc, log(), CARRIED)
    assert r["ends"][1] == TRUE_TOTAL  # 原来 13.141431：首次出声分出去的三段都加了
    assert r["ends"][0] == 1.214504


def test_line_total_still_counts_when_a_side_branch_point_is_missing():
    spec = meas.Spec(start="P1", segments={"a": ["P1", "P2"], "b": ["P2", "P3"], "c": ["P2", "P4"]},
                     lines=[{"name": "全程", "segs": [["P1", "P2"], ["P2", "P3"], ["P2", "P4"]]}])
    cur = {"P1": (1_000_000, 0), "P2": (2_000_000, 0), "P4": (5_000_000, 0)}  # 分出去的 P3 这一轮没有
    assert meas.compute(cur, spec)["ends"] == [4.0]
    cur["P3"] = (3_000_000, 0)
    assert meas.compute(cur, spec)["ends"] == [4.0]  # 不是 1 + 1 + 3


# ---- 3. 回复分几句播：取整段回复 ----

def test_last_output_takes_the_last_sentence_of_the_reply():
    sc = scenario(old_page())
    (r,) = replay(sc, log(), CARRIED)
    assert r["segs"]["P13-P14"] == round(23.122210 - 11.516684, 6)  # 原来 0.065368：只到第一句
    assert meas.suspects(r, sc) == [{"segment": "P3-P4", "name": "Opus 编码", "seconds": -0.153445,
                                     "why": "这段终点比起点先记到，两个计时点的先后对不上"}]  # 老的识别结果：重新识别后才没有（见下）


def test_identify_marks_queue_empty_every_time_and_measure_takes_the_last():
    r = reparse()
    p15 = next(s for s in r["steps"] if s["id"] == "P15")
    assert p15["once"] == "every"
    assert not any(c["kind"] == "repeat" for c in p15.get("chk") or [])  # 码表有意每次都记：不提醒"考虑只记一次"
    page = idf.to_page(r, saved()["what"])
    assert next(s for s in page["steps"] if s["p"] == "P15").get("whole") is True
    sc = scenario(page)
    assert "P15" in sc["last_points"] and "P14" in sc["last_points"]
    (rd,) = replay(sc, log("raw-after.txt"), 20)
    assert rd["segs"]["P13-P15"] == round(23.182210 - 11.516684, 6)  # 整段回复放完，不是第一句（原来 0.125291）
    assert rd["segs"]["P13-P14"] == round(23.122210 - 11.516684, 6)
    assert rd["segs"]["P2-P5"] == round(10.339111 - 10.302389, 6)
    assert all(v is None or v >= 0 for v in rd["segs"].values())
    assert meas.suspects(rd, sc) == []
    assert rd["ends"] == [1.214504, TRUE_TOTAL]


def test_insert_prompt_asks_to_record_queue_empty_every_time():
    from fsw import instrument
    page = idf.to_page(reparse(), saved()["what"])
    txt = instrument.build_prompt(page["steps"], "main/fw_stopwatch.h")
    line = next(l for l in txt.splitlines() if l.startswith("- P15"))
    assert "每次都记" in line
