"""码表自己记每段用时（决策 99）：每段记开始、结束时间，按 14 号文档的四笔账分类（码表耗时 / 被测设备 / 人配合 / 等人），
对照标准标"好 / 能忍 / 超了"，报告技术细节里给出"这次测试本身用了多久"。"""

import json
import time
from pathlib import Path

from fsw import timeline as tln
from fsw import measure as meas


def tl_first(tmp_path):
    """一次第一次测：找步骤、插计时点都调用了大模型。时间都是秒，从 0 开始。"""
    t = tln.Timeline(tmp_path / "timeline.json", clock=lambda: 0)
    ev = [("begin", "source", 0), ("end", "source", 60),
          ("begin", "identify", 60), ("end", "identify", 190, {"reused": False}),
          ("begin", "check", 190), ("end", "check", 250),
          ("begin", "insert", 250), ("end", "insert", 330),
          ("begin", "confirm", 330), ("end", "confirm", 345),
          ("begin", "build", 345), ("end", "build", 362),
          ("begin", "flash", 362), ("end", "flash", 400),
          ("begin", "try", 420), ("end", "try", 455),
          ("begin", "collect", 460), ("end", "collect", 700),
          ("begin", "advise", 700), ("end", "advise", 800),
          ("begin", "export", 820), ("end", "export", 821),
          ("begin", "restore", 830), ("end", "restore", 860)]
    for e in ev:
        kind, name, at = e[0], e[1], e[2]
        getattr(t, kind)(name, at=at, **(e[3] if len(e) > 3 else {}))
    t.wait("flash", 18, "等设备插上", at=380)  # 烧录里有 18 秒在等人插线
    t.wait("ready", 20, "等你说准备好了", at=400)
    t.count("try_said")
    t.count("clicks", 9)
    return t


DEVICE = {"speak": 60.0, "react": 36.0, "play": 116.0, "ok": 20, "bad": 1, "hints": 2}


def test_stages_are_saved_with_start_and_end(tmp_path):
    t = tl_first(tmp_path)
    d = json.loads((tmp_path / "timeline.json").read_text(encoding="utf-8"))
    assert d["stages"]["identify"] == {"start": 60, "end": 190, "reused": False}
    assert d["waits"][0]["why"] == "等设备插上"


def test_four_books(tmp_path):
    a = tln.account(tl_first(tmp_path).data, DEVICE)
    b = a["books"]
    # 码表：找步骤 130 + 插 80 + 编 17 + 烧 (38-18) + 写建议 100 + 导出 1 + 还原 30
    assert b["tool"] == 130 + 80 + 17 + 20 + 100 + 1 + 30
    assert b["device"] == 36 + 116  # 设备反应 + 放完回答
    assert b["wait"] == 18 + 20
    # 人：选源码 60 + 核对 60 + 确认 15 + 试一次 35 + 多轮实测里说话 60 和轮间空档（240 - 60 - 36 - 116 = 28）
    assert b["human"] == 60 + 60 + 15 + 35 + 60 + 28
    c = a["counts"]
    assert c["said"] == 1 + 21 and c["redo"] == 1 and c["false_hints"] == 2 and c["clicks"] == 9


def test_verdicts_against_the_14_standard(tmp_path):
    a = tln.account(tl_first(tmp_path).data, DEVICE)
    v = {s["key"]: s["verdict"] for s in a["stages"] if s.get("verdict")}
    assert v["identify"] == "能忍"  # 第一次：好 ≤120，能忍 ≤300
    assert v["insert"] == "好" and v["build"] == "好" and v["flash"] == "好"
    assert v["advise"] == "能忍"  # 好 ≤60，能忍 ≤180
    assert v["collect"] == "好"  # 每轮 240/21 ≈ 11 秒（好 ≤25）
    assert a["first"] is True and a["verdict"]["tool"] == "好"  # 码表耗时第一次好 ≤8 分钟
    assert a["verdict"]["human"] == "好"


def test_retest_uses_the_stricter_line(tmp_path):
    t = tl_first(tmp_path)
    t.begin("identify", at=60)
    t.end("identify", at=62, reused=True)  # 用上次结果
    a = tln.account(t.data, DEVICE)
    assert a["first"] is False
    v = {s["key"]: s["verdict"] for s in a["stages"] if s.get("verdict")}
    assert v["identify"] == "好" and v["insert"] == "超了"  # 再测：插计时点好 ≤5 秒；这次 80 秒


