"""识别后的结构检查（fsw/structure.py）：所有模型共用，门禁刷卡、DemoDisplay对话两类源码都要过。
全部用本地构造的源码，不调用大模型、不碰设备。"""

import copy
import json
import re
from pathlib import Path

import pytest

from fsw import identify as idf
from fsw import structure as st
from tests.identify_fixtures import DOOR_RESULT, door_source, reply_for

# ---------- 构造的门禁刷卡源码（C，读卡任务轮询 + 编译开关 + 网络回调里排队开锁） ----------
DOOR_FILES = {
    "main/rfid_task.c": """\
#include "rfid.h"

static void on_card_frame(const card_frame_t *frame)
{
    uid_t uid;
    if (frame->status != CARD_PRESENT) {
        return;
    }
    rc522_read_uid(frame->dev, &uid);
    auth_request(&uid);
}

static void rfid_task(void *arg)
{
    card_frame_t frame;
    for (;;) {
        rc522_poll(dev, &frame);
        on_card_frame(&frame);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void rfid_start(void)
{
    if (!rc522_init(dev))
        log_error("init");
    if (rc522_card_present(dev)) {
        led_set(LED_GREEN);
    } else if (rc522_error(dev)) {
        led_set(LED_RED);
    }
    while (rc522_busy(dev))
    {
        wait_ms(1);
    }
    switch (mode) {
    case 1:
        break;
    }
#ifdef CONFIG_DOOR_BEEP
    buzzer_beep(80);
#else
    led_blink(2);
#endif
#if DOOR_DEBUG_LEVEL > 1
    log_card();
#endif
}
""",
    "main/rfid.h": """\
#ifndef RFID_H
#define RFID_H
void rfid_start(void);
#endif
""",
    "main/net_auth.cc": """\
#include "net.h"

void NetAuth::Start() {
    client_.OnReply([this](const reply_t* reply) {
        auth_result_ = parse_auth_reply(reply);
        Post([this]() {
            lock_open();
        });
        led_set(LED_BLUE);
    });
}

void NetAuth::Retry() {
    send_again();
}
""",
}
DOOR_SDKCONFIG = 'CONFIG_IDF_TARGET="esp32s3"\nCONFIG_DOOR_BEEP=y\n'

# ---------- 构造的DemoDisplay对话源码（C++，音频逐帧处理 + 打开通道失败分支 + 消息回调里排队切状态） ----------
VOICE_FILES = {
    "main/audio_engine.cc": """\
#include "audio_engine.h"

void AudioEngine::ProcessTask() {
    while (true) {
        auto* result = fetch_frame();
        if (result == nullptr) {
            continue;
        }
        HandleFrame(result);
    }
}

void AudioEngine::HandleFrame(const frame_t* result) {
    cache_.Store(result->data);
    if (result->wake_state != WAKE_DETECTED) {
        return;
    }
    on_wake_(result->word);
}
""",
    "main/chat.cc": """\
#include "chat.h"

void Chat::OnWake() {
    if (!protocol_->OpenAudioChannel()) {
        SetState(kIdle);
        return;
    }
#if CONFIG_SEND_WAKE_DATA
    while (auto packet = PopWakePacket()) {
        protocol_->SendAudio(std::move(packet));
    }
#endif
    SetState(kListening);
}

void Chat::Init() {
    protocol_->OnIncomingJson([this](const json_t* root) {
        auto type = json_get(root, "type");
        if (is(type, "tts")) {
            Schedule([this]() {
                SetState(kSpeaking);
            });
        } else if (is(type, "sentence")) {
            ShowText(root);
        }
    });
}
""",
    "main/Kconfig.projbuild": """\
menu "Chat"
config SEND_WAKE_DATA
    bool "Send wake data"
    default y
    depends on USE_WAKE_WORD
endmenu
""",
}


def write(root, files, sdkconfig=None):
    root = Path(root)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if sdkconfig is not None:
        (root / "sdkconfig.defaults").write_text(sdkconfig, encoding="utf-8")
    return root


