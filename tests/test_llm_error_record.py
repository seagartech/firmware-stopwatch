"""调用大模型出错时留下原始报错：异常类型和原话、HTTP 状态码、第几次请求、哪一步、用时；页面提示照旧好懂；不留密钥。
全部用假的网络（替换发请求的 _open，或连本机的假服务器），不联网、不花钱。"""

import io
import json
import re
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
    """前 fail_at-1 次请求正常（模型要读目录），从第 fail_at 次起一直出错（会重发的错重发也照样出错）。不真等。"""
    sent = []

    def urlopen(req, timeout=None):
        sent.append(req)
        assert req.get_header("Authorization") == f"Bearer {KEY}"
        if len(sent) >= fail_at:
            if error:
                raise error
            return Resp(None, fail_read=fail_read)
        return Resp(tool_reply())
    monkeypatch.setattr(llms, "_open", lambda req, timeout=None, ct=None: urlopen(req, timeout))
    monkeypatch.setattr(llms.time, "sleep", lambda s: None)
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
    assert not r["ok"] and r["kind"] == "offline" and r["stage"] == "chain" and len(sent) == 3 + llms.MAX_RETRIES  # 第 3 次请求重发满
    err = r["error"]
    assert err["stage"] == "理清流程" and err["request_no"] == 3 and err["kind"] == "offline" and err["retries"] == llms.MAX_RETRIES
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
    assert r["kind"] == "busy" and r["error"]["stage"] == "选计时点" and r["error"]["status"] == 429 and r["error"]["request_no"] == 1 \
        and r["error"]["retries"] == llms.MAX_RETRIES and [x["stage"] for x in r["retries"]] == ["选计时点"] * llms.MAX_RETRIES


def test_redact():
    assert redact(f"key={KEY} and Bearer abc.def x-api-key: zzz Authorization: Bearer qq") == \
        "key=sk-*** and Bearer *** x-api-key *** Authorization ***"
    assert len(redact("x" * 2000)) <= 601


def test_web_job_keeps_the_error_record(tmp_path):
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "fsw/web/app.py").read_text(encoding="utf-8")
    assert 'job["error_info"] = r["error"]' in src


# ---------- 有限重发：只重发发请求阶段的连接类错误和 429/502/503，最多 2 次；读回答途中出错不重发 ----------
def flaky_net(monkeypatch, errors):
    """errors：按发请求的顺序，每次要出的错（None 表示正常回答，"read" 表示回答读到一半断开）。用完后都正常。"""
    sent, seq = [], list(errors)

    def urlopen(req, timeout=None):
        sent.append(req)
        e = seq.pop(0) if seq else None
        if e == "read":
            return Resp(None, fail_read=ConnectionResetError(54, "Connection reset by peer"))
        if e is not None:
            raise e
        return Resp({"choices": [{"message": {"role": "assistant", "content": "好了"}}]})
    monkeypatch.setattr(llms, "_open", lambda req, timeout=None, ct=None: urlopen(req, timeout))
    monkeypatch.setattr(llms.time, "sleep", lambda s: sent.append(("sleep", s)))
    return sent


TLS = urllib.error.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"))


@pytest.mark.parametrize("error", [
    TLS, ConnectionResetError(54, "Connection reset by peer"), urllib.error.URLError(ConnectionRefusedError(61, "refused")),
    urllib.error.URLError(OSError("Tunnel connection failed: 502 Bad Gateway")), urllib.error.URLError(socket.gaierror(8, "nodename nor servname")),
])
def test_send_phase_connection_errors_are_retried(monkeypatch, tmp_path, capsys, error):
    sent = flaky_net(monkeypatch, [error, error])
    m = DeepSeek(KEY)
    assert m.investigate("读一读", tmp_path) == "好了"
    reqs = [x for x in sent if not isinstance(x, tuple)]
    assert len(reqs) == 3 and [x for x in sent if isinstance(x, tuple)] == [("sleep", 2), ("sleep", 4)]  # 越等越久
    assert [x["retry"] for x in m.retry_log] == [1, 2] and all(x["request_no"] == 1 for x in m.retry_log)
    log = capsys.readouterr().err
    assert "第 1 次请求出错" in log and "2 秒后重发（第 1 次重发）" in log and KEY not in log


