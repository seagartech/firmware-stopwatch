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


def test_wake_word_round_after_a_start_is_still_ignored():
    """有"开始"、后面有点但没有起点（比如对话类喊唤醒词，设备回一句"你好呀"）：照旧不算、不提示。"""
    out = feed(["FSW,RESET,1000000,0"] + ROUND[1:] + ["FSW,RESET,9000000,0"])
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