@pytest.fixture
def door(tmp_path):
    return write(tmp_path / "door", DOOR_FILES, DOOR_SDKCONFIG)


@pytest.fixture
def voice(tmp_path):
    return write(tmp_path / "voice", VOICE_FILES, 'CONFIG_IDF_TARGET="esp32s3"\n')


def line_of(root, rel, text):
    lines = (Path(root) / rel).read_text(encoding="utf-8").split("\n")
    return next(i for i, ln in enumerate(lines, 1) if text in ln)


def step(root, rel, text, where="", once="", sid="P1", name="某一步", **kw):
    return {"id": sid, "name": name, "file": rel, "line": line_of(root, rel, text), "where": where, "once": once,
            "loc_ok": True, "sure": "高", **kw}


def run(root, *steps, segments=None, chain=None):
    steps = [copy.deepcopy(s) for s in steps]
    st.check(root, steps, segments, None, chain)
    return steps


def kinds(s, level=None):
    return [c["kind"] for c in s["chk"] if level in (None, c["level"])]


# ---------- 硬失败：插在控制语句之后，落进分支或循环体 ----------
@pytest.mark.parametrize("rel,text", [
    ("main/rfid_task.c", "if (rc522_card_present(dev)) {"),
    ("main/rfid_task.c", "} else if (rc522_error(dev)) {"),
    ("main/rfid_task.c", "for (;;) {"),
    ("main/rfid_task.c", "switch (mode) {"),
    ("main/rfid_task.c", "while (rc522_busy(dev))"),          # 大括号在下一行
    ("main/rfid_task.c", "if (!rc522_init(dev))"),             # 没有大括号：插进去会变成 if 唯一管的那句
])
def test_door_after_control_line_is_hard_fail(door, rel, text):
    s, = run(door, step(door, rel, text, where="after"))
    assert kinds(s, "fail") == ["branch"] and s["struct_ok"] is False and s["sure"] == "低"
    assert "识别结果没有说明是有意放进这里" in s["chk"][0]["text"]


@pytest.mark.parametrize("rel,text", [
    ("main/chat.cc", "if (!protocol_->OpenAudioChannel()) {"),
    ("main/chat.cc", "while (auto packet = PopWakePacket()) {"),
    ("main/chat.cc", "} else if (is(type, \"sentence\")) {"),
])
def test_voice_after_control_line_is_hard_fail(voice, rel, text):
    s, = run(voice, step(voice, rel, text, where="after"))
    assert kinds(s, "fail") == ["branch"] and not s["struct_ok"]


# ---------- 事件链写明了是哪一支：有意放进正常分支的点不硬判失败，只提醒核对 ----------
@pytest.mark.parametrize("fx,rel,text,branch", [
    ("door", "main/rfid_task.c", "if (rc522_card_present(dev)) {", "有卡靠近"),
    ("door", "main/rfid_task.c", "} else if (rc522_error(dev)) {", "读卡芯片报错"),
    ("voice", "main/chat.cc", "} else if (is(type, \"sentence\")) {", "收到一句回复文字"),
])
def test_branch_named_in_chain_is_tip_not_fail(request, fx, rel, text, branch):
    root = request.getfixturevalue(fx)
    s, = run(root, step(root, rel, text, where="after", chain_step=2),
             chain=[{"n": 1, "branch": ""}, {"n": 2, "branch": branch}])
    assert kinds(s, "fail") == [] and kinds(s, "tip") == ["branch"]
    assert s["struct_ok"] is True and s["sure"] == "高"
    assert f"事件链写明放在「{branch}」这一支" in s["chk"][0]["text"] and "没有说明是有意" not in s["chk"][0]["text"]


