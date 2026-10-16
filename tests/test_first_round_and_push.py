"""2026-10-16 20 轮真测发现的两个问题（决策 101、102），按通用工具改，不只针对小智：

1. 第 1 次漏收：码表刚开始读调试口时，设备那一轮已经开始了（没看到"开始"那一行），整轮被悄悄丢掉。
   改成：开始读的那一刻就当作一轮的开头。这一轮收齐了就算；只收到半轮（没有起点）就提示"这一次开头没收到，再做一次"，
   不算没收全，也不悄悄丢。之后照旧：有"开始"、没有起点的一轮（比如对话类喊唤醒词）不算、不提示。
2. 收好一轮后"叮"一声来得慢：网页在后台时浏览器会把定时查询拖慢（最慢一分钟一次）。
   改成：后台一收好就推给网页（服务器推送），网页不靠定时查询也能马上响。
"""

import json

from fastapi.testclient import TestClient

from fsw import measure as meas
from fsw import standards as stds
from fsw.web.app import create_app
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_standards import PAGE

SC = stds.real_scenario(PAGE, stds.find(PAGE["steps"]), None)
ROUND = [f"FSW,P{i},{3_000_000 + i * 100_000},0" for i in range(1, 10)]  # 一轮：P1（起点）到 P9 都有


def feed(lines):
    c = meas.Collector(meas.Spec.from_scenario(SC))
    out = []
    for l in lines:
        out += c.feed(l)
    return out


def test_first_round_counts_when_reading_starts_mid_listen():
    """开始读之前设备已经在听了：第 1 轮没有"开始"那一行，但起点和后面的点都收到了，照样算。"""
    out = feed(ROUND + ["FSW,RESET,9000000,0"])
    assert [r["ok"] for r in out] == [True] and out[0]["n"] == 1


def test_first_round_half_seen_is_reported_not_silently_dropped():
    out = feed(ROUND[4:] + ["FSW,RESET,9000000,0"])  # 只看到后半轮：起点已经过去了
    assert out == [{"half": True}]


def test_round_without_start_is_not_counted_but_reported():
    """有"开始"、后面大部分点都到了，就是没有起点：不算一轮，但报出来（决策 133）。
    以前悄悄不算（当它是喊唤醒词），2026-10-16 30 轮真测里有一次真问了问题、设备没记到起点，报告却写"没有丢掉的"。"""
    out = feed(["FSW,RESET,1000000,0"] + ROUND[1:] + ["FSW,RESET,9000000,0"])
    assert out == [{"nostart": True}]
    assert meas.collect(["FSW,RESET,1000000,0"] + ROUND[1:] + ["FSW,RESET,9000000,0"], meas.Spec.from_scenario(SC)) == []


def test_only_a_few_points_without_start_is_still_ignored():
    """只到了一两个点（比如进聆听时换个表情、又回去待机）：不是一次真的对话，照旧不算、不提示。"""
    out = feed(["FSW,RESET,1000000,0", ROUND[3], "FSW,RESET,9000000,0"])
    assert out == []


def test_resume_after_stop_also_treats_the_first_round_the_same_way():
    c = meas.Collector(meas.Spec.from_scenario(SC))
    c.finish()
    out = []
    for l in ROUND + ["FSW,RESET,9000000,0"]:
        out += c.feed(l)
    assert [r["ok"] for r in out] == [True]


def make(tmp_path, lines):
    return TestClient(create_app(data_dir=tmp_path / "d", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                 cc_status=lambda: {"found": True, "logged_in": True},
                                 port_finder=lambda: ["/dev/cu.usbmodem1101"], device=FakeSerialDevice(lines), wait_step=0.01))


def test_half_first_round_shows_a_hint_on_the_collect_page(tmp_path):
    c = make(tmp_path, ROUND[4:] + ["FSW,RESET,9000000,0"] + ROUND + ["FSW,RESET,19000000,0"] + ROUND + ["FSW,RESET,29000000,0"] + ROUND)
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "p")})
    j = wait(c, lambda j: j["status"] == "done" or j.get("half"))
    assert j.get("half") == 1 and "开头没收到" in j["half_hint"] and "再做一次" in j["half_hint"]
    j = wait(c, lambda j: j["status"] == "done")
    assert len([r for r in j["rounds"] if r["ok"]]) == 3 and not [r for r in j["rounds"] if not r["ok"]]


