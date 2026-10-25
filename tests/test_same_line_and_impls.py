"""同一行拆成几步的只放一对点；只在几种实现里的一种里才走到的步骤（比如某一种连接方式自己的发送）不放在主线上。
全用假源码，不调用大模型。"""

import copy
import json

import pytest

from fsw import candidates as cd
from fsw import identify as idf
from fsw import structure
from tests.test_candidates import APP, AUDIO, ENGINE, _funcs, _flow, _line, _pt
from tests.test_structure import write

PROTO = """\
class Proto {
public:
    virtual bool SendAudio(std::unique_ptr<Packet> packet) = 0;
};
"""
MQTT = """\
bool MqttProto::SendAudio(std::unique_ptr<Packet> packet) {
    std::string encrypted = Encrypt(packet->payload);
    return udp_->Send(encrypted) > 0;
}
"""
WS = """\
bool WsProto::SendAudio(std::unique_ptr<Packet> packet) {
    return ws_->Send(packet->payload.data(), packet->payload.size(), true);
}
"""


@pytest.fixture
def proj(tmp_path):
    return write(tmp_path / "voice", {"main/engine.cc": ENGINE, "main/app.cc": APP, "main/audio.cc": AUDIO,
                                      "main/proto/proto.h": PROTO, "main/proto/mqtt.cc": MQTT, "main/proto/ws.cc": WS})


def _split_output(proj):
    """模型把输出拆成两步：第 5 步首段、第 6 步末段，同一行。"""
    flow = _funcs(_flow(proj))
    out = flow["chain"][4]
    flow["chain"].insert(5, {**out, "n": 6, "what": "本轮最后一段声音同样由这一句写出"})
    flow["chain"][6]["n"] = 7
    return idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True)[0]["chain"]


def _steps():
    return [_pt("P1", 1, 8, "main/engine.cc", line_of="start"),
            _pt("P2", 2, _line(APP, "SendAudio"), once="first"), _pt("P3", 2, _line(APP, "SendAudio"), once="last"),
            _pt("P4", 3, _line(APP, "ShowUser(text)")), _pt("P5", 4, _line(APP, "SetState(kSpeaking)")),
            _pt("P6", 5, _line(AUDIO, "OutputData"), "main/audio.cc", once="first", felt="first", kind="声音"),
            _pt("P7", 6, _line(AUDIO, "OutputData"), "main/audio.cc", once="last", felt="last", kind="声音"),
            _pt("P8", 7, _line(AUDIO, "callbacks_.on_drained();"), "main/audio.cc")]


def test_output_split_into_two_steps_on_one_line_gets_one_first_last_pair(proj):
    chain = _split_output(proj)
    out, notes = idf.place_role_points(proj, copy.deepcopy(_steps()), chain)
    on = [s for s in out if s["line"] == _line(AUDIO, "OutputData") and s["file"] == "main/audio.cc"]
    assert [(s["id"], s["once"], s["chain_step"]) for s in on] == [("P6", "first", 5), ("P7", "last", 6)]
    assert all("（末次）" not in s["name"] and "（首次）" not in s["name"] for s in out)
    r = idf.parse("```json\n" + json.dumps({"steps": _steps(), "segments": []}) + "\n```", proj, chain=chain, strict=True)
    pairs = [(g["from"], g["to"]) for g in r["segments"]]
    assert ("P6", "P7") in pairs  # 整段播放：从首段量到末段
    by = {s["id"]: s for s in r["steps"]}
    for a, b in pairs:  # 没有两头同一处同一次的零长段
        assert (by[a]["file"], by[a]["line"], by[a]["once"]) != (by[b]["file"], by[b]["line"], by[b]["once"])
    assert not structure.check_order(structure.Project(proj), r["steps"], r["segments"])
    assert not any(c["level"] == "fail" for s in r["steps"] for c in s["chk"])


def test_split_output_with_only_one_model_point_gets_the_other_added_once(proj):
    chain = _split_output(proj)
    steps = [s for s in _steps() if s["id"] != "P7"]
    out, notes = idf.place_role_points(proj, steps, chain)
    on = [s for s in out if s["file"] == "main/audio.cc" and s["line"] == _line(AUDIO, "OutputData")]
    assert sorted(s["once"] for s in on) == ["first", "last"] and len(on) == 2
    assert next(s for s in on if s["once"] == "last")["chain_step"] == 6


