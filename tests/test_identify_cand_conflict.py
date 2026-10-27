"""10-27 8791"理清流程"失败之后的通用修复：第 2 步自己写的位置（登记回调的 lambda 里那一句）核对得上，又附了一个候选编号，
候选指向检测翻转那一支里的另一句。码表一见候选编号就照候选整体替换文件、行号、原文，函数名却留着模型写的，两边对不上判"函数不对"，
又被当成要重读代码，没追问直接失败。现在：
- 自己写的核对得上：以自己写的为准，候选只作参考；
- 采用候选位置时，函数名按最终位置重新算；
- 两边真冲突、码表判断不了：同一段对话里问一次让它选；
- 其它不用再读源码就能改的（函数名写错、mode 抄的原文对不上、第 1 步不是检测翻转）都给改法。
（8791 那次的原话是私有固件，不放进仓库；同样结构的假源码在这里，原话在本机另外核对过。）全用假源码，不调用大模型。"""

import copy

import pytest

from fsw import candidates as cd
from fsw import identify as idf
from tests.test_candidates import APP, AUDIO, ENGINE, _flow, _funcs, _line
from tests.test_structure import write

SERVICE = """\
void Service::Init() {
    engine_->OnVad([this](bool speaking) {
        speaking_ = speaking;
        if (callbacks_.on_vad) {
            callbacks_.on_vad(speaking);
        }
    });
}
"""
CB = _line(ENGINE, "cb_(false);")  # 候选：检测翻转那一支里通知回调的那一句


@pytest.fixture
def proj(tmp_path):
    return write(tmp_path / "voice", {"main/engine.cc": ENGINE, "main/app.cc": APP, "main/audio.cc": AUDIO, "main/service.cc": SERVICE})


@pytest.fixture
def cs(proj):
    return cd.find(proj, "语音对话")


def _k(cs, file, line):
    return next(c["id"] for c in cs if (c["file"], c["line"]) == (file, line))


def _with_step2(proj, **over):
    """在第 1 步（检测翻转）后面插一步：同一次翻转通知到登记回调的 lambda 里（像 8791 的第 2 步）。"""
    flow = _funcs(_flow(proj))
    s = {"n": 2, "what": "同一次翻转通知到服务", "file": "main/service.cc", "line": _line(SERVICE, "callbacks_.on_vad(speaking);"),
         "code": "callbacks_.on_vad(speaking);", "func": "Service::Init 里登记的 OnVad 回调 lambda", "role": "", "branch": "",
         "repeat": "once", "after": "第 1 步调用的回调就是这个 lambda", **over}
    flow["chain"].insert(1, s)
    for i, c in enumerate(flow["chain"], 1):
        c["n"] = i
    return flow


def test_own_location_same_as_candidate(proj, cs):
    flow = _funcs(_flow(proj))
    flow["chain"][0]["cand"] = _k(cs, "main/engine.cc", CB)
    got, bad = idf.check_chain(proj, flow, "语音对话", [], fresh=True, cands=cs)
    assert not bad and (got["chain"][0]["file"], got["chain"][0]["line"]) == ("main/engine.cc", CB)
    assert not [t for t in got["tips"] if "候选" in t and "为准" in t]


def test_like_8791_own_location_right_candidate_elsewhere_keeps_own(proj, cs):
    k = _k(cs, "main/engine.cc", CB)
    fixes = []
    got, bad = idf.check_chain(proj, _with_step2(proj, cand=k), "语音对话", fixes, fresh=True, cands=cs)
    assert not bad, bad
    s2 = got["chain"][1]
    assert (s2["file"], s2["line"], s2["func"]) == ("main/service.cc", _line(SERVICE, "callbacks_.on_vad(speaking);"),
                                                    "Service::Init 里登记的 OnVad 回调 lambda")
    assert any(k in t and "以自己写的为准" in t for t in got["tips"])


def test_own_location_wrong_candidate_right_uses_candidate(proj, cs):
    k = _k(cs, "main/engine.cc", CB)
    flow = _funcs(_flow(proj))
    flow["chain"][0].update(cand=k, line=1, code="cb_(false);")  # 行号抄错了，原文就是候选那一句
    got, bad = idf.check_chain(proj, flow, "语音对话", [], fresh=True, cands=cs)
    assert not bad and (got["chain"][0]["file"], got["chain"][0]["line"]) == ("main/engine.cc", CB)


def test_candidate_location_recomputes_the_function_name(proj, cs):
    k = _k(cs, "main/engine.cc", CB)
    flow = _funcs(_flow(proj))
    s = flow["chain"][0]
    del s["file"], s["line"], s["code"]
    s.update(cand=k, func="Service::Init 里的回调")  # 函数名说的不是候选那一行：不混用
    got, bad = idf.check_chain(proj, flow, "语音对话", [], fresh=True, cands=cs)
    assert not bad and got["chain"][0]["line"] == CB and "OnResult" in got["chain"][0]["func"] and "Service" not in got["chain"][0]["func"]


def test_real_conflict_is_asked_once_in_the_same_conversation(proj, cs):
    k = _k(cs, "main/engine.cc", CB)
    wrong = _with_step2(proj, cand=k, file="main/app.cc", line=_line(APP, "SetEmotion(root);"))  # 原文、函数都不在这一行，也不是候选那一句
    fixes = []
    _g, bad = idf.check_chain(proj, copy.deepcopy(wrong), "语音对话", fixes, fresh=True, cands=cs)
    assert len(bad) == 1 and "不是同一处" in bad[0] and k in bad[0]
    assert {f["bad"] for f in fixes if not f.get("soft")} >= set(bad)
    text = next(f["text"] for f in fixes if f["bad"] == bad[0])
    assert "请选一个" in text and f"main/engine.cc 第 {CB} 行" in text and "main/app.cc" in text
    fixed = _with_step2(proj, cand=k)  # 它选了自己写的那处
    _g, bad2 = idf.check_chain(proj, fixed, "语音对话", [], fresh=True, cands=cs)
    assert not bad2


def test_wrong_function_name_gets_a_fix(proj):
    flow = _funcs(_flow(proj))
    flow["chain"][4]["func"] = "Board::Init"
    fixes = []
    _g, bad = idf.check_chain(proj, flow, "语音对话", fixes, fresh=True)
    assert bad and {f["bad"] for f in fixes if not f.get("soft")} >= set(bad)
    assert any("把 func 改成" in f["text"] and "OutputTask" in f["text"] for f in fixes)


def test_mode_copied_text_mismatch_gets_a_fix(proj):
    flow = _funcs(_flow(proj))
    flow["mode"]["code"] = "return listen_mode_;"
    fixes = []
    _g, bad = idf.check_chain(proj, flow, "语音对话", fixes, fresh=True)
    assert bad and {f["bad"] for f in fixes if not f.get("soft")} >= set(bad)
    assert any(f["text"].startswith("mode 写的是") and "return aec_" in f["text"] for f in fixes)


def test_first_step_not_the_detection_gets_a_fix(proj):
    flow = _funcs(_flow(proj))
    flow["chain"][0].update(what="按下按键", file="main/app.cc", line=_line(APP, "auto bits = Wait();"), code="auto bits = Wait();", func="Run")
    fixes = []
    _g, bad = idf.check_chain(proj, flow, "语音对话", fixes, fresh=True)
    first = [b for b in bad if b.startswith("detect 里写着")]
    assert first and any(f["bad"] == first[0] and "第 1 步改成" in f["text"] for f in fixes)