@pytest.mark.parametrize("fx,rel,text,chain_step,chain", [
    ("door", "main/rfid_task.c", "for (;;) {", 1, [{"n": 1, "branch": "一直循环"}]),          # 循环体：照旧失败
    ("door", "main/rfid_task.c", "switch (mode) {", 1, [{"n": 1, "branch": "case 1"}]),        # 第一个 case 之前执行不到
    ("door", "main/rfid_task.c", "if (!rc522_init(dev))", 1, [{"n": 1, "branch": "初始化失败"}]),  # 没大括号会改逻辑
    ("voice", "main/chat.cc", "if (!protocol_->OpenAudioChannel()) {", 1, [{"n": 1, "branch": "打开失败"}]),  # 像失败分支
    ("door", "main/rfid_task.c", "if (rc522_card_present(dev)) {", 1, [{"n": 1, "branch": ""}]),  # 事件链没写分支
    ("door", "main/rfid_task.c", "if (rc522_card_present(dev)) {", 3, [{"n": 1, "branch": "有卡"}]),  # 对不上第几步
    ("door", "main/rfid_task.c", "if (rc522_card_present(dev)) {", None, [{"n": 1, "branch": "有卡"}]),  # 旧结果没有 chain_step
    ("door", "main/rfid_task.c", "if (rc522_card_present(dev)) {", 1, None),                       # 旧结果没有事件链
])
def test_branch_still_fails_without_usable_chain_branch(request, fx, rel, text, chain_step, chain):
    root = request.getfixturevalue(fx)
    s, = run(root, step(root, rel, text, where="after", chain_step=chain_step), chain=chain)
    assert kinds(s, "fail") == ["branch"] and s["struct_ok"] is False


def test_failure_condition_is_called_out(voice, door):
    s, = run(voice, step(voice, "main/chat.cc", "if (!protocol_->OpenAudioChannel()) {", where="after"))
    assert "失败或出错" in s["chk"][0]["text"]
    s, = run(door, step(door, "main/rfid_task.c", "if (rc522_card_present(dev)) {", where="after"))
    assert "失败或出错" not in s["chk"][0]["text"] and "条件成立" in s["chk"][0]["text"]
    s, = run(door, step(door, "main/rfid_task.c", "if (!rc522_init(dev))", where="after"))
    assert "改掉原来的逻辑" in s["chk"][0]["text"]


@pytest.mark.parametrize("where", ["before", "", "BEFORE"])
def test_before_control_line_or_unknown_where_is_not_a_fail(door, voice, where):
    a, = run(door, step(door, "main/rfid_task.c", "if (rc522_card_present(dev)) {", where=where))
    b, = run(voice, step(voice, "main/chat.cc", "if (!protocol_->OpenAudioChannel()) {", where=where))
    assert a["struct_ok"] and b["struct_ok"] and not kinds(a, "fail") and not kinds(b, "fail")


def test_after_plain_statement_passes(door, voice):
    a, = run(door, step(door, "main/rfid_task.c", "rc522_read_uid(frame->dev, &uid);", where="after"))
    b, = run(voice, step(voice, "main/chat.cc", "SetState(kListening);", where="after"))
    assert a["chk"] == [] and b["chk"] == [] and a["sure"] == "高"


# ---------- 提示：每次都记的点在循环里，或在循环里被反复调用的函数里 ----------
def test_every_inside_loop_is_a_tip(door, voice):
    a, = run(door, step(door, "main/rfid_task.c", "rc522_poll(dev, &frame);", where="after", once="every"))
    b, = run(voice, step(voice, "main/chat.cc", "protocol_->SendAudio(std::move(packet));", where="after", once="every"))
    for s in (a, b):
        assert "repeat" in kinds(s, "tip") and s["struct_ok"] and s["sure"] == "高"
        assert "循环" in next(c["text"] for c in s["chk"] if c["kind"] == "repeat")


def test_first_or_last_inside_loop_is_not_a_tip(door, voice):
    a, = run(door, step(door, "main/rfid_task.c", "rc522_poll(dev, &frame);", where="after", once="first"))
    b, = run(voice, step(voice, "main/chat.cc", "protocol_->SendAudio(std::move(packet));", where="after", once="last"))
    assert "repeat" not in kinds(a) and "repeat" not in kinds(b)