@pytest.mark.parametrize("code,retried", [(429, True), (502, True), (503, True), (500, False), (401, False), (402, False)])
def test_http_status_retry(monkeypatch, tmp_path, code, retried):
    sent = flaky_net(monkeypatch, [http_error(code, "x")])
    m = DeepSeek(KEY)
    if retried:
        assert m.investigate("读一读", tmp_path) == "好了" and len(m.retry_log) == 1
    else:
        with pytest.raises(LLMError) as got:
            m.investigate("读一读", tmp_path)
        assert got.value.info["retries"] == 0 and got.value.info["status"] == code
        assert len([x for x in sent if not isinstance(x, tuple)]) == 1


def test_bad_certificate_is_not_retried(monkeypatch, tmp_path):
    sent = flaky_net(monkeypatch, [urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))])
    with pytest.raises(LLMError) as got:
        DeepSeek(KEY).investigate("读一读", tmp_path)
    assert got.value.info["retries"] == 0 and len(sent) == 1  # 证书不对：重发也一样


def test_unknown_phase_timeout_is_retried_only_once(monkeypatch, tmp_path):
    t = urllib.error.URLError(socket.timeout("timed out"))
    sent = flaky_net(monkeypatch, [t, t, t])
    with pytest.raises(LLMError) as got:
        DeepSeek(KEY).investigate("读一读", tmp_path)
    reqs = [x for x in sent if not isinstance(x, tuple)]
    assert got.value.kind == "slow" and got.value.info["retries"] == 1 and len(reqs) == 2


def test_mid_response_read_failure_is_retried(monkeypatch, tmp_path):
    """10-26 8783 真测：读回答途中 IncompleteRead / 连接被重置，整个识别直接失败。现在按网络不稳重发。"""
    sent = flaky_net(monkeypatch, ["read"])
    m = DeepSeek(KEY)
    assert m.investigate("读一读", tmp_path) == "好了"
    assert len([x for x in sent if not isinstance(x, tuple)]) == 2 and m.retry_log[0]["retry"] == 1
    assert "ConnectionResetError" in m.retry_log[0]["why"]


def test_gives_up_after_all_retries_and_records_it(monkeypatch, tmp_path, capsys):
    """整个识别：第一步第 1 次请求一直 TLS 断开 → 重发满 MAX_RETRIES 次后不再重发，出错记录写明重发了几次。"""
    n = llms.MAX_RETRIES
    door = door_source(tmp_path / "door")
    sent = flaky_net(monkeypatch, [TLS] * (n + 1))
    r = idf.identify(DeepSeek(KEY), door, "从刷卡到开门")
    assert not r["ok"] and r["error"]["retries"] == n and r["error"]["request_no"] == 1 and r["error"]["retried_before"] == n
    assert len([x for x in sent if not isinstance(x, tuple)]) == n + 1
    assert [x for x in sent if isinstance(x, tuple)] == [("sleep", w) for w in llms.RETRY_WAITS]
    assert [x["stage"] for x in r["retries"]] == ["理清流程"] * n and "SSLEOFError" in r["retries"][0]["why"]
    assert f"这次请求重发过 {n} 次" in capsys.readouterr().err
    assert KEY not in json.dumps(r, ensure_ascii=False)


def test_successful_retries_are_kept_in_the_result(monkeypatch, tmp_path):
    from tests.identify_fixtures import DOOR_REPLY
    door = door_source(tmp_path / "door")
    seq = [TLS, None, http_error(503, "busy"), None]  # 第一步重发 1 次成功；第二步重发 1 次成功
    replies = iter([DOOR_REPLY, DOOR_REPLY])

    def urlopen(req, timeout=None):
        e = seq.pop(0) if seq else None
        if e is not None:
            raise e
        return Resp({"choices": [{"message": {"role": "assistant", "content": next(replies)}}]})
    monkeypatch.setattr(llms, "_open", lambda req, timeout=None, ct=None: urlopen(req, timeout))
    monkeypatch.setattr(llms.time, "sleep", lambda s: None)
    r = idf.identify(DeepSeek(KEY), door, "从刷卡到开门")
    assert r["ok"] and [(x["stage"], x["request_no"], x["retry"]) for x in r["retries"]] == [("理清流程", 1, 1), ("选计时点", 1, 1)]
    assert "HTTPError 503" in r["retries"][1]["why"]


