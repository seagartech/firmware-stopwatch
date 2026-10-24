"""调用大模型出错时留下原始报错：异常类型和原话、HTTP 状态码、第几次请求、哪一步、用时；页面提示照旧好懂；不留密钥。
全部用假的网络（替换 urlopen），不联网、不花钱。"""

import io
import json
import socket
import ssl
import urllib.error

import pytest

from fsw import identify as idf
from fsw import llm as llms
from fsw.llm import DeepSeek, LLMError, redact
from tests.identify_fixtures import door_source

KEY = "sk" + "-test0123456789abcdef"  # 拼起来写：仓库自查不把它当成真密钥


class Resp:
    def __init__(self, data, fail_read=None):
        self.data, self.fail_read = data, fail_read

    def read(self, *a):
        if self.fail_read:
            raise self.fail_read
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def tool_reply():
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "t1", "type": "function", "function": {"name": "list_dir", "arguments": "{\"path\": \".\"}"}}]}}]}


def fake_net(monkeypatch, fail_at, error=None, fail_read=None):
    """前 fail_at-1 次请求正常（模型要读目录），第 fail_at 次出错。"""
    sent = []

    def urlopen(req, timeout=None):
        sent.append(req)
        assert req.get_header("Authorization") == f"Bearer {KEY}"
        if len(sent) == fail_at:
            if error:
                raise error
            return Resp(None, fail_read=fail_read)
        return Resp(tool_reply())
    monkeypatch.setattr(llms.urllib.request, "urlopen", urlopen)
    return sent


def http_error(code, body):
    return urllib.error.HTTPError("https://api.deepseek.com/chat/completions", code, "x", {}, io.BytesIO(body.encode()))


@pytest.mark.parametrize("error,fail_read,kind,detail", [
    (ConnectionResetError(54, "Connection reset by peer"), None, "offline", "ConnectionResetError: [Errno 54] Connection reset by peer"),
    (urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol")), None, "offline", "SSLEOFError"),
    (urllib.error.URLError(OSError("Tunnel connection failed: 502 Bad Gateway")), None, "offline", "Tunnel connection failed: 502"),
    (urllib.error.URLError(socket.timeout("timed out")), None, "slow", "timed out"),
    (None, ConnectionResetError(54, "Connection reset by peer"), "dropped", "ConnectionResetError"),
    (None, TimeoutError("The read operation timed out"), "slow", "TimeoutError: The read operation timed out"),
    (http_error(503, '{"error": "server overloaded"}'), None, "busy", "HTTPError 503: {\"error\": \"server overloaded\"}"),
    (http_error(401, f'{{"error": "bad key {KEY}", "auth": "Bearer {KEY}"}}'), None, "bad_key", "HTTPError 401"),
])
def test_deepseek_failure_keeps_type_message_status_and_request_no(monkeypatch, tmp_path, error, fail_read, kind, detail):
    door = door_source(tmp_path / "door")
    fake_net(monkeypatch, 3, error, fail_read)
    with pytest.raises(LLMError) as got:
        DeepSeek(KEY).investigate("读一读", door)
    e = got.value
    assert e.kind == kind and detail in e.detail, e.detail
    assert e.info["request_no"] == 3 and e.info["elapsed"] >= 0 and "request_seconds" in e.info
    if isinstance(error, urllib.error.HTTPError):
        assert e.info["status"] == error.code
    for text in (e.detail, e.message, json.dumps(e.info)):
        assert KEY[:7] not in text and KEY not in text
    assert "Errno" not in e.message and "HTTPError" not in e.message  # 页面提示仍是好懂的话


def test_identify_failure_record_and_log(monkeypatch, tmp_path, capsys):
    """整个识别：第一步第 3 次请求断开。结果里有出错记录，运行日志里有一行原话；都不带密钥、不带请求内容。"""
    door = door_source(tmp_path / "door")
    sent = fake_net(monkeypatch, 3, urllib.error.URLError(ConnectionResetError(54, f"Connection reset by peer {KEY}")))
    r = idf.identify(DeepSeek(KEY), door, "从刷卡到开门")
    assert not r["ok"] and r["kind"] == "offline" and r["stage"] == "chain" and len(sent) == 3
    err = r["error"]
    assert err["stage"] == "理清流程" and err["request_no"] == 3 and err["kind"] == "offline"
    assert "ConnectionResetError" in err["detail"] and "Connection reset by peer" in err["detail"] and "sk-***" in err["detail"]
    assert err["stage_seconds"] >= 0 and err["seconds"] >= err["stage_seconds"]
    assert "连不上DeepSeek的服务器" in r["message"] and "ConnectionResetError" not in r["message"]
    saved = json.dumps(r, ensure_ascii=False)
    log = capsys.readouterr().err
    assert "[识别] 理清流程出错：" in log and "第 3 次请求" in log and "ConnectionResetError" in log
    for text in (saved, log):
        assert KEY not in text and "Authorization" not in text and "Bearer" not in text
        assert "读源码" not in text and "list_dir" not in text  # 不带请求内容


def test_second_stage_failure_names_the_second_stage(monkeypatch, tmp_path):
    from tests.identify_fixtures import DOOR_RESULT
    door = door_source(tmp_path / "door")
    flow = idf.check_chain(door, {k: DOOR_RESULT[k] for k in ("trigger", "upload", "mode", "detect", "chain")})[0]
    fake_net(monkeypatch, 1, http_error(429, "rate limited"))
    r = idf.identify(DeepSeek(KEY), door, "从刷卡到开门", flow=flow)
    assert r["kind"] == "busy" and r["error"]["stage"] == "选计时点" and r["error"]["status"] == 429 and r["error"]["request_no"] == 1


def test_redact():
    assert redact(f"key={KEY} and Bearer abc.def x-api-key: zzz Authorization: Bearer qq") == \
        "key=sk-*** and Bearer *** x-api-key *** Authorization ***"
    assert len(redact("x" * 2000)) <= 601


def test_web_job_keeps_the_error_record(tmp_path):
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "fsw/web/app.py").read_text(encoding="utf-8")
    assert 'job["error_info"] = r["error"]' in src
