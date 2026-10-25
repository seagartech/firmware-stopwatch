"""码表自己在源码里找候选行（输入结束、模式、协议、上传、消息、输出、全部放完），模型只从里面挑；
关键步骤的点和主线的段由码表按流程放、连。全用假源码，不调用大模型。"""

import copy

import pytest

from fsw import candidates as cd
from fsw import identify as idf
from fsw import structure
from tests.test_structure import write

ENGINE = """\
void Engine::OnResult(const result_t* r) {
    if (cb_) {
        if (r->vad_state == VAD_SPEECH && !speaking_) {
            speaking_ = true;
            cb_(true);
        } else if (r->vad_state == VAD_SILENCE && speaking_) {
            speaking_ = false;
            cb_(false);
        }
    }
}
"""
APP = """\
void App::Run() {
    while (true) {
        auto bits = Wait();
        if (bits & EVENT_SEND) {
            while (auto packet = PopPacket()) {
                if (proto_ && !proto_->SendAudio(std::move(packet))) {
                    break;
                }
            }
        }
    }
}

void App::Start() {
    if (cfg_->HasMqtt()) {
        proto_ = std::make_unique<MqttProto>();
    } else {
        proto_ = std::make_unique<WsProto>();
    }
}

ListenMode App::DefaultMode() {
    return aec_ == kAecOff ? kListenModeAutoStop : kListenModeRealtime;
}

void App::OnMessage(const cJSON* root) {
    auto type = cJSON_GetObjectItem(root, "type");
    if (strcmp(type->valuestring, "tts") == 0) {
        auto state = cJSON_GetObjectItem(root, "state");
        if (strcmp(state->valuestring, "start") == 0) {
            Schedule([this]() {
                SetState(kSpeaking);
            });
        } else if (strcmp(state->valuestring, "stop") == 0) {
            Schedule([this]() {
                SetState(kListening);
            });
        } else if (strcmp(state->valuestring, "sentence") == 0) {
            ShowText(root);
        }
    } else if (strcmp(type->valuestring, "stt") == 0) {
        auto text = cJSON_GetObjectItem(root, "text");
        ESP_LOGI(TAG, ">> %s", text->valuestring);
        Schedule([this]() {
            ShowUser(text);
        });
    } else if (strcmp(type->valuestring, "llm") == 0) {
        SetEmotion(root);
    }
}
"""
AUDIO = """\
void Audio::OutputTask() {
    while (true) {
        auto task = Pop();
        codec_->OutputData(task.pcm);
        bool notify_drained = false;
        notify_drained = MarkDrained();
        if (notify_drained && callbacks_.on_drained) {
            callbacks_.on_drained();
        }
    }
}
"""
BOARD = """\
void Board::Init() {
    while (true) {
        codec_->OutputData(buf.pcm);
    }
}
"""
TEST = """\
void test_send() {
    while (1) { proto->SendAudio(pkt); }
}
"""


def _line(text, needle):
    return next(i for i, ln in enumerate(text.split("\n"), 1) if needle in ln)


@pytest.fixture
def proj(tmp_path):
    files = {"main/engine.cc": ENGINE, "main/app.cc": APP, "main/audio.cc": AUDIO, "main/tests/test_send.cc": TEST}
    for k in range(9):  # 一个目录下并排很多款板子：某一款才用的排在后面、有通用的就不要
        files[f"main/boards/b{k}/board.cc"] = BOARD
    return write(tmp_path / "voice", files)


def _at(cands, role):
    return [(c["file"], c["line"]) for c in cands if c["role"] == role]


def test_finds_one_verified_statement_per_key_role(proj):
    cs = cd.find(proj, "语音对话")
    assert [c["id"] for c in cs] == [f"K{i}" for i in range(1, len(cs) + 1)]
    assert _at(cs, "输入结束") == [("main/engine.cc", 7), ("main/engine.cc", 8)]  # 静音翻转那一支，不是说话开始那一支
    assert _at(cs, "模式") == [("main/app.cc", _line(APP, "return aec_"))]
    assert _at(cs, "协议") == [("main/app.cc", _line(APP, "if (cfg_->HasMqtt"))]
    assert _at(cs, "上传") == [("main/app.cc", _line(APP, "SendAudio"))]  # 测试目录里的不算
    assert _at(cs, "输出") == [("main/audio.cc", _line(AUDIO, "OutputData"))]  # 板子目录里的同样写法不要
    assert _at(cs, "全部放完") == [("main/audio.cc", _line(AUDIO, "callbacks_.on_drained();"))]
    msg = {c["line"]: c["where"] for c in cs if c["role"] == "消息"}
    assert _line(APP, "SetState(kSpeaking)") in msg and "tts/start" in msg[_line(APP, "SetState(kSpeaking)")]
    assert _line(APP, "ShowUser(text)") in msg and "「stt」" in msg[_line(APP, "ShowUser(text)")]
    assert _line(APP, "ESP_LOGI") not in msg and _line(APP, "auto text") not in msg  # 打日志、取字段的不算
    for c in cs:  # 每一句都是真语句：不是判断或循环开头（协议是"几支里选其一"，写最前面那个 if）
        if c["role"] == "协议":
            continue
        src = structure.Project(proj).file(c["file"])
        assert structure.real_stmt(src, c["line"]) or structure.call_in_if(src.clines[c["line"] - 1])


