import json
import os
import socket
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fsw.llm import ClaudeAPI, ClaudeCode, DeepSeek, FakeLLM, LLMError, find_claude, make_llm

FAKE_KEY = "fake-key-for-tests-0001"


# ---------- 录好的回复：一个本机假服务器，照官方格式回 ----------
def sse(events):
    return "".join(f"event: {e.get('type', 'message')}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


ANTHROPIC_OK = sse([
    {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
     "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 0}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "好"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
    {"type": "message_stop"},
])
DEEPSEEK_OK = (
    b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n'
    b"data: [DONE]\n\n"
)


def err(status, typ, msg):
    return status, json.dumps({"type": "error", "error": {"type": typ, "message": msg}}).encode(), "application/json"


@pytest.fixture
def server():
    """按路径返回事先录好的回复；route['/v1/messages'] = (状态码, 内容, 类型)。"""
    routes, seen = {}, []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            seen.append((self.path.split("?")[0], {k.lower(): v for k, v in self.headers.items()}, json.loads(body or b"{}")))
            status, data, ctype = routes[self.path.split("?")[0]]
            self.send_response(status)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", routes, seen
    srv.shutdown()


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


# ---------- 假模型 ----------
def test_fake_model_streams_recorded_reply():
    m = FakeLLM("找到 9 个关键步骤", chunk=3)
    assert list(m.stream("x")) == ["找到 ", "9 个", "关键步", "骤"]
    assert m.complete("x") == "找到 9 个关键步骤"
    assert m.prompts == ["x", "x"]


def test_fake_model_can_fail_like_a_real_one():
    m = FakeLLM("", test_kind="no_balance")
    r = m.test()
    assert not r["ok"] and r["kind"] == "no_balance" and "余额" in r["message"]


# ---------- Claude（API 密钥）：只用录好的回复，不真调用 ----------
def test_claude_api_streams_text(server):
    url, routes, seen = server
    routes["/v1/messages"] = (200, ANTHROPIC_OK, "text/event-stream")
    m = ClaudeAPI(FAKE_KEY, base_url=url)
    assert list(m.stream("只回一个词")) == ["你", "好"]
    path, headers, body = seen[0]
    assert headers.get("x-api-key") == FAKE_KEY
    assert body["model"] == "claude-opus-5-5"
    assert body["stream"] is True


@pytest.mark.parametrize("route,kind,words", [
    (err(401, "authentication_error", "invalid x-api-key"), "bad_key", "密钥不对"),
    (err(402, "billing_error", "billing"), "no_balance", "余额"),
    (err(400, "invalid_request_error", "Your credit balance is too low to access the Anthropic API."), "no_balance", "余额"),
])
def test_claude_api_key_test_tells_what_went_wrong(server, route, kind, words):
    url, routes, _ = server
    routes["/v1/messages"] = route
    r = ClaudeAPI(FAKE_KEY, base_url=url).test()
    assert not r["ok"] and r["kind"] == kind and words in r["message"]
    assert FAKE_KEY not in r["message"]


def test_claude_api_offline():
    r = ClaudeAPI(FAKE_KEY, base_url=closed_port()).test()
    assert r["kind"] == "offline" and "连不上" in r["message"]


def test_claude_api_key_ok(server):
    url, routes, _ = server
    routes["/v1/messages"] = (200, ANTHROPIC_OK, "text/event-stream")
    r = ClaudeAPI(FAKE_KEY, base_url=url).test()
    assert r["ok"] and "能用" in r["message"]


# ---------- DeepSeek：同样先用录好的回复 ----------
def test_deepseek_streams_text(server):
    url, routes, seen = server
    routes["/chat/completions"] = (200, DEEPSEEK_OK, "text/event-stream")
    assert list(DeepSeek(FAKE_KEY, base_url=url).stream("只回一个词")) == ["你", "好"]
    assert seen[0][1].get("authorization") == f"Bearer {FAKE_KEY}"
    assert seen[0][2]["stream"] is True


@pytest.mark.parametrize("status,kind,words", [(401, "bad_key", "密钥不对"), (402, "no_balance", "余额")])
def test_deepseek_key_test_tells_what_went_wrong(server, status, kind, words):
    url, routes, _ = server
    routes["/chat/completions"] = (status, b'{"error":{"message":"x"}}', "application/json")
    r = DeepSeek(FAKE_KEY, base_url=url).test()
    assert r["kind"] == kind and words in r["message"]


def test_deepseek_offline():
    assert DeepSeek(FAKE_KEY, base_url=closed_port()).test()["kind"] == "offline"


# ---------- 本机 Claude Code：用一个假的 claude 命令 ----------
def fake_claude(tmp_path, lines, code=0):
    p = tmp_path / "claude"
    out = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{out}\nEOF\nexit {code}\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def delta(t):
    return {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}}


def test_claude_code_streams_text(tmp_path):
    exe = fake_claude(tmp_path, [{"type": "system"}, delta("你"), delta("好"), {"type": "result", "is_error": False, "result": "你好"}])
    assert list(ClaudeCode(exe).stream("只回一个词")) == ["你", "好"]


def test_claude_code_not_logged_in(tmp_path):
    exe = fake_claude(tmp_path, [{"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}], code=1)
    r = ClaudeCode(exe).test()
    assert r["kind"] == "not_logged_in" and "登录" in r["message"]


def test_claude_code_not_installed(tmp_path):
    r = ClaudeCode(None).test()
    assert r["kind"] == "not_installed" and "Claude Code" in r["message"]


def test_find_claude_also_looks_inside_the_desktop_app(tmp_path):
    for v in ["2.1.9", "2.1.10"]:
        exe = tmp_path / "Library/Application Support/Claude/claude-code" / v / "abc/claude.app/Contents/MacOS/claude"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    found = find_claude(home=tmp_path, path_env="")
    assert "/2.1.10/" in str(found)


# ---------- 统一一个样子 ----------
def test_make_llm_picks_the_right_kind(tmp_path):
    from fsw.config import Config
    cfg = Config(config_dir=tmp_path, llm_provider="deepseek", deepseek_api_key=FAKE_KEY)
    assert isinstance(make_llm(cfg), DeepSeek)
    cfg.llm_provider = "claude-api"
    cfg.claude_api_key = FAKE_KEY
    assert isinstance(make_llm(cfg), ClaudeAPI)
    cfg.llm_provider = "claude-code"
    assert isinstance(make_llm(cfg), ClaudeCode)


def test_missing_key_is_a_plain_error(tmp_path):
    from fsw.config import Config
    with pytest.raises(LLMError) as e:
        make_llm(Config(config_dir=tmp_path, llm_provider="deepseek"))
    assert "密钥" in str(e.value)
