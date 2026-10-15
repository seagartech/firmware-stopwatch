"""任务 17：为什么、怎么改。问题从数据算出（任务 16 的标签），文字由 AI 读代码和数据写；码表核对格式，不对就不用。

全用假模型（录好的回复）；门禁刷卡的录好回复就是原型里写好的那几张卡片。
"""

import copy
import json
from pathlib import Path

import pytest

from fsw import advise as adv
from fsw import judge as J
from fsw.llm import FakeLLM

DEMO = Path(__file__).resolve().parent.parent / "fsw/web/demo"


def demo(name):
    return json.loads((DEMO / f"{name}.json").read_text(encoding="utf-8"))


def demo_rounds(d):
    sc, res = d["scenario"], d["result"]
    n = len(next(iter(res["segments"].values())))
    out = []
    for i in range(n):
        segs = {k: v[i] for k, v in res["segments"].items()}
        out.append({"n": i + 1, "segs": segs, "ends": [sum(segs[k] for k in l["segments"]) for l in sc["lines"]],
                    "probs": [k for k, rs in res["problems"].items() if i + 1 in rs]})
    return out


def recorded(d):
    """录好的 AI 回复：原型里这套数据的卡片和"不用改的"。"""
    return "读完了。\n```json\n" + json.dumps({"fixes": d["page"]["fixes"], "fine": d["page"]["fine"]}, ensure_ascii=False) + "\n```"


def judged(name):
    d = demo(name)
    return d, J.judge(d["scenario"], demo_rounds(d))


def test_problems_come_from_the_labels_not_from_ai():
    d, j = judged("voice")
    probs = adv.problems(j, d["scenario"])
    assert [p["key"] for p in probs] == ["main", "problem:sw", "reversed", "spiky"]
    assert probs[0]["seg"] == "think" and probs[3]["seg"] == "dec"


def test_prompt_has_data_and_code_places_and_asks_for_json():
    d, j = judged("door")
    p = adv.build_prompt(j, d["scenario"], steps=d["F"]["steps"], what="从刷卡到开门")
    assert "服务器校验并返回" in p and "net_auth.c" in p and "```json" in p and "不改任何文件" in p
    assert "效果" in p and "估算" in p and "大、中、小" in p


def test_recorded_door_reply_passes_the_check(tmp_path):
    d, j = judged("door")
    r = adv.advise(FakeLLM(recorded(d)), tmp_path, j, d["scenario"], steps=d["F"]["steps"], save_to=tmp_path / "advice")
    assert r["ok"] and len(r["fixes"]) == 1 and r["fixes"][0]["seg"] == "check"
    assert r["fixes"][0]["opts"][0]["work"] in ("大", "中", "小", "不确定")
    assert (tmp_path / "advice" / "reply.txt").exists()  # AI 的原话存一份


@pytest.mark.parametrize("break_it,words", [
    (lambda f: f[0].update(opts=f[0]["opts"][:1]), "2～3 个方案"),
    (lambda f: f[0]["opts"][0].update(work="两天"), "工作量"),
    (lambda f: f[0].update(when=["没有这个问题"]), "对不上"),
    (lambda f: f[0].update(seg="nope"), "没有这一段"),
    (lambda f: f[0]["opts"][0].pop("who"), "找谁"),
    (lambda f: f[0].pop("dev"), "技术细节"),
])
def test_bad_reply_is_not_used(tmp_path, break_it, words):
    d, j = judged("door")
    fixes = copy.deepcopy(d["page"]["fixes"])
    break_it(fixes)
    text = "```json\n" + json.dumps({"fixes": fixes, "fine": "x"}, ensure_ascii=False) + "\n```"
    r = adv.advise(FakeLLM(text), tmp_path, j, d["scenario"], steps=d["F"]["steps"], save_to=tmp_path / "a")
    assert not r["ok"] and words in r["message"] and (tmp_path / "a" / "reply.txt").exists()


def test_every_must_or_should_problem_gets_a_card(tmp_path):
    d, j = judged("voice")
    fixes = [f for f in d["page"]["fixes"] if "main" in f["when"]]  # 漏了"偶发慢、先后反了"那张
    text = "```json\n" + json.dumps({"fixes": fixes, "fine": "x"}, ensure_ascii=False) + "\n```"
    r = adv.advise(FakeLLM(text), tmp_path, j, d["scenario"], save_to=tmp_path / "a")
    assert not r["ok"] and "没写" in r["message"]


def test_at_least_one_recommended_option(tmp_path):
    d, j = judged("door")
    fixes = copy.deepcopy(d["page"]["fixes"])
    for o in fixes[0]["opts"]:
        o.pop("rec", None)
    text = "```json\n" + json.dumps({"fixes": fixes, "fine": "x"}, ensure_ascii=False) + "\n```"
    r = adv.advise(FakeLLM(text), tmp_path, j, d["scenario"], save_to=tmp_path / "a")
    assert r["ok"] and r["fixes"][0]["opts"][0]["rec"] == 1  # 一个都没标建议，码表把第一个当建议（默认勾上）


def test_model_failure_is_plain(tmp_path):
    d, j = judged("door")
    r = adv.advise(FakeLLM("", test_kind="offline"), tmp_path, j, d["scenario"], save_to=tmp_path / "a")
    assert not r["ok"] and r["kind"] == "offline" and "重试" in r["message"]


def test_nothing_to_fix_needs_no_model(tmp_path):
    d, j = judged("door")
    j = {**j, "labels": [x for x in j["labels"] if x["level"] == "可以不改"]}
    m = FakeLLM("不该被调用")
    r = adv.advise(m, tmp_path, j, d["scenario"], save_to=tmp_path / "a")
    assert r["ok"] and r["fixes"] == [] and not m.prompts


def test_prompt_asks_for_names_not_codes_and_short_why():
    d, j = judged("voice")
    p = adv.build_prompt(j, d["scenario"])
    assert "不要写 P2-P5 这样的编号" in p and "2～4 句" in p