# ---------- 回答的 JSON 格式坏了：同一段对话里追问一次"只按格式重新输出"，不读源码；结果里记下追问过 ----------
def _broken(reply):
    head, block = reply.split("```json", 1)
    return head + "```json" + re.sub(r'",(\s*\n)', r'"\1', block, count=1)  # 少一个逗号：JSON 读不懂


def test_bad_json_in_each_stage_is_asked_once_to_reoutput(tmp_path, capsys):
    from fsw.llm import FakeLLM
    from tests.identify_fixtures import DOOR_REPLY
    door = door_source(tmp_path / "door")
    assert idf._json_block(_broken(DOOR_REPLY)) is None
    m = FakeLLM(_broken(DOOR_REPLY), followups=[DOOR_REPLY, DOOR_REPLY])
    r = idf.identify(m, door, "从刷卡到开门")
    assert r["ok"] and [(x["stage"], x["ok"]) for x in r["followups"]] == [("理清流程", True), ("选计时点", True)]
    assert m.asked == [idf.FORMAT_AGAIN] * 2 and "不要再读源码" in idf.FORMAT_AGAIN
    assert r["calls"] == 2 and r["reply_first"] == _broken(DOOR_REPLY)
    assert "理清流程的回答格式不对，追问一次重新输出：好了" in capsys.readouterr().err


def test_followup_at_most_once_and_failure_is_recorded(tmp_path):
    from fsw.llm import FakeLLM
    from tests.identify_fixtures import DOOR_REPLY
    door = door_source(tmp_path / "door")
    m = FakeLLM(_broken(DOOR_REPLY), followups=[_broken(DOOR_REPLY), DOOR_REPLY])
    r = idf.identify(m, door, "从刷卡到开门")
    assert not r["ok"] and r["kind"] == "bad_chain" and len(m.asked) == 1
    assert r["followups"] == [{"stage": "理清流程", "ok": False, "seconds": r["followups"][0]["seconds"]}]
    assert r["chain_reply_first"] == _broken(DOOR_REPLY)


def test_no_followup_when_json_is_fine_or_unsupported(tmp_path):
    from fsw.llm import FakeLLM
    from tests.identify_fixtures import DOOR_REPLY
    door = door_source(tmp_path / "door")
    m = FakeLLM(DOOR_REPLY, followups=[])
    r = idf.identify(m, door, "从刷卡到开门")
    assert r["ok"] and r["followups"] == [] and m.asked == []
    r = idf.identify(FakeLLM(_broken(DOOR_REPLY)), door, "从刷卡到开门")  # 不支持追问：照旧停下
    assert r["kind"] == "bad_chain" and r["followups"] == []


def test_deepseek_followup_continues_the_same_conversation_without_tools(monkeypatch, tmp_path):
    bodies = []
    replies = iter([{"choices": [{"message": {"role": "assistant", "content": "坏的"}}]},
                    {"choices": [{"message": {"role": "assistant", "content": "好的"}}]}])

    def urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        return Resp(next(replies))
    monkeypatch.setattr(llms, "_open", lambda req, timeout=None, ct=None: urlopen(req, timeout))
    m = DeepSeek(KEY)
    assert m.investigate("读一读", tmp_path) == "坏的"
    assert m.followup("只按格式重新输出") == "好的"
    last = bodies[-1]
    assert "tools" not in last and [x["role"] for x in last["messages"]] == ["user", "assistant", "user"]
    assert last["messages"][1]["content"] == "坏的" and last["messages"][2]["content"] == "只按格式重新输出"


# ---------- 单次请求上限 180 秒；读回答超时写清楚，不重发 ----------
def test_deepseek_request_timeout_is_180s_and_read_timeout_is_clear(monkeypatch, tmp_path):
    seen = []

    def urlopen(req, timeout=None):
        seen.append(timeout)
        return Resp(None, fail_read=TimeoutError("The read operation timed out"))
    monkeypatch.setattr(llms, "_open", lambda req, timeout=None, ct=None: urlopen(req, timeout))
    monkeypatch.setattr(llms.time, "sleep", lambda s: None)
    m = DeepSeek(KEY)
    with pytest.raises(LLMError) as got:
        m.investigate("读一读", tmp_path)
    e = got.value
    assert seen == [180, 180] and e.kind == "slow" and e.info["timeout_limit"] == 180 and e.info["retries"] == 1
    assert "等回答超时：单次请求上限 180 秒" in e.detail and "一次请求等了 180 秒还没回完" in e.message
    assert "TimeoutError" not in e.message
