"""DeepSeek 网络不稳（10-26 8783 真测：SSL EOF、RemoteDisconnected、IncompleteRead、握手超时连着来，识别直接失败）：
越等越久地多重发几次，读回答途中断开也重发；页面上能看到"网络不稳，正在重试第 N 次"；超过总时限才报错。
全用假的发请求函数，不联网、不花钱。"""

import http.client
import json
import ssl
import urllib.error

import pytest

from fsw import llm as llms
from fsw.llm import DeepSeek, LLMError
from tests.test_llm_error_record import KEY, Resp

OK = {"choices": [{"message": {"role": "assistant", "content": "好了"}}]}


def net(monkeypatch, seq, clock=None):
    """seq：每次发请求的结果：异常（发请求时抛）、("read", 异常)（回答读到一半抛）、None（正常）。"""
    sent, sleeps = [], []

    def fake_open(req, timeout=None, ct=None):
        sent.append(req)
        e = seq.pop(0) if seq else None
        if isinstance(e, tuple):
            return Resp(None, fail_read=e[1])
        if e is not None:
            raise e
        return Resp(OK)
    monkeypatch.setattr(llms, "_open", fake_open)

    def sleep(s):
        sleeps.append(s)
        if clock is not None:
            clock[0] += s
    monkeypatch.setattr(llms.time, "sleep", sleep)
    if clock is not None:
        monkeypatch.setattr(llms.time, "time", lambda: clock[0])
    return sent, sleeps


SSL_EOF = urllib.error.URLError(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"))
REMOTE = http.client.RemoteDisconnected("Remote end closed connection without response")
INCOMPLETE = ("read", http.client.IncompleteRead(b""))
HANDSHAKE = urllib.error.URLError(llms.ConnectTimeout("_ssl.c:993: The handshake operation timed out（连接阶段，上限 30 秒）"))


def test_the_8783_mix_of_network_errors_is_ridden_out(monkeypatch, tmp_path):
    sent, sleeps = net(monkeypatch, [SSL_EOF, REMOTE, INCOMPLETE, HANDSHAKE, None])
    seen = []
    m = DeepSeek(KEY)
    m.on_retry = seen.append
    assert m.investigate("读一读", tmp_path) == "好了"
    assert len(sent) == 5 and sleeps == [2, 4, 8, 15]  # 越等越久
    assert [x["retry"] for x in seen[:-1]] == [1, 2, 3, 4] and seen[-1] == {"ok": True, "request_no": 1, "retries": 4}
    assert all(x["max"] == llms.MAX_RETRIES and x["window"] == llms.RETRY_WINDOW for x in seen[:-1])
    assert "IncompleteRead" in m.retry_log[2]["why"] and "RemoteDisconnected" in m.retry_log[1]["why"]


def test_gives_up_when_total_window_would_be_exceeded(monkeypatch, tmp_path):
    clock = [1000.0]
    sent, sleeps = net(monkeypatch, [SSL_EOF] * 20, clock=clock)
    m = DeepSeek(KEY, retry_window=20)
    with pytest.raises(LLMError) as got:
        m.investigate("读一读", tmp_path)
    assert sleeps == [2, 4, 8] and got.value.info["retries"] == 3  # 再等 15 秒就超过 20 秒了：不再重发
    assert got.value.kind == "offline"


def test_bad_reply_format_is_not_retried(monkeypatch, tmp_path):
    sent, _ = net(monkeypatch, [("read", ValueError("bad json"))])
    with pytest.raises(LLMError) as got:
        DeepSeek(KEY).investigate("读一读", tmp_path)
    assert len(sent) == 1 and got.value.kind == "other"


def test_stream_retries_before_first_piece_only(monkeypatch):
    class StreamResp:
        def __init__(self, lines, fail_at=None):
            self.lines, self.fail_at = lines, fail_at

        def __iter__(self):
            for i, l in enumerate(self.lines):
                if self.fail_at == i:
                    raise http.client.IncompleteRead(b"")
                yield l

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    piece = lambda t: ("data: " + json.dumps({"choices": [{"delta": {"content": t}}]})).encode()
    seq = [StreamResp([piece("x")], fail_at=0), StreamResp([piece("你"), piece("好"), b"data: [DONE]"])]
    monkeypatch.setattr(llms, "_open", lambda req, timeout=None, ct=None: seq.pop(0))
    monkeypatch.setattr(llms.time, "sleep", lambda s: None)
    assert DeepSeek(KEY).complete("hi") == "你好"
    seq[:] = [StreamResp([piece("你"), piece("好")], fail_at=1)]  # 已经交出去一段：不能重来，直接报错
    with pytest.raises(LLMError) as got:
        DeepSeek(KEY).complete("hi")
    assert got.value.kind == "dropped"


def test_web_shows_network_retry_message():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    app = (root / "fsw/web/app.py").read_text(encoding="utf-8")
    js = (root / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "网络不稳，正在重试第" in app and "IDENT.net" in js and "FSWJ.net" in js


def test_identify_page_says_network_unstable_while_retrying(monkeypatch, tmp_path):
    """找关键步骤时 DeepSeek 连接断开：页面状态里有"网络不稳，正在重试第 N 次"，重发成功后去掉。"""
    import threading
    import time
    from tests.identify_fixtures import DOOR_REPLY
    from tests.test_identify_api import prepared, wait
    from fastapi.testclient import TestClient
    from fsw.web.app import create_app
    go = threading.Event()
    seq = [SSL_EOF]

    def fake_open(req, timeout=None, ct=None):
        e = seq.pop(0) if seq else None
        if e is not None:
            raise e
        return Resp({"choices": [{"message": {"role": "assistant", "content": DOOR_REPLY}}]})
    monkeypatch.setattr(llms, "_open", fake_open)
    monkeypatch.setattr(llms.time, "sleep", lambda s: go.wait(5))
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: DeepSeek(KEY),
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [], idf_finder=lambda: []))
    c.post("/api/settings", json={"llm_provider": "cc"})
    src = prepared(c, tmp_path)
    c.post("/api/identify/start", json={"work": src["work"], "project_dir": src["project_dir"],
                                        "fingerprint": src["fingerprint"], "what": "从刷卡到开门"})
    t, j = time.time(), {}
    while time.time() - t < 5 and not j.get("net"):
        j = c.get("/api/identify").json()
        threading.Event().wait(0.02)  # time.sleep 换成了假的（llms.time 就是 time 模块）
    assert "DeepSeek 网络不稳，正在重试第 1 次" in j["net"] and "最多试 10 分钟" in j["net"]
    go.set()
    monkeypatch.setattr(llms.time, "sleep", lambda s: threading.Event().wait(min(s, 0.05)))
    j = wait(c)
    assert j["state"] == "done" and not j.get("net")
