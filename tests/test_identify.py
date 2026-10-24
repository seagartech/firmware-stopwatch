"""任务 13：找关键步骤。全部用假模型或本机假服务器，不真调用大模型、不花钱。"""

import copy
import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fsw import identify as idf
from fsw.llm import ClaudeAPI, ClaudeCode, DeepSeek, FakeLLM, LLMError
from fsw.llm.tools import SourceTools
from tests.identify_fixtures import DOOR_REPLY, DOOR_RESULT, door_source, reply_for


@pytest.fixture
def door(tmp_path):
    return door_source(tmp_path / "door")


# ---------- 只读工具：只能看源码目录里面 ----------
def test_tools_read_with_line_numbers_and_stay_inside(door):
    t = SourceTools(door)
    out = t.call("read_file", {"path": "main/rfid_task.c", "start_line": 87, "end_line": 88})
    assert "88\t    if (rc522_card_present(dev)) {" in out
    assert "main/ui_led.c:42:" in t.call("grep", {"pattern": "led_set"})
    assert "main/" in t.call("list_dir", {"path": "."})
    assert "拒绝" in t.call("read_file", {"path": "../../etc/passwd"})


# ---------- 读回复、核对位置、换成网页要的样子 ----------
def test_parse_reply_and_check_locations(door):
    r = idf.parse(DOOR_REPLY, door)
    assert r["ok"] and len(r["steps"]) == 5
    p1 = r["steps"][0]
    assert p1["code"] == "if (rc522_card_present(dev)) {" and p1["loc_ok"]


def test_wrong_location_is_marked_for_checking(door):
    res = copy.deepcopy(DOOR_RESULT)
    res["steps"][1]["line"] = 9999
    res["steps"][3]["file"] = "main/nope.c"
    r = idf.parse(reply_for(res), door)
    bad = [s for s in r["steps"] if not s["loc_ok"]]
    assert [s["id"] for s in bad] == ["P2", "P4"]
    assert all(s["sure"] == "低" and "没有这一行" in s["doubt"] or "找不到这个文件" in s["doubt"] for s in bad)


def test_page_shape_matches_demo_fields(door):
    page = idf.to_page(idf.parse(DOOR_REPLY, door), what="从刷卡到开门")
    assert [s["p"] for s in page["steps"]] == ["P1", "P2", "P3", "P4", "P5"]
    assert page["steps"][0]["f"] == "rfid_task.c" and page["steps"][0]["path"] == "main/rfid_task.c"
    assert [s["line"] for s in page["steps"]] == ["start", "talk", "face", "talk", "talk"]
    assert page["ln"] == {"start": "起点", "talk": "开门这条", "face": "亮灯这条"}
    assert page["main"] == ["P1", "P2", "P4", "P5"]
    assert page["branch"] == {"from": "P2", "nodes": ["P3"]}
    assert page["dash"] == [1]
    assert page["segs"][1] == {"a": "P2", "b": "P4", "t": "服务器校验并返回", "who": "服务器 + 网络", "out": True}
    assert page["sideAsk"]["k"] == 1 and page["sideAsk"]["q"] == "亮灯响一声"
    assert page["mainTitle"] == "开门这条：卡贴上去 → 发出开锁"
    assert page["mapWhat"] == "从刷卡到开门"
    assert page["mapSel"] == "P3"  # 第一个需核对的


def test_page_without_side_line(door):
    res = copy.deepcopy(DOOR_RESULT)
    res["side"] = None
    res["steps"] = [s for s in res["steps"] if s["line_of"] != "side"]
    res["side_segments"] = []
    page = idf.to_page(idf.parse(reply_for(res), door), what="从刷卡到开门")
    assert page["sideAsk"] is None and page["branch"]["nodes"] == []


def test_missing_start_or_end_says_which(door):
    res = {"start_found": True, "end_found": False, "missing": "代码里没找到开门的地方", "steps": []}
    r = idf.parse(reply_for(res), door)
    assert not r["ok"] and r["kind"] == "no_end"
    assert "终点" in r["message"] and "换个说法" in r["message"]


def test_missing_end_but_some_steps_still_lists_them(door):
    res = copy.deepcopy(DOOR_RESULT)
    res["end_found"], res["missing"] = False, "没找到开锁的地方。"
    res["steps"] = res["steps"][:2]
    r = idf.parse(reply_for(res), door)
    assert r["ok"] and "终点" in r["warn"] and "添加步骤" in r["warn"]
    assert idf.to_page(r, "从刷卡到开门")["warn"] == r["warn"]


def test_bad_reply_format(door):
    r = idf.parse("我读完了，但是没按格式写。", door)
    assert not r["ok"] and r["kind"] == "bad_reply"


# ---------- 提示词 ----------
def test_prompt_says_read_only_and_asks_for_markers():
    p = idf.build_prompt("从刷卡到开门", chip="芯片 ESP32-S3")
    assert "从刷卡到开门" in p and "不改任何文件" in p and "找到：" in p and "```json" in p
    retry = idf.build_prompt("从刷卡到开门", known=["读到卡", "卡号读完"])
    assert "读到卡" in retry and "接着找" in retry


