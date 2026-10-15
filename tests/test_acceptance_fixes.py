"""检查点 3 真机验收发现的 9 个问题（后台部分）。全用假模型、假设备。"""

import copy
import json

import pytest

from fsw import advise as adv
from fsw import measure as meas
from fsw.llm import FakeLLM
from tests.test_advise import judged
from tests.test_identify_api import make, prepared  # noqa: F401  (fixture)


# ---- 1. 负责人版不能出现文件名、行号、函数名 ----

@pytest.mark.parametrize("field,text", [
    ("why", "设备本地也能听出你停了（afe_audio_engine.cc 第 483 行），但只拿来亮灯。"),
    ("impact", "要等 ResetDecoder() 跑完才出声。"),
    ("rec", "建议改 application.cc 里的那一处。"),
    ("why", "第 555 行把声音丢了。"),
])
def test_manager_text_with_code_is_refused(tmp_path, field, text):
    d, j = judged("door")
    fixes = copy.deepcopy(d["page"]["fixes"])
    fixes[0][field] = fixes[0][field] + text
    reply = "```json\n" + json.dumps({"fixes": fixes, "fine": "x"}, ensure_ascii=False) + "\n```"
    r = adv.advise(FakeLLM(reply), tmp_path, j, d["scenario"], save_to=tmp_path / "a")
    assert not r["ok"] and "负责人" in r["message"]


def test_manager_option_text_with_code_is_refused(tmp_path):
    d, j = judged("door")
    fixes = copy.deepcopy(d["page"]["fixes"])
    fixes[0]["opts"][0]["effect"] += "（改 net_auth.c 第 207 行）"
    reply = "```json\n" + json.dumps({"fixes": fixes, "fine": "x"}, ensure_ascii=False) + "\n```"
    r = adv.advise(FakeLLM(reply), tmp_path, j, d["scenario"], save_to=tmp_path / "a")
    assert not r["ok"] and "负责人" in r["message"]


def test_code_is_fine_in_how_and_dev(tmp_path):
    d, j = judged("door")
    fixes = copy.deepcopy(d["page"]["fixes"])
    fixes[0]["opts"][0]["how"] += "（net_auth.c 第 207 行 parse_auth_reply()）"
    fixes[0]["dev"]["cause"] += "（net_auth.c:207）"
    reply = "```json\n" + json.dumps({"fixes": fixes, "fine": "x"}, ensure_ascii=False) + "\n```"
    assert adv.advise(FakeLLM(reply), tmp_path, j, d["scenario"], save_to=tmp_path / "a")["ok"]


def test_prompt_says_manager_text_has_no_code():
    d, j = judged("door")
    p = adv.build_prompt(j, d["scenario"])
    assert "不能写文件名、行号、函数名" in p


# ---- 2. 选完源码扫"AI 推荐"：源码没变就用上次结果，并告诉页面这次调没调用 ----

def test_suggest_reuses_last_result_when_source_unchanged(make, tmp_path):  # noqa: F811
    calls = []

    def llm():
        calls.append(1)
        return FakeLLM('["从刷卡到开门", "从开机到连上网"]')
    c, _ = make(llm)
    src = prepared(c, tmp_path)
    body = {"work": src["work"], "name": "door-ctrl", "project_dir": src["project_dir"], "fingerprint": src["fingerprint"]}
    a = c.post("/api/suggest", json=body).json()
    b = c.post("/api/suggest", json=body).json()
    assert a["called"] and not b["called"] and b["phrases"] == a["phrases"] and len(calls) == 1


# ---- 3. 对话类喊醒那一下（没有起点的一轮）不算"没收全" ----

def spec():
    return meas.Spec(start="P1", segments={"a": ["P1", "P2"], "b": ["P2", "P5"]},
                     lines=[{"name": "线", "segs": [["P1", "P2"], ["P2", "P5"]]}])


def test_round_without_start_point_is_ignored_not_incomplete():
    lines = ["FSW,RESET,1000,0", "FSW,P2,2000,0", "FSW,P5,3000,0",          # 喊醒：没有"你说完"
             "FSW,RESET,10000,0", "FSW,P1,10500,1", "FSW,P2,11000,0", "FSW,P5,12000,0"]
    out = meas.collect(lines, spec())
    assert [r["ok"] for r in out] == [True]


def test_round_with_start_but_missing_later_point_is_still_incomplete():
    lines = ["FSW,RESET,1000,0", "FSW,P1,1500,1", "FSW,P5,3000,0", "FSW,RESET,10000,0"]
    out = meas.collect(lines, spec())
    assert out and not out[0]["ok"] and out[0]["missing"] == ["P2"]