def test_every_in_function_called_from_a_loop_is_a_tip(door, voice):
    a, = run(door, step(door, "main/rfid_task.c", "if (frame->status != CARD_PRESENT) {", where="before", once="every"))
    b, = run(voice, step(voice, "main/audio_engine.cc", "if (result->wake_state != WAKE_DETECTED) {", where="before", once="every"))
    ta = next(c["text"] for c in a["chk"] if c["kind"] == "repeat")
    tb = next(c["text"] for c in b["chk"] if c["kind"] == "repeat")
    assert "on_card_frame" in ta and "rfid_task.c 第" in ta
    assert "HandleFrame" in tb and "audio_engine.cc 第" in tb


def test_after_a_guard_on_the_incoming_data_is_not_a_tip(door, voice):
    """点前面已经"看传进来的数据不对就 return"：不是每次调用都走到，不提示。"""
    a, = run(door, step(door, "main/rfid_task.c", "auth_request(&uid);", where="before", once="every"))
    b, = run(voice, step(voice, "main/audio_engine.cc", "on_wake_(result->word);", where="before", once="every"))
    assert "repeat" not in kinds(a) and "repeat" not in kinds(b)


def test_function_not_called_from_a_loop_is_not_a_tip(door, voice):
    a, = run(door, step(door, "main/net_auth.cc", "send_again();", where="after", once="every"))
    b, = run(voice, step(voice, "main/chat.cc", "SetState(kListening);", where="after", once="every"))
    assert a["chk"] == [] and b["chk"] == []


# ---------- 提示：编译开关 ----------
def test_door_ifdef_reads_sdkconfig_defaults(door):
    on, = run(door, step(door, "main/rfid_task.c", "buzzer_beep(80);", where="after"))
    off, = run(door, step(door, "main/rfid_task.c", "led_blink(2);", where="after"))
    t_on = next(c["text"] for c in on["chk"] if c["kind"] == "ifdef")
    t_off = next(c["text"] for c in off["chk"] if c["kind"] == "ifdef")
    assert "CONFIG_DOOR_BEEP" in t_on and "sdkconfig.defaults 里是 y" in t_on and "会编进固件" in t_on
    assert "开关条件不成立时才编进去" in t_off and "不会编进固件" in t_off
    assert on["struct_ok"] and off["struct_ok"]


def test_door_non_config_macro_says_it_cannot_tell(door):
    s, = run(door, step(door, "main/rfid_task.c", "log_card();", where="after"))
    t = next(c["text"] for c in s["chk"] if c["kind"] == "ifdef")
    assert "DOOR_DEBUG_LEVEL" in t and "判断不了" in t


def test_voice_ifdef_falls_back_to_kconfig_default(voice):
    s, = run(voice, step(voice, "main/chat.cc", "protocol_->SendAudio(std::move(packet));", where="after", once="first"))
    t = next(c["text"] for c in s["chk"] if c["kind"] == "ifdef")
    assert "CONFIG_SEND_WAKE_DATA" in t and "Kconfig 默认 y" in t and "sdkconfig 里没写" in t


def test_sdkconfig_wins_over_defaults(voice):
    (voice / "sdkconfig").write_text("# CONFIG_SEND_WAKE_DATA is not set\n", encoding="utf-8")
    s, = run(voice, step(voice, "main/chat.cc", "protocol_->SendAudio(std::move(packet));", where="after", once="first"))
    t = next(c["text"] for c in s["chk"] if c["kind"] == "ifdef")
    assert "sdkconfig 里是 n" in t and "不会编进固件" in t


def test_include_guard_and_plain_code_are_not_flagged(door, voice):
    h, = run(door, step(door, "main/rfid.h", "void rfid_start(void);", where="after"))
    v, = run(voice, step(voice, "main/chat.cc", "SetState(kListening);", where="after"))
    assert h["chk"] == [] and v["chk"] == []


