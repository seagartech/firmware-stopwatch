"""10-27 8790"找关键步骤"失败之后的通用修复：模型理出的事件链位置全部核对通过，但四个角色一个都没被认出来、也没有「全部放完」，
码表直接判失败、不追问（09:09 同一份源码却成功了：模型的写法不稳）。
1. 角色宽松认：role / roles 都认，列表、字符串、逗号顿号分隔；英文别名、常见中文同义词对到标准的几个；大小写、空格、连字符、全角不计较；
2. 不用再读源码就能改的问题（缺全部放完、缺识别那一步、少写一项、没写触发路径……）都给一条追问的改法：先在同一段对话里追问一次，
   还不行才判失败，失败页写明还缺什么、留着重试；
3. 失败（含追问后还不对）：大模型原话和问题清单存到项目的 identify/failed-<时间>.json（去掉密钥），服务日志写一行。只用假模型。"""

import copy
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from fsw import identify as idf
from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.identify_fixtures import DOOR_RESULT, door_source, reply_for

SK = "s" + "k-"  # 拼出来，免得仓库里出现像密钥的字面量（上传前检查会拦）


@pytest.mark.parametrize("c, want", [
    ({"role": "上传首包、上传末包"}, ["上传首包", "上传末包"]),
    ({"roles": ["first_result"]}, ["首个结果"]),
    ({"roles": "Upload-First Chunk, last_upload"}, ["上传首包", "上传末包"]),
    ({"role": "upload_first_chunk"}, ["上传首包"]),
    ({"role": "首包上传；最后一包"}, ["上传首包", "上传末包"]),
    ({"role": ["上传第一包", "第一个结果"]}, ["上传首包", "首个结果"]),
    ({"role": "最终结果，play_done"}, ["最后结果", "全部放完"]),
    ({"role": "全部播放完"}, ["全部放完"]),
    ({"roles": "all played"}, ["全部放完"]),
    ({"role": "播完"}, ["全部放完"]),
    ({"role": "输出结束"}, ["全部放完"]),  # 长的先认：不是"输出"
    ({"role": "输出"}, ["输出"]),
    ({"role": "ＦＩＲＳＴ＿ＲＥＳＵＬＴ"}, ["首个结果"]),  # 全角
    ({"role": "Last Result / OUTPUT"}, ["最后结果", "输出"]),
    ({"role": "", "roles": "final-result"}, ["最后结果"]),
    ({"role": ""}, []),
    ({"role": "处理消息"}, []),
])
def test_role_aliases(c, want):
    assert idf.norm_roles(c) == want


def test_aliases_are_generic_words_only():
    words = " ".join(a for xs in idf.ROLE_ALIASES.values() for a in xs).lower()
    for brand in ("xiaozhi", "esp32", "esp-idf", "cores3", "m5stack"):
        assert brand not in words
    # Compare private brand substrings by digest; the names stay outside the public fixture.
    forbidden = [(8, 'ffdd219b9eabd5f1376e103806683abcb494ac30eef33e67eae47362587eb710'), (8, '650837dfb0c7be2f6c87238d7eb5b40d47b9c15be44a787c526085bb98200658')]
    for width, digest in forbidden:
        assert all(hashlib.sha256(words[i:i + width].encode()).hexdigest() != digest
                   for i in range(len(words) - width + 1))


def test_prompt_names_the_field_and_allowed_values():
    p = idf.build_chain_prompt("从说完话到听到回复", "ESP32-S3", "语音对话", [])
    assert "字段名就叫 role" in p and "上传首包、上传末包、首个结果、最后结果、输出、全部放完" in p and "不用英文" in p


def _door_with(roles):
    d = copy.deepcopy(DOOR_RESULT)
    for c in d["chain"]:
        c.pop("role", None)
    for n, v in roles.items():
        d["chain"][n - 1].update(v)
    return d


def test_english_roles_under_roles_pass_without_follow_up(tmp_path):
    root = door_source(tmp_path / "door")
    d = _door_with({4: {"roles": ["first_upload", "last_upload"]}, 5: {"roles": "First Result"}, 6: {"role": "final_result"}})
    m = FakeLLM(reply_for(d), followups=[])
    r = idf.identify(m, root, "从刷卡到开门")
    assert r["ok"], r.get("message")
    assert not m.asked and not [x for x in r["followups"] if x["stage"] == "理清流程"]
    roles = {c["n"]: c["role"] for c in r["flow"]["chain"] if c["role"]}
    assert roles == {4: ["上传首包", "上传末包"], 5: ["首个结果"], 6: ["最后结果"]}


def test_chinese_synonyms_pass(tmp_path):
    root = door_source(tmp_path / "door")
    d = _door_with({4: {"role": "首包上传、最后一包"}, 5: {"role": "第一个结果"}, 6: {"role": "最终结果"}})
    r = idf.identify(FakeLLM(reply_for(d), followups=[]), root, "从刷卡到开门")
    assert r["ok"], r.get("message")


def _good_voice_door():
    d = copy.deepcopy(DOOR_RESULT)
    d["doubts"] = d["doubts"] + ["没有全部放完的信号：找过开锁、亮灯那几个文件", "没有识别文字的消息"]
    return d


