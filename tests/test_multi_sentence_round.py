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



