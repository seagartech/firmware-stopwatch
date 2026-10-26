"""起点一轮报好几次时，跟着起点的点（10-26 真测 run-20261026-081316：小智每停一下就判一次"说完"）：
- 识别：起点和第一个结果之间的点（静音回调、上传首包、上传末包）标成跟着起点记（规则 3），沿用的识别结果重排，不调大模型；
- 计时点工具：每报一次起点，这几个"只记第一次"的点重新记；第一个结果之后不再重新记；
- 收数据：取第一个结果之前的最后一次起点，这几段 10 轮都有数；
- 老的数据（没跟着起点记）这几段只有 1 轮有数：报告标"数据不足（1/10 轮）"，不给中位数当结论。"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsw import identify as idf, instrument as ins, judge as jdg, measure as meas, standards as stds

FX = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
RUN = FX / "run-cdd6fed"
cc = pytest.mark.skipif(not shutil.which("cc"), reason="这台电脑没有 C 编译器")
FRONT = ["P1-P2", "P2-P5", "P5-P7"]  # 上传末包越过识别结果：P5-P6、P6-P7 合成 P5-P7（judge.merge_overruns）


def reparsed():
    r = json.loads((FX / "identify-cdd6fed.json").read_text(encoding="utf-8"))
    return r, idf.parse(r["reply"], str(FX / "src"), chain=r["chain"], strict=True)


def page(follow=True):
    p = json.loads((RUN / "run.json").read_text(encoding="utf-8"))["page"]
    if follow:
        _, a = reparsed()
        fl = {s["id"] for s in a["steps"] if s.get("follow")}
        for s in p["steps"]:
            if s["p"] in fl:
                s["follow"] = True
    return p


def collect(lines, pg):
    sc = stds.real_scenario(pg, None)
    c = meas.Collector(meas.Spec.from_scenario(sc))
    out = []
    for l in lines:
        out += c.feed(l)
    out += c.finish()
    rounds = {}
    for r in out:
        if r.get("ok"):
            rounds[r["n"]] = {k: v for k, v in r.items() if k != "update"}
    return sc, list(rounds.values()), c


def test_saved_identify_reparse_marks_follow_points():
    r, a = reparsed()
    assert r["rules"] == 2 and a["ok"] and a["rules"] == idf.RULES_VER >= 3
    by = {s["id"]: s for s in a["steps"]}
    assert {k for k, s in by.items() if s.get("follow")} == {"P2", "P5", "P6"}
    assert by["P1"].get("line_of") == "start" and not by["P1"].get("follow")
    assert any("跟着起点记" in x for x in a["seg_repairs"])
    pg = idf.to_page(a, r.get("what") or "")
    assert {s["p"] for s in pg["steps"] if s.get("follow")} == {"P2", "P5", "P6"}


def test_saved_identify_reuse_resorts_without_model_calls(tmp_path):
    r = json.loads((FX / "identify-cdd6fed.json").read_text(encoding="utf-8"))
    proj = tmp_path / "proj"
    (proj / "identify").mkdir(parents=True)

    class NoLLM:
        def __getattr__(self, k):
            raise AssertionError("不该调大模型")
    kw = dict(project_dir=str(proj), fingerprint="fp", provider=r.get("provider") or "deepseek", reuse=True)
    f = idf._cache_file(str(proj), "fp", r.get("what") or "", kw["provider"]) if hasattr(idf, "_cache_file") else None
    if f is None:
        pytest.skip("缓存文件名的算法换了")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    got = idf.identify_cached(NoLLM(), str(FX / "src"), r.get("what") or "", **kw)
    assert got["reused"] and got["calls"] == 0 and got["rules"] == idf.RULES_VER
    assert {s["p"] for s in got["page"]["steps"] if s.get("follow")} == {"P2", "P5", "P6"}
    assert json.loads(f.read_text(encoding="utf-8"))["rules"] == idf.RULES_VER  # 重排过的存回去了


def test_old_log_front_segments_are_thin_not_fake_medians():
    sc, rounds, _ = collect((RUN / "raw.txt").read_text(encoding="utf-8").splitlines(), page(follow=True))
    assert len(rounds) == 10
    sc, rounds, _ = jdg.merge_overruns(sc, rounds)
    j = jdg.judge(sc, rounds)
    by = {g["k"]: g for g in j["segments"]}
    for k in FRONT:
        assert by[k]["verdict"] == jdg.THIN and by[k]["count"] == 1 and by[k]["of"] == 10 and by[k]["thin"]
    assert {c["k"] for c in j["checks"] if c["kind"] == "thin"} == set(FRONT)
    assert j.get("best_fix", {}).get("k") not in FRONT and j.get("biggest") not in FRONT
    # 总的那条线按一轮真正结束（回到聆听）叫
    assert [l["name"] for l in sc["lines"]][-1] == "全程：说话结束 → 回到聆听"  # 全程这条按要测的那件事起名（10-26）


# ---- 按真测日志的时间，模拟加上"跟着起点记"之后设备会打出什么 ----
EV = re.compile(r"FSW,(LATE,)?(P\d+|RESET),(-?\d+)")


def events():
    """真日志里每一行 → 设备上走到的那几句代码（按时间）。每次说完（P1）同一处接着走静音回调（P2）；
    聆听时每 60 毫秒发一包（上传首包 P5、上传末包 P6），一直到真日志里最后一包；首段出声后每 60 毫秒放一段（P10），到最后一段。"""
    ev, last_reset, play = [], 0, None
    for l in (RUN / "raw.txt").read_text(encoding="utf-8").splitlines():
        m = EV.search(l)
        if not m:
            continue
        late, p, t = m.group(1), m.group(2), int(m.group(3))
        if p == "RESET":
            ev.append((t, 0, "FSW_ROUND_RESET();"))
            last_reset = t
        elif late and p == "P6":
            for x in range(last_reset + 30_000, t, 60_000):
                ev.append((x, 1, "FSW_MARK_ONCE(5,0);FSW_KEEP_LAST(6);"))
            ev.append((t, 1, "FSW_MARK_ONCE(5,0);FSW_KEEP_LAST(6);"))
        elif late and p == "P10" and play is not None:
            for x in range(play, t, 60_000):
                ev.append((x, 1, "FSW_KEEP_LAST(10);"))
            ev.append((t, 1, "FSW_KEEP_LAST(10);"))
        elif late:
            continue
        elif p == "P1":
            ev.append((t, 2, "FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);"))
        elif p in ("P2", "P5"):
            continue  # 模拟里由 P1、上传那几包走到
        elif p == "P11":
            ev.append((t, 2, "FSW_MARK(11,0);"))
        else:
            ev.append((t, 2, f"FSW_MARK_ONCE({p[1:]},0);"))
            if p == "P9":
                play = t
    return sorted(ev)


def simulate(tmp_path, follow):
    tail = ["P10", "P11"]
    (tmp_path / "fw_stopwatch.h").write_text(ins.header_text(1, tail, follow), encoding="utf-8")
    (tmp_path / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n", encoding="utf-8")
    body = "".join(f"T={t}LL;{code}" for t, _, code in events())
    (tmp_path / "m.c").write_text('#include "fw_stopwatch.h"\nstatic int64_t T;int64_t esp_timer_get_time(void){return T;}\n'
                                  f"int main(void){{{body}return 0;}}\n", encoding="utf-8")
    subprocess.run(["cc", "-O0", "m.c", "-o", "m", "-I."], cwd=tmp_path, check=True)
    return subprocess.run(["./m"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.splitlines()


@cc
def test_follow_header_gives_all_rounds_front_segments(tmp_path):
    out = simulate(tmp_path, ["P2", "P5", "P6"])
    assert "FSW_FOLLOW_MASK 0x64UL" in (tmp_path / "fw_stopwatch.h").read_text(encoding="utf-8")
    sc, rounds, c = collect(out, page(follow=True))
    assert len(rounds) == 10
    old_sc, old_rounds, _ = collect((RUN / "raw.txt").read_text(encoding="utf-8").splitlines(), page(follow=False))
    sc, rounds, _ = jdg.merge_overruns(sc, rounds)
    j = jdg.judge(sc, rounds)
    by = {g["k"]: g for g in j["segments"]}
    for k in FRONT:
        assert by[k]["count"] == 10 and not by[k].get("thin"), k
        assert all(r["segs"][k] >= 0 for r in rounds)
    assert not [x for x in j["checks"] if x["kind"] == "thin"]
    # 起点、首段出声、回到聆听和真日志取的一样：线的终点没变
    for a, b in zip(sorted(rounds, key=lambda r: r["n"]), sorted(old_rounds, key=lambda r: r["n"])):
        assert a["ends"][1] == pytest.approx(b["ends"][1], abs=1e-6)
        assert a["ends"][-1] == pytest.approx(b["ends"][-1], abs=1e-6)
    # 静音回调就在说完同一处：0 秒；上传首包在说完之后一包之内
    assert all(r["segs"]["P1-P2"] == 0 for r in rounds)
    assert all(0 <= r["segs"]["P2-P5"] <= 0.06 for r in rounds)


@cc
def test_without_follow_header_same_events_leave_gaps(tmp_path):
    out = simulate(tmp_path, [])
    sc, rounds, _ = collect(out, page(follow=True))
    have = [r for r in rounds if r["segs"].get("P1-P2") is not None]
    assert len(have) < 8  # 老的插法：第一次说完就占了名额


@cc
def test_follow_stops_after_first_result(tmp_path):
    """第一个结果之后又报起点（比如回复放着时杂音）：跟着起点的点不再重新记，量的还是那次说完之后的。"""
    body = ("FSW_ROUND_RESET();FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);"
            "FSW_MARK_ONCE(7,0);FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);FSW_ROUND_RESET();"
            "FSW_MARK_ONCE(1,0);FSW_MARK_ONCE(2,0);")
    (tmp_path / "fw_stopwatch.h").write_text(ins.header_text(1, [], ["P2"]), encoding="utf-8")
    (tmp_path / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n", encoding="utf-8")
    (tmp_path / "m.c").write_text('#include "fw_stopwatch.h"\nint64_t esp_timer_get_time(void){static int64_t t=1000;return t+=10;}\n'
                                  f"int main(void){{{body}return 0;}}\n", encoding="utf-8")
    subprocess.run(["cc", "m.c", "-o", "m", "-I."], cwd=tmp_path, check=True)
    out = [l.split(",")[1] for l in subprocess.run(["./m"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.splitlines()]
    assert out == ["RESET", "P1", "P2", "P1", "P2", "P7", "P1", "RESET", "P1", "P2"]


def test_header_default_has_no_follow():
    h = ins.header_text(1, ["P10"])
    assert "#define FSW_FOLLOW_MASK 0UL" in h
    assert "FSW_FOLLOW_MASK 0x4UL" in ins.header_text(1, [], ["P2", "P1"])  # 起点本身不进


def test_parse_plan_follow_from_steps():
    steps = [{"p": "P1", "line": "start"}, {"p": "P2", "follow": True}, {"p": "P3"}]
    assert ins.follow_points(steps) == ["P2"]
    assert ins.follow_points([{"p": "P1"}, {"p": "P2"}], {"follow": ["P2"]}) == ["P2"]


def test_duplicate_reset_is_harmless():
    pg = page(follow=True)
    lines = (RUN / "raw.txt").read_text(encoding="utf-8").splitlines()
    dup = []
    for l in lines:
        dup.append(l)
        if l.startswith("FSW,RESET,"):
            us = int(l.split(",")[2])
            dup.append(f"FSW,RESET,{us + 2000},0")
    _, a, _ = collect(lines, pg)
    _, b, _ = collect(dup, pg)
    assert [r["ends"] for r in a] == [r["ends"] for r in b]


def test_tail_lines_are_flagged_late():
    pg = page(follow=True)
    lines = (RUN / "raw.txt").read_text(encoding="utf-8").splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith("FSW,RESET,"))
    us = int(lines[i].split(",")[2])
    lines = lines[:i + 1] + [f"FSW,TAIL,P11,{us + 1_500_000},0"] + lines[i + 1:]
    _, rounds, c = collect(lines, pg)
    assert len(rounds) == 10
    assert c.late and c.late[0]["p"] == "P11" and c.late[0]["how"] == "tail"
