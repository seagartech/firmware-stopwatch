"""识别拆成两次调用：第一次理清流程（事件链），码表检查过再第二次选计时点。门禁、语音两类都覆盖；全用假模型，不花钱。"""

import copy
import json
import re

import pytest

from fsw import identify as idf
from fsw.llm import FakeLLM
from tests.identify_fixtures import DOOR_REPLY, DOOR_RESULT, door_source, reply_for
from tests.test_structure import VOICE_FILES, write


@pytest.fixture
def door(tmp_path):
    return door_source(tmp_path / "door")


@pytest.fixture
def voice(tmp_path):
    return write(tmp_path / "voice", VOICE_FILES, 'CONFIG_IDF_TARGET="esp32s3"\n')


def door_flow():
    return {k: copy.deepcopy(DOOR_RESULT[k]) for k in ("trigger", "mode", "detect", "chain")}


def voice_flow():
    return {
        "trigger": "喊唤醒词后连续对话",
        "mode": {"what": "协议由服务器下发配置决定", "file": "main/chat.cc", "line": 4,
                 "code": "if (!protocol_->OpenAudioChannel()) {", "config": "无相关开关"},
        "detect": {"kind": "静音检测翻转（只用来切换显示也算）", "found": True},
        "chain": [
            {"n": 1, "what": "收到服务器消息", "file": "main/chat.cc", "line": 18, "code": "auto type = json_get(root, \"type\");",
             "branch": "", "repeat": "repeat", "after": ""},
            {"n": 2, "what": "切到说话态", "file": "main/chat.cc", "line": 21, "code": "SetState(kSpeaking);",
             "branch": "type 是 tts", "repeat": "once", "after": "Schedule 里排队，主循环执行到才切（代码第 20 行）"},
            {"n": 3, "what": "显示回复文字", "file": "main/chat.cc", "line": 24, "code": "ShowText(root);",
             "branch": "type 是 sentence", "repeat": "once", "after": "不确定"},
        ],
    }


def js(d):
    return "```json\n" + json.dumps(d, ensure_ascii=False) + "\n```"


# ---------- 中间检查：通过的 ----------
def test_good_flows_pass_for_door_and_voice(door, voice):
    for root, flow in ((door, door_flow()), (voice, voice_flow())):
        got, bad = idf.check_chain(root, flow)
        assert bad == [] and len(got["chain"]) == len(flow["chain"])
        assert all(c["code"] and c["repeat"] in ("once", "repeat") for c in got["chain"])


def test_line_a_few_off_is_moved_to_where_the_code_is(voice):
    f = voice_flow()
    f["chain"][1]["line"] = 23  # 原文其实在第 21 行
    got, bad = idf.check_chain(voice, f)
    assert bad == [] and got["chain"][1]["line"] == 21


# ---------- 中间检查：不合格的，说清原因 ----------
def _bad(flow_fn, change):
    f = flow_fn()
    change(f)
    return f


@pytest.mark.parametrize("which,change,why", [
    ("door", lambda f: f["chain"][1].update(code="uid_ok = something_else();"), "对不上"),
    ("voice", lambda f: f["chain"][2].update(line=999), "没有这一行"),
    ("door", lambda f: f["chain"][0].update(file="main/nope.c"), "找不到这个文件"),
    ("voice", lambda f: f["chain"][1].update(after=""), "为什么排在上一步后面"),
    ("door", lambda f: f["chain"][2].update(repeat=""), "是否反复执行"),
    ("voice", lambda f: f["chain"][0].update(code=""), "原文"),
    ("door", lambda f: f.update(detect={"kind": ""}), "哪种输入结束检测"),
    ("voice", lambda f: f.update(detect={"kind": "按键松开", "found": False}), "找过哪里"),
    ("voice", lambda f: f.update(mode={"what": "按配置选实时或自动模式"}), "真正决定它的那一行代码"),
    ("door", lambda f: f.update(mode={"what": "走网页协议", "file": "main/rfid_task.c", "line": 88, "code": "return x;"}), "决定模式和协议的那一行"),
    ("door", lambda f: f.update(trigger=""), "触发路径"),
    ("voice", lambda f: f.update(chain=f["chain"][:1]), "不到 2 步"),
])
def test_bad_flows_are_stopped_with_a_reason(door, voice, which, change, why):
    root, fn = (door, door_flow) if which == "door" else (voice, voice_flow)
    _, bad = idf.check_chain(root, _bad(fn, change))
    assert bad and any(why in b for b in bad), bad
    assert not any(re.search(r"\bP\d+\b", b) for b in bad)


def test_not_json_is_not_a_crash(door):
    assert idf.check_chain(door, None)[1] and idf.check_chain(door, {"chain": "x"})[1]


# ---------- 整个识别：不合格就停下，不自动重试 ----------
@pytest.mark.parametrize("what", ["从刷卡到开门", "从说完话到开始播放回复"])
def test_bad_flow_stops_before_the_second_call(door, what):
    f = door_flow()
    f["chain"][1]["code"] = "完全不是这一行的代码"
    m = FakeLLM(js(f))
    r = idf.identify(m, door, what)
    assert not r["ok"] and r["kind"] == "bad_chain" and r["stage"] == "chain" and r["calls"] == 1
    assert len(m.prompts) == 1 and "理清流程" in r["message"] and "对不上" in r["message"] and "重试" in r["message"]


