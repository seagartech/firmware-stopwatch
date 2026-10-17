"""合理范围清单（减重版）的网页后台：测满 10 轮自动建、看、改；没测完、没清单、文件写坏了都说清楚。"""

from fsw import rangelist as rl
from tests.test_report_api import app, measured  # noqa: F401  （测试夹具）


def test_list_api_before_and_after_measuring(app):  # noqa: F811
    c, _, _, tmp_path = app
    assert c.get("/api/rangelist").status_code == 409                       # 还没测
    measured(c, tmp_path)
    c.get("/api/report")                                                     # 打开报告时按实测建好
    j = c.get("/api/rangelist").json()
    assert j["path"].endswith(rl.FILENAME) and j["list"]["segments"] and j["list"]["lines"]
    seg = next(iter(j["list"]["segments"]))
    bad = c.post("/api/rangelist/edit", json={"kind": "segment", "key": seg, "range": [0.5, 0.2], "slow": 1})
    assert bad.status_code == 400 and "写反" in bad.json()["detail"]
    ok = c.post("/api/rangelist/edit", json={"kind": "segment", "key": seg, "range": [0.1, 0.2], "slow": 0.3, "note": "厂家给的"}).json()
    assert ok["list"]["segments"][seg]["note"] == "厂家给的"
    line = j["list"]["lines"][-1]["name"]
    r = c.post("/api/rangelist/edit", json={"kind": "line", "key": line, "range": None, "slow": None, "fair": 1.2}).json()
    l = next(x for x in r["list"]["lines"] if x["name"] == line)
    assert l["fair"] == 1.2 and l["source"] == "用户设定" and l["range"] is None
    rep = c.get("/api/report").json()
    assert rep["lines"][-1]["exp_std"]["fair"] == 1.2                       # 报告马上按新的判


def test_broken_list_is_reported_not_ignored(app):  # noqa: F811
    c, _, _, tmp_path = app
    measured(c, tmp_path)
    c.get("/api/report")
    p = rl.path_of(tmp_path / "data" / "xiaozhi-1")
    p.write_text(p.read_text(encoding="utf-8").replace("| 测量线 |", "| 线 |", 1), encoding="utf-8")
    j = c.get("/api/rangelist").json()
    assert j["broken"] and j["errors"][0]["line"] > 0
    r = c.get("/api/report").json()
    assert r["scenario"]["range_list"]["broken"] and r["scenario"]["standard"]   # 写坏了：这次按码表自带的范围判，不悄悄忽略
    assert c.post("/api/rangelist/edit", json={"kind": "segment", "key": "x"}).status_code == 409


def test_report_table_shows_slow_threshold_and_each_lines_experience():
    """报告"分段用时"表：有慢阈值一栏（判了"超过慢阈值"得看得到阈值）；每条线按自己的体验标准标"体验差"，不只主线。"""
    from tests.test_boss_short import JS
    assert '<th class="r">合理</th><th class="r">慢阈值</th><th>判断</th>' in JS
    rows = next(x for x in JS.splitlines() if "const lineRows=" in x)
    assert "D.J.lines.find(x=>x.name===l.name)" in rows and "slowTxt(l.slow)" in rows
    assert "slowTxt(s.slow)" in next(x for x in JS.splitlines() if "const segRows=" in x)