@pytest.mark.parametrize("what", ["从刷卡到开门", "从说完话到开始播放回复"])
def test_identify_prompt_states_the_purpose_and_is_sent_to_the_model(door, what):
    """门禁、语音两类都发同一份通用提示词：目的（多轮重复、每轮每点一次、只量设备忙的时间）和由此来的选点要求。"""
    m = FakeLLM(DOOR_REPLY)
    idf.identify(m, door, what)
    p = m.prompts[0]
    assert p == idf.build_prompt(what) and what in p
    for key in ["每一轮正常使用时，每个计时点恰好记到一次", "这一轮输入结束", "不含人操作、等人开口的时间",
                "不写 every", "出错、失败、重试的那一支不要放", "没有依据就不要把它们串成前后相连的段",
                "点记到的是它真正执行的时刻", "先从配置和初始化代码确认这台设备实际走哪条", "sdkconfig",
                # 第二轮：一条触发路径、每轮都经过的起点、终点到最后一个结果、条件下的 always、首包放在确实会用上之后、顺带线用自己的点
                "主线上的点都必须在这一条路径上，不同触发方式的点不能串成一条线",
                "必须有一个点的 line_of 写 start", "人开始输入到输入结束之间的事", "要覆盖一轮的最后一个结果",
                "always 写 false，除非在 why 里说明正常每轮都会满足这个条件", "判断出\"这个包确实会被用上\"之后",
                "用的点都要属于这条线本身"]:
        assert key in p, key
    assert "被丢掉的也要能记到" not in p  # 旧规则会把点推到逐帧经过的判断之前
    assert "选它们汇合后每轮都经过的那一步" not in p  # 旧规则会把不同触发方式混在一条线上
    for bad in ["小智", "DeepSeek", "application.cc", "main/"]:  # 提示词模板不点名任何固件或文件
        assert bad not in idf.PROMPT, bad


# ---------- 整个识别：边找边报、出错留着已找到的 ----------
def test_identify_reports_progress_and_result(door):
    seen = []
    r = idf.identify(FakeLLM(DOOR_REPLY, chunk=40), door, "从刷卡到开门", on_progress=seen.append)
    assert r["ok"] and len(r["page"]["steps"]) == 5
    assert seen[-1]["found"] == ["读到卡", "卡号读完", "亮灯响一声", "收到服务器校验结果", "发出开锁"]
    assert seen[-1]["at"][0] == "main/rfid_task.c:88"


def test_identify_failure_keeps_what_was_found(door):
    class Half(FakeLLM):
        def investigate(self, prompt, root, on_text=None, on_tool=None):
            on_text and on_text("找到：读到卡（main/rfid_task.c:88）\n")
            raise self.fail("offline")
    seen = []
    r = idf.identify(Half(""), door, "从刷卡到开门", on_progress=seen.append)
    assert not r["ok"] and r["kind"] == "offline" and r["found"] == ["读到卡"]
    assert "重试" in r["message"]


# ---------- 存上次的结果：源码和要测的都没变，直接用 ----------
def test_cache_by_fingerprint_and_what(tmp_path, door):
    proj = tmp_path / "proj"
    calls = []

    class Count(FakeLLM):
        def investigate(self, *a, **k):
            calls.append(1)
            return super().investigate(*a, **k)
    m = Count(DOOR_REPLY)
    r1 = idf.identify_cached(m, door, "从刷卡到开门", proj, fingerprint="abc", provider="deepseek", reuse=True)
    r2 = idf.identify_cached(m, door, "从刷卡到开门", proj, fingerprint="abc", provider="deepseek", reuse=True)
    assert r1["ok"] and r2["ok"] and r2["reused"] and len(calls) == 1
    idf.identify_cached(m, door, "从刷卡到亮灯", proj, fingerprint="abc", provider="deepseek", reuse=True)
    idf.identify_cached(m, door, "从刷卡到开门", proj, fingerprint="abc", provider="deepseek", reuse=False)
    assert len(calls) == 3


# ---------- AI 推荐常用语、添加步骤 ----------
def test_suggest_phrases(door):
    m = FakeLLM('["从刷卡到开门", "从刷卡到亮灯响一声", "从开机到连上服务器", "多余的第四个"]')
    assert idf.suggest(m, door, {"name": "door-ctrl"}) == ["从刷卡到开门", "从刷卡到亮灯响一声", "从开机到连上服务器"]
    assert idf.suggest(FakeLLM("不是列表"), door, {"name": "door-ctrl"}) == []
    assert "door-ctrl" in m.prompts[0]


