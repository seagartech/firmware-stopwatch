"""10-26 核对 8777 那次全程报告找到的几处（数都对，显示有问题）：
1. 到回到聆听（看回答长短）那条线和只在它上面的段，报告里一个字都没有：单列"只记录、不判快慢"，写一般、最快、最慢；
   等级写"达标"、理由写"不判快慢"自相矛盾：改成"未判"。
2. 上传那段和服务器听写合成一段时，谁负责只写了前一段的「固件、网络」：两段的合起来。
3. 计时图块里的秒数按比例摊过（0.29 / 0.30，表里 0.28）；"早 0.38 秒"是两条线中位数相减，逐轮差的中位数是 0.36。"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsw import judge as jdg

JS = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
node = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
STUB = ("const f=(x,d=2)=>x==null||!isFinite(x)?'—':x.toFixed(d);"
        "const htmlEsc=t=>String(t||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));"
        "const SANS='';const med=a=>{const s=[...a].sort((x,y)=>x-y),m=s.length/2;return s.length%2?s[m|0]:(s[m-1]+s[m])/2};")


def _fn(name, end):
    i = JS.index(name)
    return JS[i:JS.index(end, i)]


def _run(js):
    return subprocess.run(["node", "-e", STUB + js], capture_output=True, text=True, check=True).stdout


def test_merged_segment_owner_includes_both_parts():
    sc = {"start": {"point": "P1"}, "last_points": ["P6"], "point_names": {"P5": "上传首包", "P6": "上传末包", "P7": "收到识别结果"},
          "segments": {"P5-P6": {"from": "P5", "to": "P6", "name": "上行音频发送", "owner": "固件、网络", "side": "device"},
                       "P6-P7": {"from": "P6", "to": "P7", "name": "语音识别", "owner": "网络、服务器", "side": "outside"}},
          "lines": [{"name": "L", "segments": ["P5-P6", "P6-P7"]}]}
    rounds = [{"n": 1, "segs": {"P5-P6": 0.30, "P6-P7": -0.03}, "ends": [0.27]}]
    sc2, r2, notes = jdg.merge_overruns(sc, rounds)
    g = sc2["segments"]["P5-P7"]
    assert g["owner"] == "固件、网络、服务器" and g["side"] == "device" and notes
    assert r2[0]["segs"]["P5-P7"] == pytest.approx(0.27)
    # 两段都在设备外：合起来也在设备外
    sc["segments"]["P5-P6"]["side"] = "outside"
    assert jdg.merge_overruns(sc, rounds)[0]["segments"]["P5-P7"]["side"] == "outside"


def test_answer_length_line_is_not_judged_and_not_called_passing():
    src = Path(jdg.__file__).read_text(encoding="utf-8")
    assert 'lv, why = "未判", ANSWER_TXT' in src and 'lv, why = "达标", ANSWER_TXT' not in src


@node
def test_record_only_line_is_kept_aside_not_dropped():
    fn = _fn("function lenByContent(", "\n// 报告顶上一行")
    sc = {"lines": [{"name": "到首段出声", "end": "首段出声", "segments": ["a", "b"], "first": True},
                    {"name": "到回到聆听", "end": "回到聆听", "segments": ["a", "c", "d"]}],
          "segments": {"a": {}, "b": {}, "c": {}, "d": {}}}
    J = {"scenario": sc, "lines": [{"name": "到回到聆听", "verdict": "看回答长短"}]}
    out = json.loads(_run(fn + f"console.log(JSON.stringify(lenByContent({json.dumps(J)})))"))
    assert [l["name"] for l in out["lines"]] == ["到首段出声"]
    assert out["recOnly"] == {"lines": ["到回到聆听"], "segments": ["c", "d"]}


@node
def test_record_only_box_shows_median_min_max_in_both_views():
    fn = _fn("function recOnlyBox(", "\nfunction reportPage(")
    D = {"n": 10, "S": {"startShort": "说话结束", "recOnly": {"lines": ["到回到聆听"], "segments": ["P9-P10"]}},
         "J": {"scenario": {"segments": {"P9-P10": {"name": "回复音频连续播放", "owner": "固件、硬件"}}},
               "lines": [{"name": "到回到聆听", "end": "回到聆听", "med": 5.1426, "min": 4.6971, "max": 7.8365, "count": 10, "of": 10}],
               "segments": [{"k": "P9-P10", "med": 4.033, "min": 3.008, "max": 6.603, "count": 10, "of": 10}]}}
    h = _run(fn + f"console.log(recOnlyBox({json.dumps(D, ensure_ascii=False)}))")
    assert "只记录、不判快慢" in h and "说话结束到回到聆听" in h and "回复音频连续播放" in h
    for x in ("5.14", "4.70", "7.84", "4.03", "3.01", "6.60", "10/10"):
        assert x in h
    page = _fn("function reportPage(", "\n// ===== ")
    assert "${recOnlyBox(D)}" in page                                    # 结论建议版
    assert "${recOnlyBox(D)}" in _fn("function dev(D){", "\n// ===== 页面 =====")   # 技术细节版（导出两版都带）


@node
def test_chart_numbers_are_the_segment_medians_and_early_uses_per_round_lead():
    fn = _fn("function niceT(", "\n// \"偶发特别慢\"")
    segs = {"P1-P2": 0.0002, "P2-P5": 0.0223, "P5-P7": 0.2751, "P7-P8": 0.0378, "P8-P13": 0.3994, "P8-P9": 0.7511}
    D = {"n": 10, "S": {"start": "说话结束", "segs": {k: {"name": k, "owner": "o", "v": [v]} for k, v in segs.items()}},
         "allSegs": [{"k": k, "med": v} for k, v in segs.items()], "use": {"P1-P2": 2, "P2-P5": 2, "P5-P7": 2, "P7-P8": 2},
         "lines": [{"name": "换上表情", "end": "换上表情", "col": "#a", "soft": "#b", "med": 0.7258, "segs": ["P1-P2", "P2-P5", "P5-P7", "P7-P8", "P8-P13"]},
                   {"name": "首段出声", "end": "首段出声", "col": "#c", "soft": "#d", "med": 1.1091, "segs": ["P1-P2", "P2-P5", "P5-P7", "P7-P8", "P8-P9"]}],
         "J": {"lines": [], "lead": {"first": "换上表情", "second": "首段出声", "med": 0.3626}}}
    D["main"] = D["lines"][1]
    h = _run(fn + f"const D={json.dumps(D, ensure_ascii=False)};D.main=D.lines[1];console.log(timeline(D))")
    nums = re.findall(r'<span class="r num">([\d.]+) 秒</span>', h)
    # 和「分段用时」表里一样；编号先给主线，两条线共用的段只列一次（10-26）
    assert nums == ["0.28", "0.75", "0.40"], nums
    assert "同上" not in h.split("</svg>", 1)[0] and "从这里分开走" in h  # 10-27 用户："同上"看不懂：共用的开头照原样画，分开的地方标出来
    assert "早 0.36 秒" in h and "早 0.38" not in h
    assert "另含极短段" in h
