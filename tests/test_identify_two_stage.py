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
    return {k: copy.deepcopy(DOOR_RESULT[k]) for k in ("trigger", "upload", "mode", "detect", "chain")}


def voice_flow():
    return {
        "trigger": "喊唤醒词后连续对话", "upload": "无（这个小例子只看下行）",
        "mode": {"what": "协议由服务器下发配置决定", "file": "main/chat.cc", "line": 4,
                 "code": "if (!protocol_->OpenAudioChannel()) {", "config": "无相关开关",
                 "protocol": {"what": "服务器下发的通道", "file": "main/chat.cc", "line": 4, "code": "if (!protocol_->OpenAudioChannel()) {"}},
        "detect": {"kind": "收到服务器消息（这个小例子只看下行）", "found": True},
        "chain": [
            {"n": 1, "what": "收到服务器消息", "file": "main/chat.cc", "line": 18, "code": "auto type = json_get(root, \"type\");",
             "func": "Init 里交给 OnIncomingJson 的回调", "branch": "", "repeat": "repeat", "after": ""},
            {"n": 2, "what": "切到说话态", "file": "main/chat.cc", "line": 21, "code": "SetState(kSpeaking);",
             "func": "Schedule 的 lambda", "role": "首个结果", "branch": "type 是 tts", "repeat": "once", "after": "Schedule 里排队，主循环执行到才切（代码第 20 行）"},
            {"n": 3, "what": "显示回复文字", "file": "main/chat.cc", "line": 24, "code": "ShowText(root);",
             "func": "Chat::Init", "role": "最后结果", "branch": "type 是 sentence", "repeat": "once", "after": "不确定"},
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
    assert "2. 切到说话态｜main/chat.cc:21 `SetState(kSpeaking);`｜函数 Schedule 的 lambda｜标为：首个结果｜分支：type 是 tts｜一轮一次" in second
    assert "排在上一步后面：不确定" in second and "模式和协议：协议由服务器下发配置决定（main/chat.cc:4" in second
    assert r["chain"] == r["flow"]["chain"] and [c["n"] for c in r["chain"]] == [1, 2, 3]
    s3 = r["steps"][2]
    assert s3["sure"] == "低" and not s3["struct_ok"] and any("理清的流程里没有第 9 步" in c["text"] for c in s3["chk"])
    assert r["strict"] is True
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


# ---------- 第一步：每一步写的函数要对（按大括号层级找包住这一行的函数） ----------
C_FILES = {"main/key.c": """#include "key.h"

static void on_release(int pin)
{
    if (pin == KEY_PIN) {
        send_event(EV_DONE);
    }
}

void key_task(void *arg)
{
    while (1) {
        int pin = wait_key();
        on_release(pin);
    }
}
"""}


def test_function_of_each_step_is_checked(voice, tmp_path):
    f = voice_flow()
    f["chain"].append({"n": 4, "what": "开通道失败回待机", "file": "main/chat.cc", "line": 5, "code": "SetState(kIdle);",
                       "func": "Init", "repeat": "once", "after": "只为测函数核对"})
    _, bad = idf.check_chain(voice, f)
    assert any("在函数 OnWake 里，不在写的「Init」里" in b and "第 16 行开始" in b for b in bad), bad
    f["chain"][3]["func"] = "Chat::OnWake"
    got, bad = idf.check_chain(voice, f)
    assert bad == [] and got["tips"] == []  # 两个 lambda 里的步骤写的是交给谁的、所在函数：都放行
    f["chain"][2]["func"] = "HandleSpeak"  # lambda 里写了别的名字：判断不了，放行并提醒
    got, bad = idf.check_chain(voice, f)
    assert bad == [] and any("lambda" in t for t in got["tips"])
    root = write(tmp_path / "key", C_FILES, 'CONFIG_IDF_TARGET="esp32"\n')
    flow = {"trigger": "按键", "upload": "无", "mode": {"what": "无"}, "detect": {"kind": "按键松开", "found": True},
            "chain": [{"n": 1, "what": "按键松开", "file": "main/key.c", "line": 14, "code": "on_release(pin);", "func": "key_task",
                       "repeat": "repeat", "role": "首个结果"},
                      {"n": 2, "what": "发出事件", "file": "main/key.c", "line": 6, "code": "send_event(EV_DONE);", "func": "key_task",
                       "repeat": "once", "role": "最后结果", "after": "第 14 行调用它"}]}
    _, bad = idf.check_chain(root, flow)
    assert any("在函数 on_release 里" in b for b in bad), bad
    flow["chain"][1]["func"] = "on_release"
    assert idf.check_chain(root, flow)[1] == []
    _, bad = idf.check_chain(door_source(tmp_path / "door2"), door_flow())  # 找不到函数（这个例子全是零散语句）：放行
    assert bad == []


# ---------- 第一步：上传首末包、首个结果、最后结果要在链里标出来 ----------
def test_roles_must_be_marked(door, voice):
    f = door_flow()
    f["chain"][3]["role"] = ""
    _, bad = idf.check_chain(door, f)
    assert any("没标出：上传首包、上传末包" in b for b in bad), bad
    f["upload"] = "无"  # 确实不往外发数据：不要上传两项
    assert idf.check_chain(door, f)[1] == []
    f = voice_flow()
    f["chain"][2]["role"] = ""
    _, bad = idf.check_chain(voice, f)
    assert any("没标出：最后结果" in b for b in bad), bad


# ---------- 第二步：点只能从链里来，插在那一行或它所在的那一支；判断或循环开头之前算失败（只对两步识别的新结果） ----------
def _pick(root, steps, strict=True):
    from fsw import structure
    chain = idf.check_chain(root, voice_flow())[0]["chain"]
    for s in steps:
        s.setdefault("loc_ok", True)
    structure.check(root, steps, chain=chain, strict=strict)
    return [[c["kind"] for c in s["chk"] if c["level"] == "fail"] for s in steps]


def test_points_must_come_from_the_chain(voice):
    mk = lambda **k: {"id": "P", "file": "main/chat.cc", "where": "before", "once": "once", **k}
    got = _pick(voice, [mk(line=21, chain_step=2), mk(line=21), mk(line=21, chain_step=7),
                        mk(line=24, chain_step=2), mk(line=13, chain_step=1), mk(line=18, chain_step=1)])
    assert got == [[], ["chain"], ["chain"], ["chain"], ["chain"], []]
    assert _pick(voice, [mk(line=21), mk(line=24, chain_step=2)], strict=False) == [[], []]  # 旧结果：不管


def test_if_or_loop_head_as_reference_fails_only_for_new_results(voice):
    mk = lambda: [{"id": "P", "file": "main/chat.cc", "line": 19, "where": "before", "once": "once", "chain_step": 1}]
    new, old = mk(), mk()
    assert _pick(voice, new) == [["anchor"]]
    assert _pick(voice, old, strict=False) == [[]] and [c["kind"] for c in old[0]["chk"]] == ["anchor"]


def test_strict_flag_survives_the_cache_and_old_cache_stays_loose(tmp_path, door):
    proj = tmp_path / "proj"
    m = FakeLLM(DOOR_REPLY)
    r = idf.identify_cached(m, door, "从刷卡到开门", proj, "fp", "deepseek")
    assert r["ok"] and r["strict"] is True and all(s["struct_ok"] for s in r["steps"])
    f = next((proj / "identify").glob("*.json"))
    saved = json.loads(f.read_text(encoding="utf-8"))
    saved["steps"][1]["chain_step"] = None
    f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    r = idf.identify_cached(None, door, "从刷卡到开门", proj, "fp", "deepseek")
    assert r["reused"] and not r["steps"][1]["struct_ok"]
    saved.pop("strict")
    f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    r = idf.identify_cached(None, door, "从刷卡到开门", proj, "fp", "deepseek")
    assert r["reused"] and r["steps"][1]["struct_ok"]


# ---------- 第一步按设备类别加提醒；类别在第一步之前就要有（不调用大模型） ----------
def test_category_hint_goes_into_the_first_prompt_only_for_known_categories():
    voice = idf.build_chain_prompt("全程", "", "语音对话")
    card = idf.build_chain_prompt("全程", "", "门禁")
    plain = idf.build_chain_prompt("全程", "")
    assert idf.VOICE_HINT in voice and idf.CARD_HINT not in voice
    assert idf.CARD_HINT in card and idf.VOICE_HINT not in card
    assert plain == idf.build_chain_prompt("全程", "", "其他") == idf.build_chain_prompt("全程", "", "报警")
    for key in ["连续对话的第二轮", "第二轮从输入结束检测（静音、说话结束）开始，不从触摸或按键开始", "只有代码表明只能手动聆听时",
                "上传的首包和末包", "收到识别出的文字", "回复开始和回复结束这一对消息", "第一段和最后一段声音播出", "播放全部放完",
                "不用逐包、逐帧执行的那一行"]:
        assert key in voice, key
    for key in ["第二次刷卡", "察觉有卡、读出卡号、校验（本地比对或发给服务器）、继电器或门锁动作、声音或指示灯反馈"]:
        assert key in card, key
    for h in (idf.VOICE_HINT, idf.CARD_HINT):
        for bad in ["小智", "xiaozhi", ".cc", ".c ", "main/", "Application", "rc522"]:
            assert bad not in h, bad


def test_category_is_guessed_before_the_first_call(tmp_path, door, voice):
    assert idf.guess_category(door) == ("刷卡终端", "源码里的词")
    assert idf.guess_category(voice) == ("", "")  # 词太少：拿不准就不猜
    talk = write(tmp_path / "talk", {"main/a.cc": "void f() { opus_encode(); vad_on(); send_tts(); on_stt(); wake_word(); }\n"}, "")
    assert idf.guess_category(talk)[0] == "语音对话"
    proj = tmp_path / "proj"
    (proj / "identify").mkdir(parents=True)
    (proj / "identify" / "x.json").write_text(json.dumps({"category": "门禁"}), encoding="utf-8")
    assert idf.guess_category(talk, proj) == ("门禁", "上次识别的结果")  # 上次 AI 认出的类别优先
    (proj / "identify" / "x.json").write_text("坏了", encoding="utf-8")
    assert idf.guess_category(talk, proj)[0] == "语音对话"  # 坏文件不崩


def test_identify_cached_puts_the_hint_into_the_first_prompt(tmp_path, door):
    m = FakeLLM(DOOR_REPLY)
    r = idf.identify_cached(m, door, "从刷卡到开门", tmp_path / "proj", "fp", "deepseek")
    assert r["ok"] and r["chain_category"] == "刷卡终端"
    assert idf.CARD_HINT in m.prompts[0] and idf.CARD_HINT not in m.prompts[1]


# ---------- 中间检查：「once（每次翻转）」这种带说明的写法认得；有问题的步骤上标的角色照样算 ----------
def test_repeat_with_a_note_and_roles_on_bad_steps(door):
    f = door_flow()
    f["chain"][0]["repeat"] = "repeat（每 0.1 秒轮询）"
    f["chain"][1]["repeat"] = "once（每次刷卡一次）"
    f["chain"][2]["repeat"] = "反复执行"
    got, bad = idf.check_chain(door, f)
    assert bad == [] and [c["repeat"] for c in got["chain"][:3]] == ["repeat", "once", "repeat"]
    f["chain"][3]["repeat"] = "看情况"  # 标了上传首末包的这一步没写清：只报这一处，不误报角色没标
    _, bad = idf.check_chain(door, f)
    assert any("第 4 步没写：是否反复执行" in b for b in bad) and not any("没标出" in b for b in bad), bad


# ---------- 误拦修正：顺带的线不要求在链里；链里那一步在延后执行的回调里时，登记回调的那一支也算 ----------
def test_side_line_points_need_not_be_in_the_chain(voice):
    mk = lambda **k: {"id": "S", "file": "main/chat.cc", "where": "before", "once": "once", **k}
    got = _pick(voice, [mk(line=24, line_of="side"), mk(line=24, line_of="side", chain_step=1), mk(line=24, line_of="main")])
    assert got == [[], [], ["chain"]]


def test_registering_branch_of_a_deferred_callback_counts_as_the_same_branch(voice):
    mk = lambda **k: {"id": "P", "file": "main/chat.cc", "where": "before", "once": "once", "chain_step": 2, **k}
    # 第 2 步（第 21 行）在交给 Schedule 的 lambda 里；第 20 行登记它，在同一个 tts 分支里：算同一支
    # 第 24 行在另一个分支（sentence）里：照旧算不在
    assert _pick(voice, [mk(line=20), mk(line=21), mk(line=24)]) == [[], [], ["chain"]]


def test_no_lambda_no_change(tmp_path):
    from fsw import structure
    root = write(tmp_path / "k", C_FILES, "")
    src = structure.Project(root).file("main/key.c")
    assert structure.in_step_area(src, 6, src.pos(6, "before"))
    assert not structure.in_step_area(src, 6, src.pos(14, "before"))  # 链那一步在函数 on_release 的分支里，点在另一个函数


def test_new_hint_and_pick_lines():
    v = idf.build_chain_prompt("全程", "", "语音对话")
    for key in ["协议里有回复开始和回复结束这一对消息的，两条都要进链（本地合成、一次性返回的没有就不写）",
                "上传首包、末包写真正发出数据的那一句，不写循环头、不写判断行；每一包都走同一句发送的，这一句同时标上传首包、上传末包",
                "声音全部播完，这一轮就结束；之后恢复聆听、开始下一轮都不进链"]:
        assert key in v, key
    p = idf.build_prompt("全程", "", flow={"chain": []})
    assert "条件成立才执行的那一句（比如调用回调、发出数据），点写在 if 大括号里的第一句，不写在 if 那一行" in p
    assert "这条线的点可以不在事件链里" in p
    for bad in ["小智", "xiaozhi", "Application", "afe", "Schedule", "OnIncoming", "websocket"]:
        assert bad not in idf.VOICE_HINT and bad not in idf.CARD_HINT, bad


# ---------- 同一句发送同时标上传首包、上传末包（第一次和最后一次执行），不用猜哪一包是最后一包 ----------
def test_one_send_step_can_be_both_first_and_last_upload(door):
    f = door_flow()
    for c in f["chain"]:
        c["role"] = "、".join(r for r in idf.ROLES if r in str(c.get("role") or "") and not r.startswith("上传"))
    f["chain"][3]["role"] = "上传首包、上传末包"  # 一句发送反复执行：第一次、最后一次
    f["chain"][3]["repeat"] = "repeat"
    f["doubts"] = ["哪一包是最后一包，代码里没有标记"]  # 写了拿不准也不冲突
    got, bad = idf.check_chain(door, f)
    assert bad == [] and set(got["chain"][3]["role"]) == {"上传首包", "上传末包"}
    f["chain"][3]["role"] = "上传首包"
    assert any("没标出：上传末包" in b for b in idf.check_chain(door, f)[1])


def test_prompts_explain_first_and_last_on_one_send_statement():
    c = idf.build_chain_prompt("全程")
    assert "逐包反复执行的同一句发送，可以同时标上传首包、上传末包（指这一句一轮里第一次和最后一次执行），下一步用 first、last 区分，不用猜哪一包是最后一包；首包、末包只标在一步上（真正发出数据的那一句；外层一句和它调用的那一句算同一次发送）。" in c
    assert "不要把两步都标成末包" not in idf.build_chain_prompt("全程", "", "语音对话")
    p = idf.build_prompt("全程", "", flow={"chain": []})
    assert "同一步同时标了上传首包和上传末包的，在这一步放两个点：一个 once 写 first，一个写 last；" in p
    assert "同一句只放一个点（上面第 2 条的首包、末包除外）" in p


def test_two_points_first_and_last_on_one_repeated_step_pass_the_check(voice):
    pts = [{"id": "P1", "chain_step": 1, "file": "main/chat.cc", "line": 18, "where": "before", "once": "first", "line_of": "main"},
           {"id": "P2", "chain_step": 1, "file": "main/chat.cc", "line": 18, "where": "before", "once": "last", "line_of": "main"}]
    assert _pick(voice, pts) == [[], []]


# ---------- 发送或调用本身写在 if 条件里：只能放在这一行前面，不算硬失败（只限流程里这一步就是这一行） ----------
def test_call_inside_if_condition_on_the_chain_line_is_allowed(tmp_path):
    from fsw import structure
    voice = tmp_path
    (voice / "main").mkdir()
    (voice / "main/chat.cc").write_text("void Send(Packet p) {\n    log(1);\n    // 发出去\n    if (!protocol_->SendAudio(p)) {\n        return;\n    }\n}\n"
                                        + "\n" * 10 + "void Other() {\n    done();\n}\n", encoding="utf-8")
    line4 = open(voice / "main/chat.cc", encoding="utf-8").read().splitlines()[3].strip()
    assert structure.call_in_if(line4), line4  # if (!protocol_->SendAudio(p)) {
    chain = [{"n": 1, "file": "main/chat.cc", "line": 4}, {"n": 2, "file": "main/chat.cc", "line": 18}]
    pt = lambda n: [{"id": "P1", "chain_step": n, "file": "main/chat.cc", "line": 4, "where": "before", "once": "once",
                     "line_of": "main", "loc_ok": True}]
    s = pt(1)
    structure.check(voice, s, chain=chain, strict=True)
    assert [c["level"] for c in s[0]["chk"] if c["kind"] == "anchor"] == ["tip"] and "动作写在 if 条件里" in s[0]["chk"][0]["text"]
    s = pt(2)  # 流程里那一步不是这一行：照旧算失败
    structure.check(voice, s, chain=chain, strict=True)
    assert "fail" in [c["level"] for c in s[0]["chk"] if c["kind"] in ("anchor", "chain")]
    for code, want in [("if (ready && cb_) {", False), ("} else if (strcmp(a, b) == 0) {", False),
                       ("while (auto p = pop()) {", False), ("if (!send(x)) {", True), ("if (sizeof(x) > 2) {", False),
                       ("if (!IsPlaybackDrainedLocked() || drained_) {", False), ("if (!ch->Open()) {", False),
                       ("if (cJSON_IsString(item)) {", False), ("if (is_ready(dev)) {", False), ("if (GetState(dev) == 2) {", False),
                       ("if (uart_write_bytes(port, buf, n) < 0) {", True), ("if (xQueueSend(q, &m, 0) != pdTRUE) {", True)]:
        assert structure.call_in_if(code) is want, code


def test_prompts_say_results_go_inside_the_branch_not_the_dispatch_entry():
    c = idf.build_chain_prompt("全程")
    assert "首个结果、最后结果这类收到某种消息或某个条件成立才发生的步骤，写认出是它之后那一支里的语句（比如处理这种消息的那一支里的第一句），不写通用的消息分发入口，也不写 else if、else 那一行。" in c
    assert "\"最后\"一类优先用整段真正结束的那一句，不用逐包、逐帧执行的那一行。" in c
    p = idf.build_prompt("全程", "", flow={"chain": []})
    assert "首个结果、最后结果这类点也放在认出是这种消息之后那一支里，不放在消息分发入口。发送或调用本身写在 if 条件里的（比如 if (!send(x))），只能写 if 这一行、where 写 before。" in p
    for bad in ["小智", "xiaozhi", "Application", "afe", "OnIncoming", "websocket", "mqtt", "SendAudio", "stt"]:  # 没有固件、文件、函数名
        assert bad not in idf.CHAIN_PROMPT + idf.PICK_PROMPT + idf.VOICE_HINT, bad


# ---------- 同一次发送只在一处放 first、last：外层末 → 内层首这种负的段要标出来 ----------
SEND_SRC = """#include "net.h"

int net_write(int x);

int send_pkt(int x) {
    return net_write(x);
}

int other(int x) {
    return x + 1;
}

void run(void) {
    while (1) {
        if (!send_pkt(1)) {
            break;
        }
        other(2);
    }
}
"""


def _order(tmp_path, pts, segs, strict=True):
    from fsw import structure
    root = tmp_path / "send"
    (root / "main").mkdir(parents=True, exist_ok=True)
    (root / "main/a.c").write_text(SEND_SRC, encoding="utf-8")
    chain = [{"n": 1, "file": "main/a.c", "line": 15}, {"n": 2, "file": "main/a.c", "line": 6}, {"n": 3, "file": "main/a.c", "line": 18}]
    for p in pts:
        p.update(file="main/a.c", where="before", line_of="main", loc_ok=True)
    structure.check(root, pts, segs, chain=chain, strict=strict)
    return {p["id"]: [c["level"] for c in p["chk"] if c["kind"] == "order"] for p in pts}


def test_last_to_first_of_the_same_send_is_flagged(tmp_path):
    pts = [{"id": "P1", "chain_step": 1, "line": 15, "once": "first", "name": "外层首"},
           {"id": "P2", "chain_step": 1, "line": 15, "once": "last", "name": "外层末"},
           {"id": "P3", "chain_step": 2, "line": 6, "once": "first", "name": "内层首"},
           {"id": "P4", "chain_step": 2, "line": 6, "once": "last", "name": "内层末"}]
    segs = [{"from": "P1", "to": "P2"}, {"from": "P2", "to": "P3"}, {"from": "P3", "to": "P4"}]
    got = _order(tmp_path, pts, segs)
    assert got == {"P1": [], "P2": [], "P3": ["fail"], "P4": []}
    assert _order(tmp_path, [dict(p) for p in pts], segs, strict=False)["P3"] == ["tip"]  # 旧结果只提醒


def test_last_to_first_of_unrelated_points_is_not_flagged(tmp_path):
    pts = [{"id": "P1", "chain_step": 1, "line": 15, "once": "last", "name": "上传末包"},
           {"id": "P2", "chain_step": 3, "line": 18, "once": "first", "name": "别的事第一次"},
           {"id": "P3", "chain_step": 3, "line": 18, "once": "last", "name": "别的事最后一次"}]
    got = _order(tmp_path, pts, [{"from": "P1", "to": "P2"}, {"from": "P2", "to": "P3"}])
    assert got == {"P1": [], "P2": [], "P3": []}


# ---------- "全部放完"单独一步：语音类必须有，或在 doubts 里写明没有这种信号 ----------
def test_voice_category_needs_a_done_step_or_a_doubt(voice):
    f = voice_flow()
    _, bad = idf.check_chain(voice, f, "语音对话")
    assert any("没有「全部放完」这一步" in b for b in bad), bad
    assert idf.check_chain(voice, f)[1] == []  # 没认出类别：不要求
    g = voice_flow()
    g["chain"][2]["role"] = "最后结果、全部放完"
    got, bad = idf.check_chain(voice, g, "语音对话")
    assert bad == [] and "全部放完" in got["chain"][2]["role"]
    h = voice_flow()
    h["doubts"] = ["没有全部放完的信号：找过播放队列和输出任务"]
    assert idf.check_chain(voice, h, "语音对话")[1] == []


def test_card_category_does_not_require_done_step(door):
    assert idf.check_chain(door, door_flow(), "门禁")[1] == []


def test_prompts_for_one_send_one_mark_and_done_step():
    c = idf.build_chain_prompt("全程")
    assert "首包、末包只标在一步上（真正发出数据的那一句；外层一句和它调用的那一句算同一次发送）。" in c
    assert ("代码里有输出或播放队列放空、动作完成这类\"全部放完\"信号的，单独列一步，写判断出放完的那一支里的语句（不写检查是否放完的入口），role 标全部放完；"
            "最后结果标在它和结束消息里更晚的那一步，说不准先后就两步都标；找不到这种信号，在 doubts 里写明\"没有全部放完的信号\"和找过哪里。") in c
    assert "播放全部放完（播放队列放空的那一支）" in idf.VOICE_HINT
    assert "门锁动作或提示音有\"真正做完\"信号的（比如动作到位、提示音放完），这一步标全部放完。" in idf.CARD_HINT
    p = idf.build_prompt("全程", "", flow={"chain": []})
    assert "链里标了上传首包、上传末包、首个结果、最后结果、全部放完的步骤都要有点" in p
    assert "上传首包、末包只放在标了它们的那一步。" in p
    assert "全部放完的点放在判断出放完的那一支里，不放在检查状态的入口。" in p


# ---------- mode 分两样写：模式那一行、协议或通道那一行（或在 doubts 里说明） ----------
def test_mode_needs_its_own_protocol_line_or_a_doubt(voice):
    f = voice_flow()
    del f["mode"]["protocol"]
    _, bad = idf.check_chain(voice, f)
    assert any("没写决定协议或通道的那一行" in b for b in bad), bad
    f["mode"]["protocol"] = {"what": "无"}  # 只有一种
    assert idf.check_chain(voice, f)[1] == []
    del f["mode"]["protocol"]
    f["doubts"] = ["走哪种协议由服务器下发的配置决定，源码里定不下来"]
    assert idf.check_chain(voice, f)[1] == []
    g = voice_flow()
    g["mode"]["protocol"]["line"] = 30  # 行号对不上
    assert any("决定协议或通道的那一行" in b for b in idf.check_chain(voice, g)[1])
    assert idf.check_chain(voice, voice_flow())[0]["mode"]["protocol"]["line"] == 4


def test_door_mode_none_needs_no_protocol(door):
    assert idf.check_chain(door, door_flow())[1] == []


def test_output_role_is_kept(voice):
    f = voice_flow()
    f["chain"][1]["role"] = "首个结果、输出"
    got, bad = idf.check_chain(voice, f)
    assert bad == [] and got["chain"][1]["role"] == ["首个结果", "输出"]


# ---------- 先后说不准的两步不串成一段 ----------
def _uncertain_check(tmp_path, chain_extra, segs):
    from fsw import structure
    root = tmp_path / "send"
    (root / "main").mkdir(parents=True, exist_ok=True)
    (root / "main/a.c").write_text(SEND_SRC, encoding="utf-8")
    chain = [{"n": 1, "file": "main/a.c", "line": 15, "role": ["最后结果"], "after": ""},
             {"n": 2, "file": "main/a.c", "line": 18, "role": [], "after": "第 1 步之后"},
             {"n": 3, "file": "main/a.c", "line": 6, "role": [], "after": "第 2 步之后"}]
    for c in chain:
        c.update(chain_extra.get(c["n"], {}))
    pts = [{"id": f"P{c['n']}", "chain_step": c["n"], "file": "main/a.c", "line": c["line"], "where": "before", "once": "once",
            "line_of": "main", "loc_ok": True, "name": f"第{c['n']}步"} for c in chain]
    structure.check(root, pts, segs, chain=chain, strict=True)
    return {p["id"]: [c["level"] for c in p["chk"] if c["kind"] == "order"] for p in pts}


def test_two_last_result_steps_are_not_chained(tmp_path):
    segs = [{"from": "P1", "to": "P2"}, {"from": "P2", "to": "P3"}]
    assert _uncertain_check(tmp_path, {2: {"role": ["最后结果"]}}, segs)["P2"] == ["fail"]
    assert _uncertain_check(tmp_path, {}, segs) == {"P1": [], "P2": [], "P3": []}
    # 放空和它引起的收尾：只有一头标最后结果，有因果先后，不拦
    assert _uncertain_check(tmp_path, {1: {"role": ["全部放完"]}, 2: {"role": ["最后结果"]}}, segs)["P2"] == []


def test_step_with_uncertain_order_is_not_chained_to_the_previous_step(tmp_path):
    extra = {3: {"after": "排序不确定：两条消息谁先到取决于服务器"}}
    assert _uncertain_check(tmp_path, extra, [{"from": "P2", "to": "P3"}])["P3"] == ["fail"]
    assert _uncertain_check(tmp_path, extra, [{"from": "P1", "to": "P3"}])["P3"] == []  # 从更早的同一个点量起：可以


def test_prompts_for_uncertain_order_output_and_two_mode_lines():
    c = idf.build_chain_prompt("全程")
    assert "运行时走哪种模式（比如聆听、工作模式）、哪种协议或通道：两样分别找到真正决定它的那一行代码，再查 sdkconfig、sdkconfig.defaults* 或 Kconfig 的取值，写进 mode 和 mode.protocol；定不下来的写进 doubts。" in c
    assert "和上一步谁先谁后说不准的，after 写\"不确定\"。" in c
    assert "真正把输出送出去的那一句（比如把声音写进扬声器、驱动执行器）单独列一步，role 标输出：首段、末段输出用它的第一次和最后一次执行，不用入队那句代替。" in c
    assert '"protocol": {"what": "哪种协议或通道；只有一种的写：无"' in c
    p = idf.build_prompt("全程", "", flow={"chain": []})
    assert "链里先后写着\"不确定\"的、两步都标了最后结果的，不要串成前后相连的段：各自从前面同一个点量起，写进 doubts。" in p
    assert "标了输出的那一步也放 first、last 两个点。" in p


# ---------- 只查状态的调用写在 if 条件里不算动作；首包末包只标一步；抄错原文追问一次 ----------
def test_query_call_in_if_is_still_a_hard_failure(tmp_path):
    from fsw import structure
    (tmp_path / "main").mkdir()
    (tmp_path / "main/a.cc").write_text("void Loop() {\n    lock();\n    if (!IsDrained() || notified_) {\n        return;\n    }\n    notify();\n}\n", encoding="utf-8")
    chain = [{"n": 1, "file": "main/a.cc", "line": 3}]
    s = [{"id": "P1", "chain_step": 1, "file": "main/a.cc", "line": 3, "where": "before", "once": "once", "line_of": "main", "loc_ok": True}]
    structure.check(tmp_path, s, chain=chain, strict=True)
    assert "fail" in [c["level"] for c in s[0]["chk"] if c["kind"] in ("anchor", "chain")]


def test_upload_roles_on_two_steps_fail_the_mid_check(voice):
    flow = voice_flow()
    flow["upload"] = "发音频包"
    flow["chain"][1]["role"] = "上传首包"
    flow["chain"][2]["role"] = "上传末包"
    fixes = []
    got, bad = idf.check_chain(voice, flow, "", fixes)
    assert any("只能标在一步上" in b for b in bad), bad
    assert any("只留一步" in f["text"] for f in fixes)


def _mistyped():
    d = copy.deepcopy(DOOR_RESULT)
    code = d["chain"][1]["code"]
    d["chain"][1]["code"] = code[:-2] + "x" + code[-1:]
    return d


def test_code_mismatch_gets_one_follow_up_with_the_real_line(door):
    d = _mistyped()
    m = FakeLLM(js(d), followups=[DOOR_REPLY])
    r = idf.identify(m, door, "从刷卡到开门")
    assert r["ok"], r.get("message")
    assert [(x["stage"], x.get("kind"), x["ok"]) for x in r["followups"]] == [("理清流程", "fix", True)]
    assert r["calls"] == 2 and len(m.asked) == 1
    c = DOOR_RESULT["chain"][1]
    real = (door / c["file"]).read_text(encoding="utf-8").splitlines()[c["line"] - 1].strip()
    assert real in m.asked[0] and d["chain"][1]["code"] in m.asked[0] and "不要再读源码" in m.asked[0]


def test_code_mismatch_follow_up_only_once_then_stop(door):
    m = FakeLLM(js(_mistyped()), followups=[js(_mistyped()), DOOR_REPLY])
    r = idf.identify(m, door, "从刷卡到开门")
    assert not r["ok"] and r["kind"] == "bad_chain" and len(m.asked) == 1 and r["calls"] == 1
    assert [(x.get("kind"), x["ok"]) for x in r["followups"]] == [("fix", False)]


def test_other_problems_mean_no_code_fix_follow_up(door):
    d = _mistyped()
    d["chain"][2].pop("func", None)
    m = FakeLLM(js(d), followups=[DOOR_REPLY])
    r = idf.identify(m, door, "从刷卡到开门")
    assert not r["ok"] and r["kind"] == "bad_chain" and not m.asked and not r["followups"]


# ---------- 标了最后结果、全部放完的点要有一段量到它；先把输入追到真正发出去的那一句 ----------
def _end_project(tmp_path):
    from fsw import structure
    (tmp_path / "main").mkdir()
    (tmp_path / "main/a.c").write_text("void Run(void) {\n    begin();\n    drained();\n    finish();\n}\n", encoding="utf-8")
    chain = [{"n": 1, "file": "main/a.c", "line": 2}, {"n": 2, "file": "main/a.c", "line": 3, "role": "全部放完"},
             {"n": 3, "file": "main/a.c", "line": 4, "role": "最后结果"}]
    pts = [{"id": f"P{i}", "chain_step": i, "name": f"第{i}步", "file": "main/a.c", "line": i + 1, "where": "before", "once": "once",
            "line_of": "start" if i == 1 else "main", "loc_ok": True} for i in (1, 2, 3)]
    return structure, chain, pts


@pytest.mark.parametrize("strict,level", [(True, "fail"), (False, "tip")])
def test_dangling_last_result_is_flagged(tmp_path, strict, level):
    structure, chain, pts = _end_project(tmp_path)
    structure.check(tmp_path, pts, [{"from": "P1", "to": "P2", "name": "放完"}], chain=chain, strict=strict)
    got = [c for c in pts[2]["chk"] if c["kind"] == "segment"]
    assert [c["level"] for c in got] == [level] and "没有任何一段量到它" in got[0]["text"]
    assert not [c for c in pts[1]["chk"] if c["kind"] == "segment"]


def test_last_result_measured_from_a_common_earlier_point_passes(tmp_path):
    structure, chain, pts = _end_project(tmp_path)
    segs = [{"from": "P1", "to": "P2", "name": "放完"}, {"from": "P1", "to": "P3", "name": "结束"}]
    structure.check(tmp_path, pts, segs, chain=chain, strict=True)
    assert not [c for p in pts for c in p["chk"] if c["kind"] == "segment"]
    side = [{"from": "P1", "to": "P3", "name": "旁线到它也算"}]
    structure.check(tmp_path, pts, [{"from": "P1", "to": "P2"}], side, chain=chain, strict=True)
    assert not [c for p in pts for c in p["chk"] if c["kind"] == "segment"]


def test_prompts_trace_input_to_the_send_and_connect_end_points():
    c = idf.build_chain_prompt("全程")
    assert ("设备会把输入往外发的（比如声音、传感器数据、刷卡上报），先顺着输入从采集一直追到真正把它发出去的那一句（起点仍按第 2 条，不从按键、触摸这类触发动作往下追），再挑步骤；"
            "有上传却缺了上传首包、末包或首个结果的链是错的。") in c
    p = idf.build_prompt("全程", "", flow={"chain": []})
    assert "标了最后结果、全部放完的点，每个都要有一段以它为终点。" in p
    for t in (c, p, idf.VOICE_HINT, idf.CARD_HINT):
        assert "xiaozhi" not in t.lower() and "application.cc" not in t


# ---------- 起点钉在设备自己的输入结束检测；选点只差插在判断开头、输出缺首末点时追问一次 ----------
def test_start_is_pinned_to_the_own_end_of_input_detection(voice):
    flow = voice_flow()
    flow["detect"] = {"kind": "按键松开", "found": True, "looked": "静音检测翻转只用来切指示灯"}
    flow["chain"][0]["what"] = "按键松开，切换对话状态"
    msg = "第 1 步却不是它"
    assert any(msg in b for b in idf.check_chain(voice, flow, "", fresh=True)[1])
    assert not any(msg in b for b in idf.check_chain(voice, flow, "")[1])  # 缓存里的旧流程不加这一条
    flow["chain"][0]["what"] = "静音检测翻转（只用来切换显示）"
    assert not any(msg in b for b in idf.check_chain(voice, flow, "", fresh=True)[1])
    door = door_flow()  # 刷卡这类：detect 不提静音，不受影响
    assert not any(msg in b for b in idf.check_chain(door_source(voice.parent / "d"), door, "", fresh=True)[1])


def test_prompt_keeps_the_start_rule_when_tracing_the_input():
    assert "先顺着输入从采集一直追到真正把它发出去的那一句（起点仍按第 2 条，不从按键、触摸这类触发动作往下追），再挑步骤；" in idf.build_chain_prompt("全程")


def _if_project(tmp_path):
    from fsw import structure
    (tmp_path / "main").mkdir(exist_ok=True)
    (tmp_path / "main/a.c").write_text("void Run(void) {\n    begin();\n    if (state == 2) {\n        finish();\n    }\n}\n", encoding="utf-8")
    chain = [{"n": 1, "file": "main/a.c", "line": 2}, {"n": 2, "file": "main/a.c", "line": 3, "role": "最后结果"}]
    pts = [{"id": "P1", "chain_step": 1, "name": "开始", "file": "main/a.c", "line": 2, "where": "before", "once": "once", "line_of": "start", "loc_ok": True},
           {"id": "P2", "chain_step": 2, "name": "结束", "file": "main/a.c", "line": 3, "where": "before", "once": "once", "line_of": "main", "loc_ok": True}]
    return structure, chain, pts


def test_if_head_anchor_gives_a_follow_up_with_the_branch_body(tmp_path):
    structure, chain, pts = _if_project(tmp_path)
    structure.check(tmp_path, pts, [{"from": "P1", "to": "P2"}], chain=chain, strict=True)
    items = structure.pick_fixes(tmp_path, pts, chain)
    assert len(items) == 1 and "第 4 行「finish();」" in items[0] and "4:         finish();" in items[0]
    pts[1]["chain_step"] = 1  # 再加一个别的硬失败（不在那一步那一支）：不追问
    pts[1]["line"] = 99
    structure.check(tmp_path, pts, [{"from": "P1", "to": "P2"}], chain=chain, strict=True)
    assert structure.pick_fixes(tmp_path, pts, chain) is None


@pytest.mark.parametrize("strict,level", [(True, "fail"), (False, "tip")])
def test_output_step_needs_first_and_last(tmp_path, strict, level):
    structure, chain, pts = _if_project(tmp_path)
    chain[0]["role"] = "输出"
    pts[1]["line"] = 4
    structure.check(tmp_path, pts, [{"from": "P1", "to": "P2"}], chain=chain, strict=strict)
    got = [c for c in pts[0]["chk"] if c["kind"] == "output"]
    assert [c["level"] for c in got] == [level] and "first、last" in got[0]["text"]
    pts[0]["once"] = "first"
    pts.append({**pts[0], "id": "P3", "once": "last", "line_of": "main"})
    structure.check(tmp_path, pts, [{"from": "P1", "to": "P3"}, {"from": "P3", "to": "P2"}], chain=chain, strict=strict)
    assert not [c for p in pts for c in p["chk"] if c["kind"] == "output"]


def _door_output(missing_last=True):
    d = copy.deepcopy(DOOR_RESULT)
    d["chain"][5]["role"] = "最后结果、输出"
    d["steps"][4]["once"] = "first"
    if not missing_last:
        d["steps"].append({**d["steps"][4], "id": "P6", "name": "开锁最后一次", "once": "last"})
        d["segments"].append({"from": "P5", "to": "P6", "name": "开锁动作"})
    return d


def test_pick_follow_up_fixes_only_those_points(door):
    m = FakeLLM(js(_door_output()), followups=[js(_door_output(False))])
    r = idf.identify(m, door, "从刷卡到开门")
    assert r["ok"] and len(m.asked) == 1 and "流程第 6 步标了输出" in m.asked[0] and "不要再读源码" in m.asked[0]
    f = [x for x in r["followups"] if x["stage"] == "选计时点"]
    assert len(f) == 1 and f[0]["kind"] == "fix" and f[0]["ok"] and f[0]["fails_before"] == 1 and f[0]["fails_after"] == 0
    assert any(s["id"] == "P6" for s in r["steps"]) and r["reply_first"] == js(_door_output())


def test_pick_follow_up_that_makes_it_worse_keeps_the_first_answer(door):
    worse = _door_output()
    worse["steps"][0]["file"] = "main/net_auth.c"
    m = FakeLLM(js(_door_output()), followups=[js(worse)])
    r = idf.identify(m, door, "从刷卡到开门")
    assert r["ok"] and len(m.asked) == 1 and not any(s["id"] == "P6" for s in r["steps"])
    assert [(x["kind"], x["ok"]) for x in r["followups"]] == [("fix", False)]
    assert r["steps"][0]["file"] == "main/rfid_task.c"


def test_no_pick_follow_up_for_wrong_file(door):
    d = _door_output(False)
    d["steps"][1]["file"] = "main/net_auth.c"
    d["steps"][1]["line"] = 180
    m = FakeLLM(js(d), followups=[DOOR_REPLY])
    r = idf.identify(m, door, "从刷卡到开门")
    assert r["ok"] and not m.asked and not r["followups"]
