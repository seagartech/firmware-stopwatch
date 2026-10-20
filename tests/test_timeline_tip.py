"""一次的计时图：按中位数画（总长 = 这条线的中位数，各段按各自中位数的比例分）；色块、终点圆点鼠标移上去即时显示数字，
图例下写明颜色规则和怎么画的（2026-10-19 用户问"色块鼠标移入没有说明"、"标题 1.4 秒、图上 1.51 秒对不上"）。

用 node 跑 app.js 里的 timeline()，两组数据：门禁样子的和语音样子的，提示里只能有段名、线名和数字。
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
    names = ["sorted", "num", "med", "f", "fms", "htmlEsc", "niceT", "step", "segMed", "lineParts", "segTip", "timeline"]
    code = "\n".join(grab(n) for n in names) + f"\nconsole.log(JSON.stringify(timeline({json.dumps(D)})));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def door():
    segs = {"a": {"name": "读卡号", "v": [0.04, 0.08]}, "b": {"name": "校验<权限>", "v": [0.001, 0.001]},
            "c": {"name": "查名单", "v": [0.002, 0.002]}, "d": {"name": "亮灯响一声", "v": [0.3, 0.5]}}
    meds = {"a": 0.06, "b": 0.001, "c": 0.002, "d": 0.4}
    return {"n": 2, "S": {"start": "刷卡", "segs": segs}, "use": {"a": 1, "b": 1, "c": 1, "d": 1},
            "allSegs": [{"k": k, "med": meds[k]} for k in segs],
            "lines": [{"name": "反应线", "end": "亮灯", "col": "red", "soft": "pink", "med": 0.5, "p90": 0.58, "max": 0.58,
                       "segs": ["a", "b", "c", "d"]}]}


def voice():
    """两条线共用第一段：总长各按自己的中位数。"""
    segs = {"s": {"name": "听写", "v": [0.4, 0.5, 3.0]}, "f": {"name": "选表情", "v": [0.4, 0.4, 0.4]},
            "t": {"name": "合成声音", "v": [0.9, 1.0, 0.8]}}
    meds = {"s": 0.5, "f": 0.4, "t": 0.9}
    return {"n": 10, "S": {"start": "说完", "segs": segs}, "use": {"s": 2, "f": 1, "t": 1},
            "allSegs": [{"k": k, "med": meds[k]} for k in segs],
            "lines": [{"name": "线甲", "end": "换表情", "col": "green", "soft": "lg", "med": 0.83, "p90": 3.5, "max": 3.5, "segs": ["s", "f"]},
                      {"name": "线乙", "end": "出声", "col": "blue", "soft": "lb", "med": 1.45, "p90": 1.61, "max": 4.13, "segs": ["s", "t"]}]}


def test_every_block_and_end_dot_has_instant_tip_with_medians():
    out = svg(door())
    assert "<title>" not in out  # 不再靠浏览器自带的慢提示
    tips = re.findall(r'data-tip="([^"]*)"', out)
    assert len(tips) == 4  # 读卡号、合并块、亮灯响一声、终点圆点
    seg = next(t for t in tips if "读卡号" in t)
    assert "2 轮中位数 0.060 秒" in seg and "占「反应线」13%" in seg and "最快～最慢 0.040～0.080 秒" in seg  # 不到 0.1 秒写三位
    assert "本轮" not in out and "起止时刻" not in out                       # 不再画某一轮
    merged = next(t for t in tips if "合并显示" in t)
    assert "2 段合并显示" in merged and "查名单" in merged
    assert "&amp;lt;权限&amp;gt;" in merged  # 段名转义两次：属性里一次、提示框 innerHTML 一次
    dot = tips[-1]
    assert "反应线" in dot and "2 轮中位数 0.50 秒" in dot and "最慢 0.58 秒" in dot
    assert "亮灯 0.50 秒" in out                                             # 终点写的是中位数，和标题的"一般"一样


def test_line_length_is_its_median_and_shared_segment_split_by_ratio():
    out = svg(voice())
    assert "换表情 0.83 秒" in out and "出声 1.45 秒" in out
    assert "早 0.62 秒" in out                                               # 两条线中位数之差
    tips = re.findall(r'data-tip="([^"]*)"', out)
    shared = [t for t in tips if "听写" in t]
    assert len(shared) == 2 and all("（多条线共用）" in t and "10 轮中位数 0.50 秒" in t for t in shared)
    assert "占「线甲」56%" in shared[0] and "占「线乙」36%" in shared[1]        # 0.5/(0.5+0.4)、0.5/(0.5+0.9)
    assert "10 次里最慢 1.61 秒" in tips[-1]


def test_legend_states_how_it_is_drawn_and_color_rule_without_scene_words():
    out = svg(door())
    assert "颜色规则：每条线一种颜色；到达终点的最后一段为深色，之前各段为浅色" in out
    assert "总长是这条线的中位数（2 轮）；各段按各自中位数的比例分" in out
    assert "相邻的极短段合并为一块" in out
    assert "共用的段为灰色" not in out  # 没有共用段时不写这条
    for w in ("说话", "语音", "开口", "表情", "服务器"):
        assert w not in out
    assert "共用的段为灰色" in svg(voice())


def test_headline_writes_the_same_median_as_the_chart_end():
    """标题和图终点写同一个中位数：都是两位小数（10-19 用户：标题 1.4、图上 1.45 对不上）。"""
    D = {"S": {"startShort": "刷卡"}, "lines": [{"med": 0.0712, "phrase": "先亮灯"}, {"med": 0.9149, "phrase": "后开门"}]}
    names = ["f", "headline"]
    code = "\n".join(grab(n) for n in names) + f"\nconsole.log(JSON.stringify(headline({json.dumps(D, ensure_ascii=False)})));"
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
    assert out.startswith("刷卡 0.07 秒先亮灯") and "0.91 秒后开门" in out
    assert "med,1)" not in JS                                                # 线的中位数没有哪里还只写一位


def test_report_names_category_start_gap_and_best_fix_without_scene_words_in_code():
    assert "function bestFix(" in JS and "const startGap=" in JS
    assert "最值得优化的一段" in JS and "这个标准只有及格线" in JS



def test_best_fix_percentages_use_one_decimal():
    """「最值得优化的一段」两个百分比写法一样：都留一位小数（0.7%、1.0%）。"""
    D = {"J": {"best_fix": {"name": "解码", "med": 0.01, "line": "线", "share": 0.7, "device_share": 1.0}}, "lines": [{"name": "线", "end": "出声"}]}
    code = "\n".join(grab(n) for n in ["f", "fms", "htmlEsc", "bestFix"]) + f"\nconsole.log(JSON.stringify(bestFix({json.dumps(D, ensure_ascii=False)})));"
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
    assert "占「出声」的 0.7%" in out and "合计占 1.0%" in out