# ---------- 提示：同一段两头执行时机不同 ----------
def test_voice_segment_across_direct_and_scheduled_lambda(voice):
    a = step(voice, "main/chat.cc", "auto type = json_get(root, \"type\");", where="after", sid="P1", name="收到消息")
    b = step(voice, "main/chat.cc", "SetState(kSpeaking);", where="before", sid="P2", name="开始播放")
    c = step(voice, "main/chat.cc", "ShowText(root);", where="before", sid="P3", name="显示文字")
    out = run(voice, a, b, c, segments=[{"from": "P1", "to": "P2", "name": "合成起播"}, {"from": "P1", "to": "P3", "name": "显示"}])
    t = next(x["text"] for x in out[1]["chk"] if x["kind"] == "timing")
    assert "「合成起播」这一段" in t and "Schedule" in t and "当场执行" in t and "Chat::Init" not in t
    assert "Init" in t
    assert "timing" not in kinds(out[2])  # 同一个回调里都当场执行：不提示


def test_door_segment_across_direct_and_posted_lambda(door):
    a = step(door, "main/net_auth.cc", "auth_result_ = parse_auth_reply(reply);", where="after", sid="P4", name="收到校验结果")
    b = step(door, "main/net_auth.cc", "lock_open();", where="after", sid="P5", name="发出开锁")
    c = step(door, "main/net_auth.cc", "led_set(LED_BLUE);", where="after", sid="P6", name="亮蓝灯")
    out = run(door, a, b, c, segments=[{"from": "P4", "to": "P5", "name": "电磁锁动作"}, {"from": "P4", "to": "P6"}])
    t = next(x["text"] for x in out[1]["chk"] if x["kind"] == "timing")
    assert "Post" in t and "「发出开锁」" in t and out[1]["struct_ok"]
    assert "timing" not in kinds(out[2])


def test_segment_across_different_functions_is_not_flagged(door):
    a = step(door, "main/net_auth.cc", "lock_open();", where="after", sid="P1", name="甲")
    b = step(door, "main/net_auth.cc", "send_again();", where="after", sid="P2", name="乙")
    out = run(door, a, b, segments=[{"from": "P1", "to": "P2"}])
    assert "timing" not in kinds(out[1])


# ---------- 接进识别：所有模型共用，缓存沿用的也查 ----------
def door_reply_with(where_line_88="after"):
    res = copy.deepcopy(DOOR_RESULT)
    res["steps"][0]["where"] = where_line_88
    return reply_for(res)


def test_parse_runs_structure_check_for_any_model(tmp_path):
    door = door_source(tmp_path / "door")
    r = idf.parse(door_reply_with("after"), door)
    p1 = r["steps"][0]
    assert p1["loc_ok"] is True and p1["struct_ok"] is False and p1["sure"] == "低"
    assert all(s["struct_ok"] for s in r["steps"][1:])
    page = idf.to_page(r, what="从刷卡到开门")
    assert page["steps"][0]["struct_ok"] is False and page["steps"][0]["chk"][0]["level"] == "fail"
    assert page["steps"][1]["chk"] == [] and page["steps"][1]["struct_ok"] is True


def test_parse_keeps_chain_and_passes_it_to_the_check(tmp_path):
    """新回答带事件链和 chain_step：存下来，分支写明了的点只提醒；旧回答没有这两项也照常解析。"""
    door = door_source(tmp_path / "door")
    res = copy.deepcopy(DOOR_RESULT)
    res["steps"][0]["where"] = "after"
    res["chain"] = [{"n": 1, "what": "读卡芯片报告有卡", "file": "main/rfid_task.c", "line": 88, "branch": "有卡靠近",
                     "repeat": "repeat", "why": "轮询里判断有卡"}]
    for i, x in enumerate(res["steps"]):
        x["chain_step"] = [1, "2", "x", 0, None][i]
    r = idf.parse(reply_for(res), door)
    assert r["chain"] == res["chain"]
    assert [x["chain_step"] for x in r["steps"]] == [1, 2, None, None, None]
    assert r["steps"][0]["struct_ok"] is True and kinds(r["steps"][0], "tip") == ["branch"]
    old = idf.parse(door_reply_with("after"), door)
    assert "chain" not in old and all(x["chain_step"] is None for x in old["steps"])
    assert old["steps"][0]["struct_ok"] is False


