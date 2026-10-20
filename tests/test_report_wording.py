"""检查点 3 复测留下的两个小问题（报告页的说法）：

10. 技术细节"分段用时"的范围写成负数（被吞那一轮第一段声音比进入说话状态还早到）→ 写"这一轮先后反了"。
11. 一句话结论下面那行还写着已经合并进吞字那条的"偶尔特别慢" → 跟要你拍板一样，用合并后的说法。

用 node 跑 app.js 里的几个纯函数（数据取自 22:59 复测的第 2 轮）。
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


def run(names, expr):
    code = "\n".join(grab(n) for n in names) + f"\nconsole.log(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_negative_segment_range_says_order_reversed():
    v, ns = [1.39, -4.31, 1.57], [1, 2, 3]
    txt = run(["f", "fms", "rangeTxt"], f"rangeTxt({v},{ns})")
    assert "-" not in txt.replace("～", "")
    assert txt.startswith("1.39～1.57") and "第 2 轮先后反了" in txt


def test_normal_segment_range_unchanged():
    assert run(["f", "fms", "rangeTxt"], "rangeTxt([0.5,0.9,0.7],[1,2,3])") == "0.50～0.90"


SEGS = [{"k": "s4", "name": "等服务器的第一段声音传过来"}, {"k": "s5", "name": "设备解码声音，送进喇叭"}]
J = {"labels": [
    {"kind": "problem", "k": "problem:sw", "name": "回答开头被吞", "rounds": [2], "level": "必须改"},
    {"kind": "spiky", "k": "spiky:s5", "segment": "s5", "rounds": [2], "with": "problem:sw"},
], "lines": []}


def subline_of(j):
    D = {"lines": [{"name": "开口这条", "end": "设备出声", "med": 2.6}], "allSegs": SEGS, "rounds": [{"n": 1}, {"n": 2}, {"n": 3}], "main": {"name": "开口这条"}}
    return run(["f", "roundList", "noExp", "withJudge", "subline"], f"subline(withJudge({json.dumps(D)},{json.dumps(j)}))")


def test_subline_keeps_only_the_judgment_sentence():
    """10-20 用户定：标题下的副标题只留一句判断；出错的轮次、偶发慢各有自己的位置，不挤在这里。"""
    s = subline_of(J)
    assert "偶尔特别慢" not in s and "第 2 轮回答开头被吞" not in s and "平均比" not in s
    j = {"labels": [{"kind": "spiky", "k": "spiky:s4", "segment": "s4", "rounds": [16]}], "lines": []}
    assert "偶尔特别慢" not in subline_of(j)


def test_millisecond_segments_are_not_shown_as_zero():
    # 几毫秒的段多留一位：0.004～0.012，不再显示成 0.00～0.01；一般的段照旧两位
    assert run(["f", "fms", "rangeTxt"], "rangeTxt([0.004,0.012,0.008],[1,2,3])") == "0.004～0.012"
    assert run(["f", "fms"], "[fms(0.003),fms(0.25),fms(1.234),fms(0)]") == ["0.003", "0.25", "1.23", "0.00"]
    assert '<td class="r">${fms(s.med)}</td>' in JS
