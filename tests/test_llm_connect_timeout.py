"""连接、TLS 握手阶段超时（请求还没发出去）和等回答超时分开：前者按连接类错误重发（最多 2 次），报"连接超时"；
后者不重发，报"等回答超时"；分不清阶段的按等回答超时。全用本机的假服务器或假的发请求函数，不连外网、不花钱。"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fsw import llm as llms
from fsw.llm import DeepSeek, LLMError

KEY = "sk" + "-test0123456789abcdef"
OK = {"choices": [{"message": {"role": "assistant", "content": "好了"}}]}


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    """本机测试不走系统代理；每个测试重新建发请求的 opener。"""
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(llms, "_openers", {})


def http_server(delay=0.0):
    """本机 HTTP 假服务器：读完请求，等 delay 秒再回 OK。返回 (地址, 收到几次请求, 关掉)。"""
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            got.append(self.headers.get("Authorization"))
            time.sleep(delay)
            body = json.dumps(OK).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", got, srv.shutdown


def silent_tls_server():
    """接受连接但一个字节都不回：TLS 握手卡住。返回 (https 地址, 接受了几次连接, 关掉)。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    conns, stop = [], threading.Event()

    def run():
        s.settimeout(0.1)
        while not stop.is_set():
            try:
                c, _ = s.accept()
                conns.append(c)
            except OSError:
                continue
    threading.Thread(target=run, daemon=True).start()

    def close():
        stop.set()
        for c in conns:
            c.close()
        s.close()
    return f"https://127.0.0.1:{s.getsockname()[1]}", conns, close


def test_tls_handshake_timeout_is_a_connect_timeout_and_is_retried(tmp_path, capsys):
    url, conns, close = silent_tls_server()
    try:
        m = DeepSeek(KEY, base_url=url, timeout=10, connect_timeout=0.3, retry_wait=(0, 0))
        t = time.time()
        with pytest.raises(LLMError) as got:
            m.investigate("读一读", tmp_path)
        took = time.time() - t
    finally:
        close()
    e = got.value
    assert len(conns) == 3 and took < 5  # 第一次 + 重发 2 次，每次 0.3 秒就放弃，不等 10 秒
    assert e.kind == "offline" and e.info["retries"] == 2 and e.info["timeout_phase"] == "connect"
    assert "连接" in e.message and "超时" in e.message and "等回答" not in e.message
    assert e.detail.startswith("连接超时") and "handshake" in e.detail and "等回答" not in e.detail
    assert [x["retry"] for x in m.retry_log] == [1, 2] and all("ConnectTimeout" in x["why"] for x in m.retry_log)
    assert KEY not in capsys.readouterr().err


def test_tcp_connect_timeout_is_retried_then_succeeds(monkeypatch, tmp_path):
    url, got, close = http_server()
    real, calls = socket.create_connection, []

    def flaky(addr, timeout=None, *a, **k):
        calls.append(timeout)
        if len(calls) <= 2:
            raise TimeoutError("timed out")  # 建 TCP 连接时超时
        return real(addr, timeout, *a, **k)
    monkeypatch.setattr(socket, "create_connection", flaky)
    try:
        m = DeepSeek(KEY, base_url=url, timeout=10, connect_timeout=0.3, retry_wait=(0, 0))
        assert m.investigate("读一读", tmp_path) == "好了"
    finally:
        close()
    assert calls == [0.3, 0.3, 0.3] and len(got) == 1  # 连接阶段用的是 0.3 秒的上限；请求只真正发出去一次
    assert [x["retry"] for x in m.retry_log] == [1, 2]


def test_read_timeout_is_retried_once_then_says_waiting_for_answer(tmp_path):
    url, got, close = http_server(delay=1.5)
    try:
        m = DeepSeek(KEY, base_url=url, timeout=0.5, connect_timeout=0.3, retry_wait=(0, 0))
        with pytest.raises(LLMError) as e:
            m.investigate("读一读", tmp_path)
    finally:
        close()
    assert len(got) == 2 and len(m.retry_log) == 1  # 等回答超时只重发 1 次（10-26：网络不稳时自己重试，但等回答超时多半是服务器那边）
    assert e.value.kind == "slow" and "等回答超时" in e.value.detail and e.value.info.get("timeout_phase") != "connect"


def test_connect_timeout_does_not_cut_a_slow_answer(tmp_path):
    """连接阶段的短上限只管连接和握手：连上之后等回答照旧用长的上限。"""
    url, got, close = http_server(delay=0.8)
    try:
        m = DeepSeek(KEY, base_url=url, timeout=5, connect_timeout=0.3, retry_wait=(0, 0))
        assert m.investigate("读一读", tmp_path) == "好了" and len(got) == 1
    finally:
        close()


@pytest.mark.parametrize("error", [
    urllib.error.URLError(TimeoutError("timed out")),  # 分不清在哪个阶段
    urllib.error.URLError(TimeoutError("The write operation timed out")),  # 发请求内容途中：可能已经发出去一部分
    TimeoutError("The read operation timed out"),
])
def test_unknown_phase_timeouts_count_as_waiting_for_answer(monkeypatch, tmp_path, error):
    sent = []

    def fake_open(req, timeout=None, ct=None):
        sent.append(req)
        raise error
    monkeypatch.setattr(llms, "_open", fake_open)
    with pytest.raises(LLMError) as e:
        DeepSeek(KEY, retry_wait=(0, 0)).investigate("读一读", tmp_path)
    assert len(sent) == 2 and e.value.kind == "slow" and "等回答超时" in e.value.detail and "连接超时" not in e.value.detail
    assert e.value.info["retries"] == 1


def test_default_limits():
    m = DeepSeek(KEY)
    assert m.timeout == 180 and m.connect_timeout == llms.CONNECT_TIMEOUT == 30