def test_voice_flow_goes_into_the_second_prompt(voice):
    """语音：第一次给出的流程原样写进第二次的提示词，点只能从里面选。"""
    res = {"summary": "s", "start_found": True, "end_found": True, "start": {"name": "收到消息"}, "main": {"name": "播放这条", "end": "说话"},
           "steps": [{"id": "P1", "chain_step": 1, "name": "收到消息", "file": "main/chat.cc", "line": 18, "where": "before", "once": "first", "line_of": "start", "sure": "高"},
                     {"id": "P2", "chain_step": 2, "name": "切到说话态", "file": "main/chat.cc", "line": 21, "where": "before", "once": "once", "line_of": "main", "sure": "高"},
                     {"id": "P3", "chain_step": 9, "name": "乱选的", "file": "main/chat.cc", "line": 24, "where": "before", "once": "once", "line_of": "main", "sure": "高"}],
           "segments": [{"from": "P1", "to": "P2", "name": "排队切状态"}]}
    replies = iter([js(voice_flow()), reply_for(res)])

    class Two(FakeLLM):
        def investigate(self, prompt, root, on_text=None, on_tool=None):
            self.reply = next(replies)
            return super().investigate(prompt, root, on_text, on_tool)
    m = Two("")
    r = idf.identify(m, voice, "从说完话到开始播放回复")
    assert r["ok"] and r["calls"] == 2 and len(m.prompts) == 2
    second = m.prompts[1]
    assert "2. 切到说话态｜main/chat.cc:21 `SetState(kSpeaking);`｜分支：type 是 tts｜一轮一次" in second
    assert "排在上一步后面：不确定" in second and "模式和协议：协议由服务器下发配置决定（main/chat.cc:4" in second
    assert r["chain"] == r["flow"]["chain"] and [c["n"] for c in r["chain"]] == [1, 2, 3]
    s3 = r["steps"][2]
    assert s3["sure"] == "低" and "理清的流程里没有第 9 步" in s3["doubt"]
    assert r["steps"][1]["sure"] == "高"


# ---------- 两次分别缓存：第二次失败了，重试不用再理一遍 ----------
def test_second_call_fails_then_retry_reuses_the_flow(tmp_path, door):
    proj, sent = tmp_path / "proj", []

    class Flaky(FakeLLM):
        def investigate(self, prompt, root, on_text=None, on_tool=None):
            sent.append(prompt)
            if len(sent) == 2:
                raise self.fail("offline")
            return super().investigate(prompt, root, on_text, on_tool)
    m = Flaky(DOOR_REPLY)
    r1 = idf.identify_cached(m, door, "从刷卡到开门", proj, "fp", "deepseek", reuse=False)
    assert not r1["ok"] and r1["stage"] == "pick" and len(list((proj / "identify" / "flow").glob("*.json"))) == 1
    r2 = idf.identify_cached(m, door, "从刷卡到开门", proj, "fp", "deepseek", reuse=False, reuse_flow=True)
    assert r2["ok"] and r2["flow_reused"] and r2["calls"] == 1 and len(sent) == 3
    assert "这一步只理清流程" in sent[0] and "这一步只理清流程" not in sent[2]
    assert list((proj / "identify").glob("*.json"))  # 整份结果另存一份


def test_broken_or_stale_flow_cache_is_ignored(tmp_path, door):
    proj = tmp_path / "proj"
    m = FakeLLM(DOOR_REPLY)
    idf.identify_cached(m, door, "从刷卡到开门", proj, "fp", "deepseek")
    ff = next((proj / "identify" / "flow").glob("*.json"))
    for bad in ("不是 json", json.dumps({"raw": {"chain": []}}), json.dumps([1, 2]), json.dumps({"raw": "x"})):
        ff.write_text(bad, encoding="utf-8")
        m.prompts.clear()
        r = idf.identify_cached(m, door, "从刷卡到开门", proj, "fp", "deepseek", reuse=False, reuse_flow=True)
        assert r["ok"] and not r["flow_reused"] and len(m.prompts) == 2  # 存的流程用不了：重新理一遍，不崩


def test_old_single_call_cache_still_loads(tmp_path, door):
    """旧版本一次调用存下的整份结果（没有 flow、calls）：放在现在的缓存名下也能读，不崩。"""
    proj = tmp_path / "proj"
    old = idf.parse(DOOR_REPLY, door)
    for k in ("flow", "calls", "chain"):
        old.pop(k, None)
    f = idf._cache_file(proj, "fp", "从刷卡到开门", "deepseek")
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({**old, "what": "从刷卡到开门", "provider": "deepseek"}, ensure_ascii=False), encoding="utf-8")
    r = idf.identify_cached(None, door, "从刷卡到开门", proj, "fp", "deepseek")
    assert r["ok"] and r["reused"] and r["calls"] == 0 and r["page"]["steps"]


def test_web_shows_two_plain_stages():
    from pathlib import Path
    js_ = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "理清流程" in js_ and "选计时点" in js_ and "J.stage==='pick'" in js_