def test_chain_prompt_lists_candidates_only_when_there_are_some(proj):
    cs = cd.find(proj, "语音对话")
    p = idf.build_chain_prompt("从说完话到回复播完", "", "语音对话", cs)
    assert "码表在源码里找到的候选" in p and "K1 main/engine.cc:7" in p and '"cand": "K编号"' in p
    assert "码表在源码里找到的候选" not in idf.build_chain_prompt("从说完话到回复播完", "", "语音对话")


def _flow(proj, **chain_over):
    def step(n, file, line, role="", repeat="once", **kw):
        src = (proj / file).read_text().split("\n")
        return {"n": n, "what": f"第{n}步", "file": file, "line": line, "code": src[line - 1].strip(), "func": "f", "role": role,
                "branch": "", "repeat": repeat, "after": "" if n == 1 else "上一步之后", **kw}
    chain = [step(1, "main/engine.cc", 8), step(2, "main/app.cc", _line(APP, "SendAudio"), "上传首包、上传末包", "repeat"),
             step(3, "main/app.cc", _line(APP, "ShowUser(text)"), "首个结果"),
             step(4, "main/app.cc", _line(APP, "SetState(kSpeaking)")),
             step(5, "main/audio.cc", _line(AUDIO, "OutputData"), "输出", "repeat"),
             step(6, "main/audio.cc", _line(AUDIO, "callbacks_.on_drained();"), "全部放完、最后结果")]
    for i, over in chain_over.items():
        chain[int(i[1:]) - 1].update(over)
    return {"trigger": "说话", "upload": "声音", "detect": {"kind": "静音检测翻转", "found": True},
            "mode": {"what": "自动停止", "file": "main/app.cc", "line": _line(APP, "return aec_"), "code": "return aec_",
                     "protocol": {"what": "mqtt", "file": "main/app.cc", "line": _line(APP, "if (cfg_->HasMqtt"), "code": "if (cfg_->HasMqtt()) {"}},
            "chain": chain, "doubts": []}


def _funcs(flow):
    flow["chain"][0]["what"], flow["chain"][2]["what"] = "静音检测翻转", "收到识别文字"
    for c in flow["chain"]:
        c["func"] = {"main/engine.cc": "OnResult", "main/audio.cc": "OutputTask"}.get(c["file"], "Run")
    flow["chain"][2]["func"] = flow["chain"][3]["func"] = "OnMessage"
    return flow


def test_cand_ids_are_filled_by_the_tool_and_unknown_ids_are_asked(proj):
    cs = cd.find(proj, "语音对话")
    k = {(c["file"], c["line"]): c["id"] for c in cs}
    flow = _funcs(_flow(proj))
    up = flow["chain"][1]
    up["cand"] = k[(up["file"], up["line"])]
    del up["file"], up["line"], up["code"]
    got, bad = idf.check_chain(proj, flow, "语音对话", [], fresh=True, cands=cs)
    assert not bad and got["chain"][1]["line"] == _line(APP, "SendAudio") and got["chain"][1]["file"] == "main/app.cc"
    flow2 = _funcs(_flow(proj))
    flow2["chain"][1]["cand"] = "K99"
    del flow2["chain"][1]["file"]
    fixes = []
    _g, bad = idf.check_chain(proj, flow2, "语音对话", fixes, fresh=True, cands=cs)
    assert any("K99" in b for b in bad) and any("K99" in f["text"] and not f.get("soft") for f in fixes)


def test_key_step_off_the_candidates_is_asked_then_moved_to_the_nearest_one(proj):
    cs = cd.find(proj, "语音对话")
    flow = _funcs(_flow(proj, s1={"line": 6, "code": "} else if (r->vad_state == VAD_SILENCE && speaking_) {"},
                         s6={"line": _line(AUDIO, "notify_drained = MarkDrained();"), "code": "notify_drained = MarkDrained();"}))
    fixes = []
    _g, bad = idf.check_chain(proj, copy.deepcopy(flow), "语音对话", fixes, fresh=True, cands=cs)
    assert not bad and any("第 1 步" in f["text"] and "K1" in f["text"] and f.get("soft") for f in fixes)
    got = idf.check_chain(proj, copy.deepcopy(flow), "语音对话", fresh=True, apply=True, cands=cs)[0]
    assert (got["chain"][0]["file"], got["chain"][0]["line"]) == ("main/engine.cc", 7)  # 同一个函数里最近的候选
    assert got["chain"][5]["line"] == _line(AUDIO, "callbacks_.on_drained();")