def test_add_step_finds_location(door):
    m = FakeLLM('```json\n{"name": "门锁到位", "file": "main/lock_ctrl.c", "line": 55, "why": "电磁锁动作的地方", "sure": "中"}\n```')
    r = idf.add_step(m, door, "门锁到位", what="从刷卡到开门")
    assert r["ok"] and r["f"] == "lock_ctrl.c" and r["l"] == 55 and r["code"] == "gpio_set_level(LOCK_PIN, 0);"
    bad = idf.add_step(FakeLLM('```json\n{"name": "x", "file": "main/nope.c", "line": 3}\n```'), door, "x")
    assert not bad["ok"] and "找不到" in bad["message"]


# ---------- 三种大模型的"读代码"接法 ----------
def test_fake_model_investigate_reports_tools(door):
    tools = []
    FakeLLM(DOOR_REPLY, tool_calls=[("read_file", {"path": "main/rfid_task.c"})]).investigate("p", door, on_tool=tools.append)
    assert tools == ["read_file main/rfid_task.c"]


def test_claude_code_runs_read_only_in_source_folder(tmp_path, door):
    log = tmp_path / "args.txt"
    lines = [
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "找到：读到卡\n"}}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": str(door / "main/rfid_task.c")}}]}},
        {"type": "result", "is_error": False, "result": "找到：读到卡\n"},
    ]
    out = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\npwd > '{log}'\nfor a in \"$@\"; do echo \"$a\" >> '{log}'; done\ncat <<'EOF'\n{out}\nEOF\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    texts, tools = [], []
    final = ClaudeCode(exe).investigate("找步骤", door, on_text=texts.append, on_tool=tools.append)
    args = log.read_text(encoding="utf-8").splitlines()
    assert args[0] == str(door.resolve())
    assert "--restricted" in args and "Read,Grep,Glob" in args and "--no-session-persistence" in args
    assert "Bash" not in " ".join(args[args.index("--tools") + 1:args.index("--tools") + 2])
    assert texts == ["找到：读到卡\n"] and tools == ["Read main/rfid_task.c"] and "读到卡" in final


@pytest.fixture
def seq_server():
    """按顺序回事先录好的几次回复（大模型要来回好几轮：先要读文件，再给结果）。"""
    replies, seen = [], []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            seen.append(body)
            data = json.dumps(replies.pop(0)).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", replies, seen
    srv.shutdown()


def test_deepseek_reads_code_with_tools(seq_server, door):
    url, replies, seen = seq_server
    replies += [
        {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "main/rfid_task.c", "start_line": 88, "end_line": 88}'}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": DOOR_REPLY}}]},
    ]
    tools, texts = [], []
    final = DeepSeek("fake-key", base_url=url).investigate("找步骤", door, on_text=texts.append, on_tool=tools.append)
    assert final == DOOR_REPLY and tools == ["read_file main/rfid_task.c"]
    assert seen[0]["tools"] and seen[1]["messages"][-1]["role"] == "tool"
    assert "rc522_card_present" in seen[1]["messages"][-1]["content"]


def test_claude_api_reads_code_with_tools(seq_server, door):
    url, replies, seen = seq_server
    msg = lambda content, stop: {"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                  "content": content, "stop_reason": stop, "stop_sequence": None,
                                  "usage": {"input_tokens": 1, "output_tokens": 1}}
    replies += [
        msg([{"type": "tool_use", "id": "t1", "name": "grep", "input": {"pattern": "led_set"}}], "tool_use"),
        msg([{"type": "text", "text": DOOR_REPLY}], "end_turn"),
    ]
    tools = []
    final = ClaudeAPI("fake-key", base_url=url).investigate("找步骤", door, on_tool=tools.append)
    assert final == DOOR_REPLY and tools == ["grep led_set"]
    assert seen[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_claude_code_hanging_is_stopped_with_plain_message(tmp_path, door):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(LLMError) as e:
        ClaudeCode(exe, timeout=1).investigate("找步骤", door)
    assert e.value.kind == "timeout" and "重试" in e.value.message


def test_reused_result_rebuilds_page_with_new_fields(tmp_path, door):
    proj = tmp_path / "proj"
    idf.identify_cached(FakeLLM(DOOR_REPLY), door, "从刷卡到开门", proj, fingerprint="f", provider="cc")
    f = next((proj / "identify").glob("*.json"))
    old = json.loads(f.read_text(encoding="utf-8"))
    for s in old["page"]["steps"]:  # 像旧版本存下的结果：页面数据里没有"一轮记哪一次"
        s.pop("once", None)
    f.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    r = idf.identify_cached(FakeLLM(""), door, "从刷卡到开门", proj, fingerprint="f", provider="cc")
    assert r["reused"] and "once" in r["page"]["steps"][0]


def test_missing_end_reason_reads_as_a_sentence(tmp_path):
    from tests.identify_fixtures import DOOR_RESULT, reply_for, door_source
    import json as _j
    r = _j.loads(_j.dumps(DOOR_RESULT)); r["end_found"] = False; r["missing"] = "代码里没有门磁信号"
    out = idf.parse(reply_for(r), door_source(tmp_path / "door"))
    assert "代码里没有门磁信号。可以点" in out["warn"]
