"""10-26 结论版说人话（第一步，不改数据结构）：最终用户看不懂代码、也没法追问，结论区要自己说明白。
- 第一句：你{起始动作}后，一般约 X 秒{感觉到的第一下}（n 次里 k 次在标准内）；词按设备类别（后台给）和主指标那一下的感觉方式取，对不上用通用词。
- 第二句：等云端多少、设备自己多少；第三句三种：云端占七成以上 / 设备上最长那段 / 已经很快。
- 达标按平均判（T/CESA 1045 6.3.3），逐次超过的只当"偶尔超过"补充。
- 图：不用圈号、不写"共用""早 X 秒"；设备内部的极短几步合成一句。
- 次要的"第一下"和全程放"其他参考"；老存档、缺信息时退回原来的写法。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fsw import identify as idf

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "fsw/web/static/app.js").read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
STUB = ("const f=(x,d=2)=>x==null||!isFinite(x)?'—':x.toFixed(d);"
        "const htmlEsc=t=>String(t||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));"
        "const SANS='';const med=a=>{const s=[...a].sort((x,y)=>x-y),m=s.length/2;return s.length%2?s[m|0]:(s[m-1]+s[m])/2};"
        "const num=a=>a.filter(x=>x!=null&&isFinite(x));const fms=x=>x>0&&x<.1?x.toFixed(3):f(x);")


def _fn(name, end=None):
    """从 name 截到 end；不给 end 就截到下一个顶层的 function / const。"""
    i = JS.index(name)
    if end is None:
        j = min(x for x in (JS.find("\nfunction ", i + 1), JS.find("\nconst ", i + 1), JS.find("\n// ", i + 1)) if x > 0)
        return JS[i:j] + "\n"
    return JS[i:JS.index(end, i)]


CODE = (_fn("function measureLine(", "function titleHtml(") + _fn("function segMed(", "// 把一条线的各段")
        + _fn("function stages(") + _fn("function niceT(") + _fn("function step(") + _fn("function titleHtml(") + _fn("function subline("))


def _run(D, expr):
    js = STUB + CODE + f"(()=>{{const D={json.dumps(D, ensure_ascii=False)};console.log({expr})}})()"
    return subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout


def _D(cloud=1.70, dev=0.095, ends=None, words=None, kind="声音", best=None, good=1.0):
    """说完 → (设备极短几步) → 等云端 → 设备解码出声；一条次要的"第一下"（识别文字，被藏起来）；全程。"""
    ends = ends or [1.40, 1.60, 1.75, 1.80, 1.84, 1.85, 2.05, 2.10, 2.20, 2.27]
    segs = {"P1-P2": {"name": "停口通知", "owner": "固件", "v": [0.0002]},
            "P2-P3": {"name": "事件分发", "owner": "固件", "v": [0.0003]},
            "P3-P8": {"name": "服务器识别和生成", "owner": "服务器", "v": [cloud]},
            "P3-P5": {"name": "服务器识别", "owner": "服务器", "v": [0.38]},
            "P8-P12": {"name": "解码并送到扬声器", "owner": "固件", "v": [dev]}}
    main = {"name": "到扬声器首次出声", "end": "扬声器首次出声", "col": "#c", "soft": "#d", "med": 1.85, "ends": ends,
            "segs": ["P1-P2", "P2-P3", "P3-P8", "P8-P12"]}
    rec = {"name": "到收到识别文字", "end": "收到识别文字", "col": "#a", "soft": "#b", "med": 0.387, "ends": [0.387] * 10,
           "segs": ["P1-P2", "P2-P3", "P3-P5"]}
    return {"n": 10, "S": {"real": True, "start": "说话结束", "startShort": "说话结束", "segs": segs,
                           "plain": {"cat": "语音对话", "words": words if words is not None else idf.PLAIN_WORDS["语音对话"],
                                     "server": "服务器：语音识别、生成回复、语音合成",
                                     "kind": {"扬声器首次出声": kind, "收到识别文字": "画面"},
                                     "unseen": {"收到识别文字": True}}},
            "allSegs": [{"k": k, "med": v["v"][0]} for k, v in segs.items()], "use": {"P1-P2": 2, "P2-P3": 2},
            "lines": [rec, main],
            "J": {"scenario": {"lines": [{"name": "到收到识别文字", "also_first": True}, {"name": "到扬声器首次出声", "first": True},
                                         {"name": "全程", "measure": True, "end": "回复播完"}],
                               "segments": {"P3-P8": {"owner": "服务器", "side": "outside"}, "P3-P5": {"owner": "服务器", "side": "outside"}}},
                  "lines": [{"name": "到扬声器首次出声", "exp_std": {"fair": 2.0, "good": good, "short": "T/CESA 1045"}},
                            {"name": "全程", "measure": True, "med": 2.995, "min": 2.43, "max": 5.86}],
                  "lead": {"first": "到收到识别文字", "second": "到扬声器首次出声", "med": 1.46}, "best_fix": best},
            "main": main}


def test_headline_is_one_plain_sentence():
    h = _run(_D(), "titleHtml(D)")
    assert "<h1>你说完话后，一般约 1.85 秒听到第一声回复（10 次里 6 次在 2 秒内）。</h1>" in h


def test_generic_words_when_category_unknown():
    h = _run(_D(words={}, kind="灯光"), "titleHtml(D)")
    assert "你操作完后，一般约 1.85 秒看到灯亮" in h
    assert "说完话" not in h


def test_cloud_mostly_sentence_and_average_verdict():
    s = _run(_D(), "subline(D)")
    assert "其中约 1.70 秒在等云端（语音识别、生成回复、语音合成），设备自己约 0.10 秒。" in s
    assert "慢主要在云端，改设备代码帮不上" in s
    assert "平均 1.89 秒，达标" in s and "偶尔超过：10 次里 4 次超过 2.0 秒" not in s  # fair 写成 2，不是 2.0
    assert "偶尔超过：10 次里 4 次超过 2 秒" in s
    assert "没达标" not in s


def test_device_biggest_uses_best_fix():
    D = _D(cloud=0.40, dev=1.40, best={"name": "解码并送到扬声器", "med": 1.40, "share": 76})
    s = _run(D, "subline(D)")
    assert "最值得改的是设备上的「解码并送到扬声器」" in s and "改设备代码帮不上" not in s


def test_fast_says_no_change_needed():
    D = _D(ends=[0.5] * 10)
    D["lines"][1]["med"] = D["main"]["med"] = 0.5
    s = _run(D, "subline(D)")
    assert "已经很快，不用改。" in s


def test_failing_average_says_not_reached():
    s = _run(_D(ends=[2.5] * 10), "subline(D)")
    assert "没达标" in s and "偶尔超过" not in s


def test_other_refs_explain_secondary_and_whole_round():
    r = _run(_D(), "plainRefs(D)")
    assert "<h2>其他参考</h2>" in r
    assert "这只说明云端很快就听懂了你的话" in r and "你感觉不到" in r and "你真正在等的是扬声器首次出声那 1.85 秒" in r
    assert "全程（到回复播完）" in r and "长短主要看回答说了多少字，不判快慢" in r


def test_plain_chart_has_no_circles_shared_or_early_line():
    t = _run(_D(), "timeline(D)")
    for bad in "①②③":
        assert bad not in t
    assert "（共用）" not in t and "早 " not in t and "另含极短段" not in t
    assert "收到识别文字" not in t.split("</svg>")[0]  # 次要的线挪到"其他参考"
    assert "设备内部还有几步，加起来不到 0.05 秒，可忽略。" in t
    assert "这段在设备外，设备改不了" in t and "等云端" in t
    assert "行业标准：平均不超过 2 秒" in t
    assert "图里的数是 10 次里的一般情况（中位数）" in t


def test_old_saves_fall_back_to_old_wording():
    D = _D()
    D["S"].pop("plain")
    h = _run(D, "titleHtml(D)")
    assert "你说完话后" not in h and "扬声器首次出声 1.85 秒" in h
    t = _run(D, "timeline(D)")
    assert "①" in t


def test_category_words_come_from_backend_page():
    r = {"steps": [], "category": "语音对话"}
    assert idf.PLAIN_WORDS["语音对话"]["start"] == "说完话"
    assert "PLAIN_CAT" not in JS and "说完话" not in JS  # 网页不认具体类别，类别的说法由后台给
