"""一次的计时图：色块、终点圆点鼠标移上去即时显示数字，图例下写明颜色规则（2026-10-19 用户问"色块鼠标移入没有说明"）。

用 node 跑 app.js 里的 timeline()，两组数据：语音样子的和门禁样子的，提示里只能有段名、线名和数字。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")


def grab(name):
    m = re.search(rf"^(const {name}=.*|function {name}\(.*)$", JS, re.M)
    assert m, f"app.js 里没有 {name}"
    if m.group(0).startswith("const"):
        return m.group(0)
    out, depth = [], 0
    for line in JS[m.start():].splitlines():
        out.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            break
    return "\n".join(out)


def svg(D):
    names = ["sorted", "num", "med", "f", "htmlEsc", "niceT", "step", "segTip", "timeline"]
    code = "\n".join(grab(n) for n in names) + f"\nconsole.log(JSON.stringify(timeline({json.dumps(D)})));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def door():
    segs = {"a": {"name": "读卡号", "v": [0.06, 0.05]}, "b": {"name": "校验<权限>", "v": [0.001, 0.001]},
            "c": {"name": "查名单", "v": [0.002, 0.002]}, "d": {"name": "亮灯响一声", "v": [0.4, 0.5]}}
    return {"n": 2, "S": {"start": "刷卡", "segs": segs}, "use": {"a": 1, "b": 1, "c": 1, "d": 1},
            "rep": {"n": 1, "ends": [0.463]}, "allSegs": [{"k": k, "med": sum(s["v"]) / 2} for k, s in segs.items()],
            "lines": [{"name": "反应线", "end": "亮灯", "col": "red", "soft": "pink", "med": 0.51, "segs": ["a", "b", "c", "d"]}]}


def test_every_block_and_end_dot_has_instant_tip():
    out = svg(door())
    assert "<title>" not in out  # 不再靠浏览器自带的慢提示
    tips = re.findall(r'data-tip="([^"]*)"', out)
    assert len(tips) == 4  # 读卡号、合并块、亮灯响一声、终点圆点
    seg = next(t for t in tips if "读卡号" in t)
    assert "本轮耗时 0.06 秒" in seg and "占「反应线」13%" in seg and "起止时刻 0.00 → 0.06 秒" in seg and "2 轮中位数 0.06 秒" in seg
    merged = next(t for t in tips if "合并显示" in t)
    assert "2 段合并显示" in merged and "查名单" in merged
    assert "&amp;lt;权限&amp;gt;" in merged  # 段名转义两次：属性里一次、提示框 innerHTML 一次
    dot = tips[-1]
    assert "反应线" in dot and "本轮总耗时 0.46 秒" in dot and "2 轮中位数 0.51 秒" in dot


def test_legend_states_color_rule_without_scene_words():
    out = svg(door())
    assert "颜色规则：每条线一种颜色；到达终点的最后一段为深色，之前各段为浅色" in out
    assert "相邻的极短段合并为一块" in out
    assert "共用的段为灰色" not in out  # 没有共用段时不写这条
    for w in ("说话", "语音", "开口", "表情", "服务器"):
        assert w not in out