def test_mode_off_the_candidates_falls_back_to_the_only_mode_candidate(proj):
    cs = cd.find(proj, "语音对话")
    flow = _funcs(_flow(proj))
    flow["mode"].update(line=_line(APP, "auto bits"), code="auto bits = Wait();")
    fixes = []
    idf.check_chain(proj, copy.deepcopy(flow), "语音对话", fixes, fresh=True, cands=cs)
    assert any(f["text"].startswith("mode 写的是") and f.get("soft") for f in fixes)
    got = idf.check_chain(proj, copy.deepcopy(flow), "语音对话", fresh=True, apply=True, cands=cs)[0]
    assert got["mode"]["line"] == _line(APP, "return aec_") and any("模式从" in r for r in got["repairs"])


def _pt(i, n, line, file="main/app.cc", once="once", line_of="main", **kw):
    return {"id": i, "name": i, "file": file, "line": line, "chain_step": n, "once": once, "line_of": line_of, "where": "before", **kw}


def test_tool_places_role_points_and_never_measures_from_a_last_to_a_first(proj):
    flow = _funcs(_flow(proj))
    chain = idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True)[0]["chain"]
    steps = [_pt("P1", 1, 6, "main/engine.cc", line_of="start"),  # 模型写在 else if 那一行
             _pt("P2", 2, _line(APP, "while (auto packet"), once="first"),  # 写在循环头
             _pt("P3", 3, _line(APP, "Schedule([this]() {") + 0),
             _pt("P4", 4, _line(APP, "SetState(kSpeaking)")),
             _pt("P5", 5, _line(AUDIO, "OutputData"), "main/audio.cc", once="first", felt="first", kind="声音"),
             _pt("P6", 6, _line(AUDIO, "callbacks_.on_drained();"), "main/audio.cc")]
    out, notes = idf.place_role_points(proj, copy.deepcopy(steps), chain)
    by = {(s["chain_step"], s["once"]): s for s in out}
    assert by[(1, "once")]["line"] == 8 and by[(2, "first")]["line"] == by[(2, "last")]["line"] == _line(APP, "SendAudio")
    assert by[(5, "last")]["felt"] == "last" and by[(5, "last")]["line"] == _line(AUDIO, "OutputData")
    segs, _n = structure.build_segments(out, [{"from": "P1", "to": "P2", "name": "开始上传", "who": "固件"}])
    ids = {s["id"]: s for s in out}
    assert segs[0]["name"] == "开始上传"
    for g in segs:  # 不会从"最后一次"连到别处的"第一次"
        assert not (ids[g["from"]]["once"] == "last" and ids[g["to"]]["once"] == "first")
    last2 = by[(2, "last")]["id"]
    assert {"from": by[(2, "first")]["id"], "to": last2} in [{"from": g["from"], "to": g["to"]} for g in segs]
    r = idf.parse("```json\n" + __import__("json").dumps({"steps": steps, "segments": []}) + "\n```", proj, chain=chain, strict=True)
    end = [g for g in r["segments"] if g["to"] == next(s["id"] for s in r["steps"] if s["chain_step"] == 6)]
    first_out = next(s["id"] for s in r["steps"] if s["chain_step"] == 5 and s["once"] == "first")
    assert end and all(g["from"] == first_out for g in end)  # 全部放完、最后结果从首段输出量起
    assert not structure.check_order(structure.Project(proj), r["steps"], r["segments"])


def test_step_without_any_model_point_is_left_to_the_follow_up(proj):
    chain = idf.check_chain(proj, _funcs(_flow(proj)), "语音对话", fresh=True, apply=True)[0]["chain"]
    steps = [_pt("P1", 1, 8, "main/engine.cc", line_of="start"), _pt("P3", 3, _line(APP, "ShowUser(text)"))]
    out, _n = idf.place_role_points(proj, steps, chain)
    assert [s["chain_step"] for s in out] == [1, 3]


def test_points_after_the_last_result_are_next_round(proj):
    flow = _funcs(_flow(proj))
    flow["chain"].append({**flow["chain"][3], "n": 7, "role": "", "after": "放完以后回到聆听"})
    chain = idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True)[0]["chain"]
    steps = [_pt("P1", 1, 8, "main/engine.cc", line_of="start"), _pt("P6", 6, _line(AUDIO, "callbacks_.on_drained();"), "main/audio.cc"),
             _pt("P7", 7, _line(APP, "SetState(kSpeaking)"))]
    out, notes = idf.place_role_points(proj, steps, chain)
    assert [s["id"] for s in out] == ["P1", "P6"] and any("下一轮" in n for n in notes)


def test_message_step_named_in_its_text_moves_into_that_message(proj):
    cs = cd.find(proj, "语音对话")
    flow = _funcs(_flow(proj, s3={"line": _line(APP, "auto bits"), "code": "auto bits = Wait();"}))
    flow["chain"][2].update(func="Run", what="收到识别文字（stt消息）")
    got = idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True, cands=cs)[0]
    assert got["chain"][2]["line"] == _line(APP, "ShowUser(text)") and any("换成候选" in r for r in got["repairs"])