def test_two_plain_steps_on_the_same_spot_keep_one_point(proj):
    flow = _funcs(_flow(proj))
    flow["chain"].insert(3, {**flow["chain"][3], "role": ""})
    for i, c in enumerate(flow["chain"], 1):
        c["n"] = i
    chain = idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True)[0]["chain"]
    steps = [_pt("P1", 1, 8, "main/engine.cc", line_of="start"), _pt("P4", 4, _line(APP, "SetState(kSpeaking)")),
             _pt("P5", 5, _line(APP, "SetState(kSpeaking)"))]
    out, notes = idf.place_role_points(proj, steps, chain)
    assert [s["id"] for s in out] == ["P1", "P4"] and any("同一处" in n for n in notes)


def test_upload_candidates_keep_the_shared_call_not_each_transport(proj):
    cs = cd.find(proj, "语音对话")
    assert [(c["file"], c["line"]) for c in cs if c["role"] == "上传"] == [("main/app.cc", _line(APP, "SendAudio"))]
    p = structure.Project(proj)
    assert structure.one_of_impls(p, p.file("main/proto/mqtt.cc"), 3)[:2] == ("MqttProto", "SendAudio")
    assert structure.one_of_impls(p, p.file("main/app.cc"), _line(APP, "SendAudio")) is None  # 只有一份的不算


def test_upload_step_written_in_one_transport_moves_to_the_shared_call(proj):
    cs = cd.find(proj, "语音对话")
    flow = _funcs(_flow(proj, s2={"file": "main/proto/mqtt.cc", "line": 3, "code": "return udp_->Send(encrypted) > 0;"}))
    flow["chain"][1]["func"] = "SendAudio"
    fixes = []
    idf.check_chain(proj, copy.deepcopy(flow), "语音对话", fixes, fresh=True, cands=cs)
    assert any("第 2 步" in f["text"] and f.get("soft") for f in fixes)  # 先问一次
    got = idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True, cands=cs)[0]
    assert (got["chain"][1]["file"], got["chain"][1]["line"]) == ("main/app.cc", _line(APP, "SendAudio"))


def test_plain_step_inside_one_transport_goes_to_the_side_line(proj):
    flow = _funcs(_flow(proj))
    flow["chain"].insert(2, {**flow["chain"][1], "file": "main/proto/mqtt.cc", "line": 3, "code": "return udp_->Send(encrypted) > 0;",
                             "func": "SendAudio", "role": "", "what": "发到网络"})
    for i, c in enumerate(flow["chain"], 1):
        c["n"] = i
    chain = idf.check_chain(proj, flow, "语音对话", fresh=True, apply=True)[0]["chain"]
    steps = [_pt("P1", 1, 8, "main/engine.cc", line_of="start"),
             _pt("P2", 2, _line(APP, "SendAudio"), once="first"), _pt("P3", 2, _line(APP, "SendAudio"), once="last"),
             _pt("P4", 3, 3, "main/proto/mqtt.cc", once="first"), _pt("P5", 3, 3, "main/proto/mqtt.cc", once="last"),
             _pt("P6", 4, _line(APP, "ShowUser(text)"))]
    segs = [{"from": "P2", "to": "P4", "name": "交给协议层"}, {"from": "P4", "to": "P5", "name": "逐包上传"}, {"from": "P5", "to": "P6", "name": "识别"}]
    r = idf.parse("```json\n" + json.dumps({"steps": steps, "segments": segs}) + "\n```", proj, chain=chain, strict=True)
    by = {s["id"]: s for s in r["steps"]}
    assert by["P4"]["line_of"] == by["P5"]["line_of"] == "side"
    assert not any(g["from"] in ("P4", "P5") or g["to"] in ("P4", "P5") for g in r["segments"])
    side = {(g["from"], g["to"]): g["name"] for g in r["side_segments"]}
    assert side.get(("P2", "P4")) == "交给协议层" and ("P3", "P5") in side
    assert ("P3", "P6") in [(g["from"], g["to"]) for g in r["segments"]]
    assert not any(c["level"] == "fail" for s in r["steps"] for c in s["chk"])
