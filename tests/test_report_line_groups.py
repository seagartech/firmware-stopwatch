"""10-26 用户看报告问"眼睛表情和情绪表情是不是一回事""下面三行等云端各是什么"之后的通用修复：
1. 段归属：两头分别在"收到不同服务器消息"的回调里（结构检查标了 split）、起点本身是收到服务器消息的一步 → 设备外（网络、服务器）；
2. 设备外各段写它自己在等什么（standards.plain_cloud），不再统一贴"服务器负责什么"整句；
3. 报告底部按线分组，每行写占这条线的百分之几，共用段只写一次并注明还有哪几条也走；
4. 顺带的线按实际量到的点起名；名字太像的按被什么带起来的分开；
5. 图里和上面一样的开头写"和上面一样，走到「某一步」"，不用圈号。
回归数据：一次真测（语音对话，两条表情线）的结果页 + 只留 FSW 行的原始日志（tests/fixtures/voice_two_face_lines_*）。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fsw import identify as idf, judge as jdg, measure as meas, standards as stds
from fsw.scenario import names_too_close

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests/fixtures"
JS = (ROOT / "fsw/web/static/app.js").read_text(encoding="utf-8")
node = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")


def _page():
    return json.loads((FX / "voice_two_face_lines_page.json").read_text(encoding="utf-8"))["page"]


def _sc(page=None):
    page = page or _page()
    return stds.with_default_experience(stds.real_scenario(page, stds.find(page.get("steps") or [])))


def _rounds(sc, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy(FX / "voice_two_face_lines_raw.txt", run / "raw.log")
    return [r for r in meas.run_rounds(run, meas.Spec.from_scenario(sc)) if r["ok"]]


# ---------- 1 段归属 ----------

def test_wait_for_next_server_message_is_outside():
    seg = _sc()["segments"]
    g = seg["P7-P14"]  # 收到TTS开始 → 换上表情（收到另一条消息才执行）
    assert g["side"] == "outside" and g["owner"] == "网络、服务器" and g.get("split")
    assert seg["P7-P21"]["side"] == "device"  # 设备状态一变就走的那段仍是设备
    assert seg["P9-P10"]["side"] == "device"


def test_old_saves_without_check_keep_old_owner():
    page = _page()
    for s in page["steps"]:
        s.pop("chk", None)
    page.pop("branch")
    sc = _sc(page)
    assert sc["segments"]["P7-P14"]["side"] == "device" and sc["segments"]["P7-P14"]["owner"] == "固件"
    assert "到说出状态已生效" not in [l["name"] for l in sc["lines"]]  # 不知道识别时有几个点的老存档不改名
    assert "plain" not in sc["segments"]["P7-P14"]


def test_no_rename_when_every_point_was_inserted():
    page = _page()
    page["branch"]["nodes"] = ["S1", "P14"]  # 识别时就这两个点，都插上了：终点没被换掉
    assert "到说出状态已生效" not in [l["name"] for l in _sc(page)["lines"]]


def test_split_needs_start_to_be_a_received_message():
    page = _page()
    for g in page["segs"]:
        g.pop("out", None)  # 起点前面那段不在设备外：起点不是"收到服务器消息"
    assert _sc(page)["segments"]["P7-P14"]["side"] == "device"


# ---------- 2 设备外那段写自己的事 ----------

def test_plain_cloud_uses_own_meaning():
    seg = _sc()["segments"]
    assert seg["P5-P6"]["plain"] == "语音识别"  # 段名说到了"服务器负责什么"里的那件
    assert seg["P8-P9"]["plain"] == "第一包回复语音传回设备"
    assert seg["P6-P8"]["plain"] == "第一句文字发到设备"
    assert seg["P6-P7"]["plain"] == "回复开始通知"
    assert seg["P7-P14"]["plain"] == "再发一条消息，收到后才换上表情"
    assert all("生成回复" not in (g.get("plain") or "") for g in seg.values())


def test_plain_cloud_rules_are_generic():
    assert stds.plain_cloud("等TTS首包", "", "云端：语音合成") == "语音合成"
    assert stds.plain_cloud("ASR 结果下发", "", "") == "语音识别 结果发到设备"
    assert stds.plain_cloud("", "", "服务器：开门鉴权、下发指令") == "开门鉴权、下发指令"  # 没段名：老写法
    assert stds.plain_cloud("云端鉴权", "", "服务器：开门鉴权、下发指令") == "开门鉴权"


# ---------- 4 线的命名 ----------

def test_line_named_by_point_actually_measured():
    ls = {l["name"]: l for l in _sc()["lines"]}
    eye = ls["到说出状态已生效"]
    assert eye["end"] == "说出状态已生效" and eye["actual"] and eye["segments"][-1] == "P7-P21"
    assert ls["情绪表情这条"]["end"] == "换上表情"  # 量到的就是用户看得到的那一下：不改


def test_names_too_close_rule():
    assert names_too_close("换表情", "换上表情") and names_too_close("表情这条", "到表情")
    assert not names_too_close("说出状态已生效", "换上表情") and not names_too_close("开始出声", "回复放完")


def _two_sides(end2="换表情", second_outside=True):
    page = _page()
    page["sideAsk"] = {"q": end2}  # 识别时大模型起的顺带那条的终点名（网页按它写）
    # 两条顺带的线终点名只差一个字：一条跟设备状态走（P7-P21 在设备上），一条跟服务器消息走
    page["steps"] = [{**s, "n": end2} if s["p"] == "P21" else s for s in page["steps"]]
    for s in page["steps"]:
        if s["p"] == "P21":
            s.update(felt="first", kind="画面")
            s["chk"] = []
        if s["p"] == "P14" and not second_outside:
            s["chk"] = []
    page["sides"][0]["end"] = "换上表情"
    return page


def test_close_names_get_their_trigger():
    ls = [(l["name"], l["end"]) for l in _sc(_two_sides())["lines"]]
    assert ("眼睛表情这条（跟设备自己走）", "换表情（跟设备自己走）") in ls
    assert ("情绪表情这条（跟服务器消息走）", "换上表情（跟服务器消息走）") in ls
    assert ("到开始出声", "开始出声") in ls  # 主线不动


def test_close_names_same_trigger_told_apart_by_where_they_are():
    ls = {l["name"]: l["end"] for l in _sc(_two_sides(second_outside=False))["lines"]}
    side = [n for n in ls if n.startswith(("眼睛表情这条", "情绪表情这条"))]
    assert len(side) == 2 and len(set(side)) == 2  # 从同一步分出去、同在一个文件：按行号分开
    assert all("第 " in n and " 行）" in n for n in side)


def test_close_names_keep_experience_and_order_rules():
    sc = _sc(_two_sides())
    names = {l["name"] for l in sc["lines"]}
    assert all(e["line"] in names for e in sc["experiences"])


def test_side_review_blocks_close_names_and_prompt_lists_existing(tmp_path):
    from tests.test_report_main_indicator import _r_for_side
    r = _r_for_side(tmp_path)
    r["side"] = {"name": "表情这条", "end": "换表情"}
    ans = '```json\n{"review": [{"id": "K2", "keep": true, "name": "表情那条", "end": "换上表情", "from": "P2", "kind": "画面"}]}\n```'
    idf.apply_side_review(str(tmp_path), r, idf.side_candidates(r), ans)
    assert r["sides"][0]["name"] == "表情那条（跟服务器消息走）"
    assert "{existing}" in idf.SIDE_REVIEW and "表情这条（终点：换表情）" in idf.existing_lines(r)


# ---------- 3、5 报告（网页） ----------

def _fn(name, end):
    i = JS.index(name)
    return JS[i:JS.index(end, i)]


STUB = ("const f=(x,d=2)=>x==null||!isFinite(x)?'—':x.toFixed(d);"
        "const htmlEsc=t=>String(t||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));"
        "const SANS='';const med=a=>{const s=[...a].sort((x,y)=>x-y),m=s.length/2;return s.length%2?s[m|0]:(s[m-1]+s[m])/2};"
        "const num=a=>a.filter(x=>x!=null&&isFinite(x));const fms=x=>x>0&&x<.1?x.toFixed(3):f(x);")
CODE = _fn("function measureLine(", "function titleHtml(") + _fn("function segMed(", "// 把一条线的各段") + _fn("function stages(", "// \"偶发特别慢\"") \
    + _fn("function niceT(", "// 一次的计时图")


def _D(tmp_path, plain=True):
    """按真测数据拼报告网页要的 D：段、线的中位数用后台 judge 算的（和报告接口一样）。"""
    sc0 = _sc()
    sc, ok, _ = jdg.merge_overruns(sc0, _rounds(sc0, tmp_path))
    j = jdg.judge(sc, ok)
    jl = {l["name"]: l for l in j["lines"]}
    show = [l for l in sc["lines"] if not l.get("measure")]
    cols = ["#1", "#2", "#3"]
    lines = [{"name": l["name"], "end": l["end"], "col": cols[i], "soft": cols[i] + "s", "med": jl[l["name"]]["med"],
              "ends": [r["ends"][sc["lines"].index(l)] for r in ok], "segs": l["segments"]} for i, l in enumerate(show)]
    main = next(x for x, l in zip(lines, show) if l.get("first"))
    use = {}
    for l in lines:
        for k in l["segs"]:
            use[k] = use.get(k, 0) + 1
    segs = {k: {"name": g["name"], "owner": g["owner"], "v": [r["segs"].get(k) for r in ok]} for k, g in sc["segments"].items()}
    D = {"n": len(ok), "S": {"real": True, "start": "说话结束", "startShort": "说话结束", "segs": segs,
                             "plain": {"cat": "语音对话", "words": idf.PLAIN_WORDS["语音对话"], "server": _page()["server"],
                                       "kind": {"开始出声": "声音"}, "unseen": {}} if plain else None},
         "allSegs": [{"k": s["k"], "med": s["med"]} for s in j["segments"]], "use": use, "lines": lines, "main": main,
         "J": {"scenario": {"lines": [{k: v for k, v in l.items() if k in ("name", "first", "also_first", "measure", "end")} for l in sc["lines"]],
                            "segments": sc["segments"], "point_names": sc["point_names"]},
               "lines": [{"name": l["name"], "exp_std": {"fair": 2.0, "good": 0.8, "short": "T/CESA 1045"}} if l.get("first") else {"name": l["name"]}
                         for l in sc["lines"]], "segments": j["segments"]}}
    if not plain:
        D["S"].pop("plain")
    return D


def _run(D, expr):
    js = STUB + CODE + f"(()=>{{const D={json.dumps(D, ensure_ascii=False)};D.main=D.lines.find(l=>l.name===D.main.name);console.log({expr})}})()"
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout


def _groups(t):
    """图下列表按线分组：{线名: 那一组的文字}。"""
    body = t.split("</svg>", 1)[1]
    out, parts = {}, body.split('<li style="margin-top:6px"><b>')[1:]
    for p in parts:
        name = p.split("</b>", 1)[0]
        out[name] = p
    return out


@node
def test_regression_groups_by_line_and_right_denominators(tmp_path):
    t = _run(_D(tmp_path), "timeline(D)")
    g = _groups(t)
    assert list(g) == ["到开始出声", "到说出状态已生效", "情绪表情这条"]
    m, eye, emo = g["到开始出声"], g["到说出状态已生效"], g["情绪表情这条"]
    assert "<b>等云端（第一句文字发到设备）</b>：一般 1.05 秒，占这条线 65%。这段在设备外" in m
    assert "<b>等云端（第一包回复语音传回设备）</b>：一般 0.12 秒，占这条线 8%。这段在设备外" in m
    # 0.04 那段（收到识别结果 → 收到TTS开始）只在表情线下面，占的是眼睛那条线（0.04 / 0.47）
    assert "回复开始通知" not in m and "回复开始通知" not in emo
    assert "<b>等云端（回复开始通知）</b>：一般 0.04 秒，占这条线 9%。这段在设备外" in eye and "「情绪表情这条」也走这一段" in eye
    # 0.76 那段归云端/网络，不再"属于设备"
    assert "<b>等云端（再发一条消息，收到后才换上表情）</b>：一般 0.76 秒，占这条线 61%。这段在设备外，设备改不了。" in emo
    assert "属于设备。" not in emo.split("这段在设备外")[0]
    assert "顺带的线" in eye and "顺带的线" in emo and "顺带的线" not in m
    assert "开头和上面一样" not in m
    assert "开头和上面一样，走到「收到TTS开始」" in emo and "开头和上面一样，走到「收到识别结果」" in eye
    assert "语音识别、生成回复" not in t.split("</svg>", 1)[1]


@node
def test_regression_chart_says_same_as_above_in_words(tmp_path):
    t = _run(_D(tmp_path), "timeline(D)")
    svg = t.split("</svg>", 1)[0]
    assert "同上 ①" not in svg and "①" not in svg
    assert "同上，到「收到识别结果」" in svg and "同上，到「收到TTS开始」" in svg
    assert "说出状态已生效 0.47 秒" in svg and "换上表情 1.25 秒" in svg


@node
def test_tech_version_keeps_numbers_but_no_circled_same(tmp_path):
    t = _run(_D(tmp_path, plain=False), "timeline(D)")
    assert "①" in t  # 技术细节版仍有编号
    assert "同上 ①" not in t.split("</svg>", 1)[0]


def test_actual_end_used_by_page():
    assert "l.sl||l.actual?l.end:" in JS
