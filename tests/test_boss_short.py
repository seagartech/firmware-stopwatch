"""负责人版写短（决策 98）：只改展示，内容一个字不删。

默认展开：一句话结论、要你拍板（必须改在前、建议改在后，每条加一行"建议做哪个、预计省多少"）、你的决定。
达标的只露一句"另有 n 条在合理范围里，不用改"；方案表、详细原因和影响、标签规则、发给对方的消息、结尾说明默认收起。
"要你拍板"挪到计时图前面（手机第一屏能看到）。导出的离线报告一样（用的是同一份页面，<details> 不用脚本也能点开）。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
node = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")


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


@node
def test_must_and_should_open_no_change_folded():
    ds = [{"k": "a", "lv": 1}, {"k": "b", "lv": 3}, {"k": "c", "lv": 0}, {"k": "d", "lv": 3}, {"k": "e", "lv": 2}]
    r = run(["bossSplit"], f"bossSplit({json.dumps(ds)})")
    assert [x["k"] for x in r["open"]] == ["c", "a", "e"]  # 必须改、建议改、未判（没判不等于没问题，不收起来）
    assert [x["k"] for x in r["fold"]] == ["b", "d"]       # 达标的收起来


@node
def test_recommended_option_line():
    F = {"opts": [{"name": "换模型", "effect": "约 0.5 秒（估算）"}, {"name": "边想边说", "effect": "约 0.3 秒（估算）", "rec": 1}]}
    assert run(["recLine"], f"recLine({json.dumps(F)})") == "建议：边想边说，预计约 0.3 秒（估算）。"
    assert run(["recLine"], "recLine(null)") == ""


def test_decide_comes_before_the_timing_chart():
    page = grab("reportPage")
    assert page.index("要你拍板") < page.index("一次的计时图")  # 结论版（说人话）写成"结论和建议"在"时间花在哪"前面
    assert page.index("结论和建议") < page.index("时间花在哪")


def test_details_folded_by_default():
    fa = grab("fixA")
    assert "<details" in fa and "adviceOptions(F)" in fa.split("<details", 1)[1]  # 方案表收在里面，不再勾选（10-21）
    assert "<b>已知情况</b>" in fa.split("<details", 1)[1] and "<b>使用影响</b>" in fa.split("<details", 1)[1]
    assert "不用改的" not in fa and "S.fine" not in fa  # 收起块删了（10-21 用户定）
    ot = grab("optTable")
    assert "pick?'':ESTNOTE" in ot  # 工作量怎么分：表下一行小字
    assert "replace(/^(小|中|大|不确定)" in ot  # 依据开头重复的档位去掉
    est = grab("ESTNOTE")
    assert "不确定" in est and "估算" not in est and "自动改代码" not in est
    ok = grab("devOk")
    assert "设备自己的几段都正常" in ok and "devOk(D)" in grab("decideList")
    dl = grab("decideList")
    assert "另有 ${" in dl and "达标" in dl and "标签规则" in dl.split("<details")[-1]
    mp = grab("myPick")
    assert "<details" in mp and "todoText(" in mp.split("<details", 1)[1] and "type=\"checkbox\"" not in mp  # 协作沟通：默认收起，没有勾选（10-21 用户定）


def test_no_details_open_by_default_in_manager_view():
    for fn in ("fixA", "decideList", "myPick"):
        assert "<details open" not in grab(fn)


def test_copy_button_turns_into_check_and_works_in_export():
    tc = grab("todoCopy")
    assert "已复制" in tc and "2000" in tc and "render(" not in tc  # 只改按钮本身，2 秒变回（10-21 用户定）
    assert "execCommand('copy')" in tc  # 本地文件打开时剪贴板被拦，用旧办法
    assert "${todoCopy.toString()}" in JS and "if(b.dataset.copy)b.dataset.copy=pre+b.dataset.copy" in JS  # 导出的报告里也能复制