def test_like_8790_all_roles_missing_and_no_done_is_asked_once_then_ok(tmp_path):
    """8790 那次的样子：链的位置全对，角色一个都没认出来，也没有全部放完（语音类）：先追问一次，补好了就接着往下。"""
    root = door_source(tmp_path / "door")
    m = FakeLLM(reply_for(_door_with({})), followups=[reply_for(_good_voice_door())])
    r = idf.identify(m, root, "从刷卡到开门", category="语音对话")
    assert r["ok"], r.get("message")
    assert len(m.asked) == 1 and "不要再读源码" in m.asked[0]
    assert "在那一步的 role 里补标全部放完" in m.asked[0] and "链里没标出：上传首包、上传末包、首个结果、最后结果" in m.asked[0]
    assert [(x["stage"], x["kind"], x["ok"]) for x in r["followups"]][:1] == [("理清流程", "fix", True)]


def test_missing_done_alone_gets_a_fix(tmp_path):
    root = door_source(tmp_path / "door")
    d = copy.deepcopy(DOOR_RESULT)
    d["doubts"] = d["doubts"] + ["没有识别文字的消息"]
    fixes = []
    _, bad = idf.check_chain(root, d, "语音对话", fixes)
    assert len(bad) == 1 and "全部放完" in bad[0]
    assert {f["bad"] for f in fixes} == set(bad) and "补标全部放完" in fixes[0]["text"]


@pytest.mark.parametrize("break_it, word", [
    (lambda d: d.update(trigger=""), "trigger"),
    (lambda d: d["mode"].update(what=""), "mode.what"),
    (lambda d: d["detect"].update(kind=""), "detect.kind"),
    (lambda d: d["chain"][2].pop("after"), "第 3 步补上"),
    (lambda d: d["chain"][3].update(n=3), "重新编号"),
])
def test_other_askable_problems_all_get_a_fix(tmp_path, break_it, word):
    root = door_source(tmp_path / "door")
    d = copy.deepcopy(DOOR_RESULT)
    break_it(d)
    fixes = []
    _, bad = idf.check_chain(root, d, "", fixes)
    assert bad and {f["bad"] for f in fixes} >= set(bad), (bad, fixes)
    assert any(word in f["text"] for f in fixes)


def test_still_wrong_after_follow_up_fails_with_what_is_missing(tmp_path):
    root = door_source(tmp_path / "door")
    m = FakeLLM(reply_for(_door_with({})), followups=[reply_for(_door_with({}))])
    r = idf.identify(m, root, "从刷卡到开门", category="语音对话")
    assert not r["ok"] and r["kind"] == "bad_chain" and len(m.asked) == 1
    assert "追问过一次" in r["message"] and "还缺：" in r["message"] and "全部放完" in r["message"] and "重试" in r["message"]
    assert r["chain_reply"] and r["problems"]


# ---------- 3 失败留底 ----------

def test_save_failed_scrubs_keys(tmp_path):
    r = {"ok": False, "kind": "bad_chain", "message": "m", "stage": "chain", "problems": ["缺全部放完"],
         "chain_reply": f"原话 {SK}abcdefghijklmnopqrstu 还有 api_key=ABCDEFGHIJKLMNOP 和 MYSECRETKEY123456", "followups": []}
    f = idf.save_failed(tmp_path / "proj", r, keys=("MYSECRETKEY123456",), what="全程", provider="deepseek")
    assert f.parent == tmp_path / "proj" / "identify" and f.name.startswith("failed-")
    t = f.read_text(encoding="utf-8")
    d = json.loads(t)
    assert d["problems"] == ["缺全部放完"] and d["what"] == "全程" and d["provider"] == "deepseek" and "原话" in d["chain_reply"]
    assert SK + "abcdefghijklmnopqrstu" not in t and "ABCDEFGHIJKLMNOP" not in t and "MYSECRETKEY123456" not in t
    assert idf.save_failed(tmp_path / "proj", {"ok": True}) is None and idf.save_failed(None, r) is None
    assert idf.save_failed(tmp_path / "proj", r) != f  # 同一秒再失败一次：不覆盖上一份


def test_api_failure_is_saved_and_logged(tmp_path, capfd):
    secret = SK + "TESTONLY0123456789abcdef"
    bad = _door_with({})
    bad["summary"] = f"读完了（{secret}）"
    m = FakeLLM(reply_for(bad), followups=[reply_for(bad)])
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: m,
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [], idf_finder=lambda: []))
    c.post("/api/settings", json={"llm_provider": "cc"})
    src = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/identify/start", json={"work": src["work"], "project_dir": src["project_dir"], "fingerprint": src["fingerprint"],
                                        "what": "从刷卡到开门"})
    t = time.time()
    while (j := c.get("/api/identify").json())["state"] == "running" and time.time() - t < 10:
        time.sleep(0.05)
    assert j["state"] == "failed" and j["kind"] == "bad_chain" and "chain_reply" not in j
    f = j["failed_log"]
    assert f.startswith(src["project_dir"]) and "/identify/failed-" in f
    d = json.loads(open(f, encoding="utf-8").read())
    assert d["problems"] and "读完了" in d["chain_reply"] and secret not in json.dumps(d, ensure_ascii=False)
    err = capfd.readouterr().err
    assert "[识别] 失败（bad_chain" in err and f in err and secret not in err