def test_reused_cache_uses_saved_chain(tmp_path):
    door = door_source(tmp_path / "door")
    proj = tmp_path / "proj"
    res = copy.deepcopy(DOOR_RESULT)
    res["steps"][0].update(where="after", chain_step=1)
    res["chain"] = [{"n": 1, "branch": "有卡靠近"}]
    saved = idf.parse(reply_for(res), door)
    f = idf._cache_file(proj, "fp", "从刷卡到开门", "deepseek")
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({**saved, "what": "从刷卡到开门", "provider": "deepseek"}, ensure_ascii=False), encoding="utf-8")
    r = idf.identify_cached(None, door, "从刷卡到开门", proj, "fp", "deepseek")
    assert r["reused"] and r["steps"][0]["struct_ok"] is True and r["page"]["steps"][0]["chk"][0]["level"] == "tip"


def test_page_text_has_no_internal_step_ids(voice):
    a = step(voice, "main/chat.cc", "if (!protocol_->OpenAudioChannel()) {", where="after", sid="P1", name="打开通道")
    b = step(voice, "main/chat.cc", "SetState(kSpeaking);", where="before", once="every", sid="P2", name="开始播放")
    c = step(voice, "main/chat.cc", "auto type = json_get(root, \"type\");", where="after", sid="P3", name="收到消息")
    out = run(voice, a, b, c, segments=[{"from": "P3", "to": "P2", "name": "合成"}])
    texts = [x["text"] for s in out for x in s["chk"]]
    assert texts and not any(re.search(r"\bP\d+\b", t) for t in texts)


def test_reused_cache_is_checked_again(tmp_path):
    """存下时还没有这层检查（或者是改完再测挪过的）：沿用时按现在的源码重新查。"""
    door = door_source(tmp_path / "door")
    proj = tmp_path / "proj"

    class NoCall:
        def investigate(self, *a, **k):
            raise AssertionError("沿用时不该调用大模型")

    old = idf.parse(door_reply_with("after"), door)
    for s in old["steps"]:  # 模拟旧版本存下的结果：没有检查字段，可信度也没降
        s.pop("chk"), s.pop("struct_ok")
        s["sure"] = "高"
    f = idf._cache_file(proj, "fp", "从刷卡到开门", "deepseek")
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({**old, "what": "从刷卡到开门", "provider": "deepseek"}, ensure_ascii=False), encoding="utf-8")
    r = idf.identify_cached(NoCall(), door, "从刷卡到开门", proj, "fp", "deepseek")
    assert r["reused"] and r["steps"][0]["struct_ok"] is False and r["steps"][0]["sure"] == "低"
    assert r["page"]["steps"][0]["chk"][0]["level"] == "fail"
    again = idf.identify_cached(NoCall(), door, "从刷卡到开门", proj, "fp", "deepseek")
    assert len(again["page"]["steps"][0]["chk"]) == 1  # 重复查不会叠加


def test_checker_error_does_not_block_identify(door, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(st, "check_branch", boom)
    s, = run(door, step(door, "main/rfid_task.c", "rc522_poll(dev, &frame);", where="after"))
    assert kinds(s) == ["error"] and s["struct_ok"]


def test_web_page_holds_auto_run_and_shows_checks():
    js = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "x.struct_ok===false" in js and "码表结构检查没通过" in js
    assert "chkTxt(x)" in js and "chkTxt(sel)" in js and "chkBox()" in js