def test_missing_stage_is_not_made_up(tmp_path):
    t = tln.Timeline(tmp_path / "t.json", clock=lambda: 0)
    t.begin("build", at=0)
    t.end("build", at=20)
    a = tln.account(t.data, None)
    s = {x["key"]: x for x in a["stages"]}
    assert s["identify"]["secs"] is None and s["identify"]["note"] == "没记下"
    assert a["books"]["device"] is None  # 没有多轮实测的数据就不算


def test_round_times_split_into_speak_react_play():
    """每轮记三个设备时刻：开始聆听、你说完（起点）、最后一个计时点。放完回答 = 下一轮开始聆听 - 这一轮最后一个点；最后一轮放到哪不知道，不算。"""
    rs = tln.split_rounds([{"ok": True, "t": [1.0, 3.8, 4.4]}, {"ok": False}, {"ok": True, "t": [10.0, 12.0, 13.0]}])
    assert rs == {"speak": 4.8, "react": 1.6, "play": 5.6, "idle": 0, "ok": 2, "bad": 1}


def test_collector_records_the_three_device_times():
    from fsw import standards as stds
    from tests.test_standards import PAGE
    sc = stds.real_scenario(PAGE, stds.find(PAGE["steps"]), None)
    c = meas.Collector(meas.Spec.from_scenario(sc))
    out = []
    lines = ["FSW,RESET,1000000,0"] + [f"FSW,P{i},{3_000_000 + i * 100_000},0" for i in range(1, 10)]
    for l in lines:
        out += c.feed(l)
    assert out[0]["ok"] and out[0]["t"] == [1.0, 3.1, 3.9]


# ---- 网页后台真的记了 ----
from tests.test_fsw_api import setup, start_insert, wait, run_to_try  # noqa: E402,F401


def test_backend_records_each_stage(setup, tmp_path):  # noqa: F811
    c, r, dev, *_ = setup()
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    time.sleep(0.05)
    assert run_to_try(c)["status"] == "done"
    c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]})
    t = c.get("/api/timing").json()
    s = {x["key"]: x for x in t["stages"]}
    for k in ("insert", "confirm", "build", "flash", "try", "restore"):
        assert s[k]["secs"] is not None, k
    assert s["identify"]["note"] == "没记下"  # 这个测试没走找关键步骤：照实写，不编
    assert not any(w["why"].startswith("等你准备好") for w in t["waits"])  # 决策 107：不再停下等人点"准备好了"
    assert t["counts"]["said"] == 1 and t["counts"]["llm_calls"] == 1
    saved = json.loads((Path(r["project_dir"]) / "timeline.json").read_text(encoding="utf-8"))
    assert "build" in saved["stages"]


def test_tech_details_show_the_timing_box_and_manager_view_does_not(tmp_path):
    import shutil
    import subprocess
    from tests.test_boss_short import grab, JS
    if not shutil.which("node"):
        return
    a = tln.account(tl_first(tmp_path).data, DEVICE)
    code = "\n".join(grab(n) for n in ("mmssT", "VTAG", "timingBox")) + f"\nconsole.log(timingBox({json.dumps(a, ensure_ascii=False)}));"
    html = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    assert "这次测试本身用了多久" in html and "码表耗时" in html and "等人" in html
    assert "说了 22 句" in html and "补测 1 轮" in html and "c-warn\">能忍" in html
    assert "timingBox(" in grab("dev") and "timingBox(" not in grab("reportPage")  # 负责人版不加


def test_long_gap_between_rounds_is_waiting_not_playback():
    """2026-10-16 真测：第 19、20 轮之间隔了 424 秒（有一轮没算上，人在等）。放回答不会这么久：两轮之间超过 30 秒的部分算等人。"""
    rs = tln.split_rounds([{"ok": True, "t": [1.0, 3.0, 4.0]}, {"ok": True, "t": [9.0, 11.0, 12.0]}, {"ok": True, "t": [436.0, 438.0, 439.0]}])
    assert rs["play"] == 5.0 + 30.0 and rs["idle"] == 394.0
    a = tln.account({"stages": {"collect": {"start": 0, "end": 440}}, "waits": [], "counts": {}}, {**rs, "hints": 0})
    assert a["books"]["wait"] == 394.0 and a["books"]["device"] == 1 + 1 + 1 + 5 + 30  # 设备反应每轮 1 秒，放回答 5 + 30
    s = next(x for x in a["stages"] if x["key"] == "collect")
    assert s["secs"] == 440 - 394 and s["per_round"] == round(46 / 3, 1)