def test_collect_events_push_each_change(tmp_path):
    lines = []
    for k in range(3):
        lines += [f"FSW,RESET,{k * 10_000_000 + 1_000_000},0"] + [f"FSW,P{i},{k * 10_000_000 + 3_000_000 + i * 100_000},0" for i in range(1, 10)]
    lines += ["FSW,RESET,99000000,0"]
    c = make(tmp_path, lines)
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "p")})
    seen = []
    with c.stream("GET", "/api/collect/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                v = json.loads(line[6:])
                seen.append(len([x for x in v.get("rounds", []) if x["ok"]]))
                if v["status"] == "done":
                    break
    assert seen[-1] == 3 and seen == sorted(seen)


def test_page_listens_to_the_push_and_keeps_polling_as_fallback():
    from tests.test_boss_short import JS
    assert "new EventSource('/api/collect/events')" in JS
    assert "fetch('/api/collect')" in JS  # 推送断了就退回定时查询


# ---- 第二次亲手操作发现的（决策 111、112） ----
def test_point_recorded_before_the_start_belongs_to_before_this_round():
    """决策 111：比起点还早的点（比如上一轮剩下的一包声音在聆听时到了，"只记第一次"就记成了它），不是这一轮的；
    这一轮真正的那一次没记到，这轮不能算（不判吞字），写清楚原因，自动补一轮。"""
    stale = ["FSW,RESET,1000000,0", "FSW,P7,1200000,5"] + [f"FSW,P{i},{3_000_000 + i * 100_000},0" for i in range(1, 10) if i != 7]
    out = feed(stale + ["FSW,RESET,9000000,0"])
    assert len(out) == 1 and out[0]["ok"] is False and out[0].get("stale")
    assert out[0]["missing"]  # 写出是哪个点


def test_first_round_after_reconnect_missing_a_point_is_half_not_incomplete():
    """决策 112：接着测时重新打开调试口，设备可能正说到一半，有的点在断开那一下丢了：算"开头没收到"，不算"没收全"。"""
    part = [f"FSW,P{i},{3_000_000 + i * 100_000},0" for i in range(1, 10) if i != 7]  # 起点有，中间丢了一个
    out = feed(part + ["FSW,RESET,9000000,0"])
    assert out == [{"half": True}]


# ---- 第四次亲手操作（决策 123～126） ----
def test_point_recorded_several_times_uses_the_first_after_the_start():
    """决策 123："表情换上"插成了每次都记：进聆听时设备先换一次表情（在你说完之前），回答来了再换一次。
    这一轮的要用起点之后的第一次，不是最早那次；以前每轮都被当成"上一轮剩下的"，一直补测。"""
    lines = ["FSW,RESET,1000000,0", "FSW,P4,1000100,0"] + [f"FSW,P{i},{3_000_000 + i * 100_000},0" for i in range(1, 10)]
    out = feed(lines + ["FSW,RESET,9000000,0"])
    assert len(out) == 1 and out[0]["ok"], out


def test_point_only_recorded_before_the_start_is_still_stale():
    stale = ["FSW,RESET,1000000,0", "FSW,P7,1200000,5"] + [f"FSW,P{i},{3_000_000 + i * 100_000},0" for i in range(1, 10) if i != 7]
    assert feed(stale + ["FSW,RESET,9000000,0"])[0].get("stale")


def test_today_log_all_rounds_count_after_the_fix():
    """用 2026-10-16 17:55 那次的真日志：以前 1 轮绿、5 轮灰；现在每轮都算。"""
    import json as _j
    from pathlib import Path as _P
    f = _P.home() / "firmware-stopwatch-data/xiaozhi-899bf4/runs/20261016-175542"
    if not (f / "raw.log").exists():
        return
    run = _j.loads((f / "run.json").read_text(encoding="utf-8"))
    sc = stds.real_scenario(run["page"], stds.find(run["page"]["steps"]), run.get("values"))
    c = meas.Collector(meas.Spec.from_scenario(sc))
    out = []
    for l in (f / "raw.log").read_text(encoding="utf-8", errors="replace").splitlines():
        out += c.feed(l)
    assert out and all(r.get("ok") for r in out if not r.get("half")), [r for r in out if not r.get("ok")]
