"""大模型适配：Claude（API 密钥）、本机已登录的 Claude Code、DeepSeek 三种接法统一成一个样子。

每种都有：
  stream(prompt) → 边回答边一段段返回文字
  complete(prompt) → 等它答完，返回整段
  test() → 测试能不能用，不能用时分清是密钥错、没余额、连不上还是别的，说人话
  investigate(prompt, root) → 让它自己用只读工具读源码目录再回答（找关键步骤用），边读边报进度
密钥只在内存里用，不打印、不写进日志和返回的说法里。
"""

import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from fsw.llm.tools import SourceTools, anthropic_tools, describe, openai_tools

TEST_PROMPT = "只回一个字：好"
CLAUDE_MODEL = "claude-opus-5-5"
DEEPSEEK_MODEL = "deepseek-chat"
MAX_TOOL_ROUNDS = 80  # 读代码最多来回多少轮；用完就让它按已读的内容给结果
READ_ONLY = "Read,Grep,Glob"  # Claude Code 只开这三个读代码的工具，改文件、跑命令的都不给


_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]{4,}|(?i:bearer)\s+[^\s\"']+|(?i:x-api-key|authorization)[\"']?\s*[:=]\s*[\"']?(?:(?i:bearer)\s+)?[^\s\"',}]+")


def redact(text, limit=600):
    """存下、打出来的报错文字：去掉像密钥、Authorization 头的部分，太长的截断（不会带上整个请求）。"""
    t = _SECRET.sub(lambda m: "sk-***" if m.group().startswith("sk-") else m.group().split()[0].split(":")[0].split("=")[0] + " ***",
                    str(text or ""))
    return t if len(t) <= limit else t[:limit] + "…"


def _why(e):
    """异常类型和原话；urllib 把底层原因包在 URLError 里，把里面那层的类型也写出来（比如连接被重置、证书、代理）。"""
    r = getattr(e, "reason", None)
    if isinstance(e, urllib.error.URLError) and isinstance(r, BaseException):
        return f"{type(e).__name__}({type(r).__name__}): {r}"
    return f"{type(e).__name__}: {e}"


RETRY_STATUS = {429, 502, 503}  # 服务器说忙、网关临时出错：可以重发
# 网络不稳时重发（10-26 8783 真测：DeepSeek 连着几次 SSL EOF、RemoteDisconnected、IncompleteRead、握手超时，
# 原来每次请求只重发 2 次、读回答途中断了一次都不重发，整个识别 99 秒、392 秒后直接失败，用户只能手动重来）：
# - 等的时间越来越长：2、4、8、15、30、30 秒，最多重发 6 次；
# - 读回答途中断开（IncompleteRead、RemoteDisconnected、连接被重置、SSL 断开）也重发；
#   这种情况服务器可能已经算过一次钱，但不重发整个识别就白做了（用户 10-26 定：网络不稳时要自己重试）；
# - 等回答超时（单次 180 秒）只重发 1 次：再等 180 秒还没回，多半是服务器那边的问题；
# - 一次请求从第一次发出算起，超过 RETRY_WINDOW 秒就不再重发，报错（页面上写明最多试到多久）。
RETRY_WAITS = (2, 4, 8, 15, 30, 30)
MAX_RETRIES = len(RETRY_WAITS)
SLOW_RETRIES = 1
RETRY_WINDOW = 600  # 10 分钟


CONNECT_TIMEOUT = 30  # 连接、TLS 握手（含经过代理建隧道）的上限：正常 1-2 秒就好，卡住的早点放弃、重发


class ConnectTimeout(TimeoutError):
    """连接、TLS 握手阶段超时：请求还没发出去，重发不会重复计费。"""


def _phase_connection(base):
    class Conn(base):
        """连接和握手用较短的上限，连上后改回读回答的上限；连接阶段超时换成 ConnectTimeout，好和"等回答超时"分开。"""
        def __init__(self, *a, connect_timeout=None, **kw):
            super().__init__(*a, **kw)
            self.connect_timeout = connect_timeout

        def connect(self):
            read_timeout = self.timeout
            if self.connect_timeout and (read_timeout is None or not isinstance(read_timeout, (int, float))
                                         or self.connect_timeout < read_timeout):
                self.timeout = self.connect_timeout
            try:
                super().connect()
            except TimeoutError as e:  # 建连接、代理隧道、TLS 握手时超时（socket.timeout 也是它）
                raise ConnectTimeout(f"{e}（连接阶段，上限 {self.timeout} 秒）") from e
            finally:
                self.timeout = read_timeout
            if self.sock is not None and isinstance(read_timeout, (int, float)):
                self.sock.settimeout(read_timeout)
    Conn.__name__ = base.__name__
    return Conn


_HTTPSConn, _HTTPConn = _phase_connection(http.client.HTTPSConnection), _phase_connection(http.client.HTTPConnection)


class _PhaseHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, connect_timeout):
        super().__init__()
        self.ct = connect_timeout

    def https_open(self, req):
        return self.do_open(_HTTPSConn, req, context=self._context, connect_timeout=self.ct)


class _PhaseHTTP(urllib.request.HTTPHandler):
    def __init__(self, connect_timeout):
        super().__init__()
        self.ct = connect_timeout

    def http_open(self, req):
        return self.do_open(_HTTPConn, req, connect_timeout=self.ct)


_openers = {}


def _open(req, timeout, connect_timeout=None):
    """发请求：和 urllib.request.urlopen 一样（照样走系统代理），只是连接、握手阶段单独用 connect_timeout。"""
    if not connect_timeout:
        return urllib.request.urlopen(req, timeout=timeout)
    if connect_timeout not in _openers:
        _openers[connect_timeout] = urllib.request.build_opener(_PhaseHTTPS(connect_timeout), _PhaseHTTP(connect_timeout))
    return _openers[connect_timeout].open(req, timeout=timeout)


def retryable(e):
    """发请求阶段出的、可以放心重发的错（对方还没开始回答，重发不会多算钱）：
    连接被重置或中断、TLS 中途断开、代理断开、查不到域名，以及 HTTP 429/502/503。
    超时：只有确定是连接、TLS 握手阶段的（ConnectTimeout，请求还没发出去）才重发；分不清阶段的一律按等回答超时，不重发
    （连上后在等回答时重发会重复计费）。证书不对也不重发（重发也一样）。"""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in RETRY_STATUS
    r = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(r, ConnectTimeout):
        return True
    if isinstance(r, (TimeoutError, ssl.SSLCertVerificationError)):
        return False
    if isinstance(r, (ConnectionError, ssl.SSLError, socket.gaierror)):
        return True
    return "tunnel connection failed" in str(r).lower() or "proxy" in str(r).lower()


class LLMError(Exception):
    def __init__(self, kind, message, detail="", **info):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = redact(detail)  # 原始报错（异常类型和原话），放进页面的"详情"和记录，不放进提示
        self.info = info  # 补充：status（HTTP 状态码）、request_no（这次读代码的第几次请求）、elapsed（这次调用已用秒数）


class LLM:
    name = "大模型"
    site = ""

    def stream(self, prompt, system=None):
        raise NotImplementedError

    def complete(self, prompt, system=None):
        return "".join(self.stream(prompt, system=system))

    def _say(self, kind, detail=""):
        return {
            "bad_key": f"密钥不对：{self.name}不认这个密钥。看看有没有复制完整、前后有没有多空格，或者去{self.site}重新建一个。",
            "no_balance": f"密钥是对的，但账户余额不够了：去{self.site}充值后再试。",
            "offline": f"连不上{self.name}的服务器：看看电脑有没有联网，或者是不是要开代理。",
            "connect_timeout": f"连接{self.name}的服务器超时（连接、握手阶段卡住，请求还没发出去）：看看网络和代理稳不稳，再点重试。",
            "busy": f"{self.name}那边现在太忙，过一会儿再试。",
            "refused": f"{self.name}拒绝回答这次请求。",
            "not_installed": "这台电脑上没找到 Claude Code。装好并登录后再选这一项，或者改用填密钥的方式。",
            "not_logged_in": "找到了 Claude Code，但还没登录：在终端里运行 claude，按提示登录一次。",
            "timeout": f"{self.name}太久没读完（超过 {getattr(self, 'timeout', 900) // 60} 分钟），码表先停下了。点重试再来一次。",
            "slow": f"{self.name}太久没有回复{self._limit_text()}，码表先停下了：可能是网络慢或服务器忙，过一会儿点重试。",
            "dropped": f"和{self.name}的连接中途断了：看看网络稳不稳，再点重试。",
        }.get(kind, f"{self.name}返回了错误：{detail[:200]}")

    def _limit_text(self):
        t = getattr(self, "request_timeout", None)
        return f"（一次请求等了 {t} 秒还没回完）" if t else ""

    def fail(self, kind, detail="", **info):
        return LLMError(kind, self._say(kind, redact(detail)), detail, **info)

    def _read_error(self, e):
        """读回复途中出的错（超时、断连、坏行）：归成大模型错误，原始报错留在 detail 里。"""
        detail = f"{type(e).__name__}: {e}"
        if isinstance(e, TimeoutError):
            t = getattr(self, "request_timeout", None)
            if t:  # 写明是读回答超时、上限多少：和"连不上"分开
                detail += f"（等回答超时：单次请求上限 {t} 秒）"
                return LLMError("slow", self._say("slow"), detail, timeout_limit=t)
            return LLMError("slow", self._say("slow"), detail)
        if isinstance(e, (OSError, http.client.HTTPException)):
            return LLMError("dropped", self._say("dropped"), detail)
        return LLMError("other", self._say("other", "回复的格式不对"), detail)

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        """自己读源码目录（只读）再回答，返回最后的完整回答。on_text 收到一段段文字，on_tool 收到"读了什么"。"""
        raise NotImplementedError

    def followup(self, text):
        """在上一次 investigate 的同一段对话里再问一句（不给读代码的工具），返回回答。
        接法不支持（比如 Claude Code 命令行）就返回 None。"""
        return None

    def test(self):
        t = time.time()
        try:
            reply = self.complete(TEST_PROMPT).strip()
        except LLMError as e:
            return {"ok": False, "kind": e.kind, "message": e.message}
        return {"ok": True, "kind": "ok", "message": "✓ 能用", "reply": reply, "seconds": round(time.time() - t, 1)}


class FakeLLM(LLM):
    """录好回复的假模型，给自动测试用，不花钱、不联网。"""
    name = "假模型"

    def __init__(self, reply, chunk=8, test_kind=None, tool_calls=None, followups=None):
        self.reply, self.chunk, self.test_kind, self.prompts = reply, chunk, test_kind, []
        self.tool_calls = tool_calls or []
        self.followups, self.asked = followups, []  # followups：追问时依次给的回答（None 表示不支持追问）

    def followup(self, text):
        if self.followups is None:
            return None
        self.asked.append(text)
        return self.followups.pop(0) if self.followups else ""

    def stream(self, prompt, system=None):
        self.prompts.append(prompt)
        if self.test_kind:
            raise self.fail(self.test_kind)
        for i in range(0, len(self.reply), self.chunk):
            yield self.reply[i:i + self.chunk]

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        tools = SourceTools(root)
        for name, args in self.tool_calls:
            tools.call(name, args)
            on_tool and on_tool(describe(name, args))
        text = []
        for piece in self.stream(prompt):
            text.append(piece)
            on_text and on_text(piece)
        return "".join(text)


class ClaudeAPI(LLM):
    name = "Claude"
    site = " Claude 开发者平台（console.anthropic.com）"

    def __init__(self, key, model=CLAUDE_MODEL, base_url=None, max_retries=2):
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=key, base_url=base_url, max_retries=max_retries, timeout=600)

    @contextmanager
    def _errors(self):
        import anthropic
        try:
            yield
        except anthropic.AuthenticationError as e:
            raise self.fail("bad_key", f"{type(e).__name__}: {e}", status=getattr(e, "status_code", None)) from e
        except anthropic.PermissionDeniedError as e:
            raise self.fail("bad_key", f"{type(e).__name__}: {e}", status=getattr(e, "status_code", None)) from e
        except anthropic.RateLimitError as e:
            raise self.fail("busy", f"{type(e).__name__}: {e}", status=getattr(e, "status_code", None)) from e
        except anthropic.InternalServerError as e:
            raise self.fail("busy", f"{type(e).__name__}: {e}", status=getattr(e, "status_code", None)) from e
        except anthropic.APIStatusError as e:
            msg = str(getattr(e, "message", "") or e)
            if e.status_code == 402 or "credit balance" in msg.lower():
                raise self.fail("no_balance", f"{type(e).__name__} {e.status_code}: {msg}", status=e.status_code) from e
            raise self.fail("other", f"{e.status_code} {msg}", status=e.status_code) from e
        except anthropic.APITimeoutError as e:  # 是它的子类，要放在"连不上"前面：超时不是没联网
            raise LLMError("slow", self._say("slow"), str(e)) from e
        except anthropic.APIConnectionError as e:
            raise LLMError("offline", self._say("offline"), str(e)) from e
        except Exception as e:  # 边答边传的途中超时、断开：SDK 不包，原样抛它底下网络库的异常
            if type(e).__module__.split(".")[0] not in ("httpx", "httpx2", "httpcore"):
                raise
            kind = "slow" if "Timeout" in type(e).__name__ else "dropped"
            raise LLMError(kind, self._say(kind), f"{type(e).__name__}: {e}") from e

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        tools, messages, final, t0 = SourceTools(root), [{"role": "user", "content": prompt}], [], time.time()
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            kw = {"tools": anthropic_tools()} if rnd < MAX_TOOL_ROUNDS else {}
            if rnd == MAX_TOOL_ROUNDS:
                messages.append({"role": "user", "content": "读代码的次数用完了，请按已读的内容直接给出最终结果。"})
            sent = time.time()
            try:
                with self._errors():
                    msg = self.client.messages.create(model=self.model, max_tokens=16000, messages=messages, **kw)
            except LLMError as e:
                e.info.update(request_no=rnd + 1, request_seconds=round(time.time() - sent, 1), elapsed=round(time.time() - t0, 1))
                raise
            if msg.stop_reason == "refusal":
                raise self.fail("refused")
            texts = [b.text for b in msg.content if b.type == "text"]
            for t in texts:
                on_text and on_text(t)
            uses = [b for b in msg.content if b.type == "tool_use"]
            if not uses:
                final = texts
                self._talk = messages + [{"role": "assistant", "content": "".join(texts) or "（空）"}]
                break
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
            results = []
            for u in uses:
                on_tool and on_tool(describe(u.name, u.input))
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": tools.call(u.name, u.input)})
            messages.append({"role": "user", "content": results})
        return "".join(final)

    def followup(self, text):
        if not getattr(self, "_talk", None):
            return None
        messages = self._talk + [{"role": "user", "content": text}]
        with self._errors():
            msg = self.client.messages.create(model=self.model, max_tokens=16000, messages=messages)
        out = "".join(b.text for b in msg.content if b.type == "text")
        self._talk = messages + [{"role": "assistant", "content": out or "（空）"}]
        return out

    def stream(self, prompt, system=None):
        kw = {"system": system} if system else {}
        with self._errors():
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=64000,
                messages=[{"role": "user", "content": prompt}],
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},  # 被安全规则拦下时，服务器换推荐的模型接着答
                **kw,
            ) as s:
                yield from s.text_stream
                if s.get_final_message().stop_reason == "refusal":
                    raise self.fail("refused")

    def test(self):
        self.client = self.client.with_options(max_retries=0, timeout=30)
        return super().test()


class DeepSeek(LLM):
    name = "DeepSeek"
    site = " DeepSeek 开放平台（platform.deepseek.com）"

    # 单次请求的上限：以前的复测里一次请求一般 2-4 秒，最长的（写最终结果那次）约 34 秒；
    # 180 秒给长回答留了 5 倍余量，卡住的请求也不会白等 10 分钟。
    # 连接、握手另有 30 秒上限（CONNECT_TIMEOUT）：正常 1-2 秒，卡住的早点放弃并重发，不白等 180 秒。
    def __init__(self, key, model=DEEPSEEK_MODEL, base_url="https://api.deepseek.com", timeout=180, retry_wait=RETRY_WAITS,
                 connect_timeout=CONNECT_TIMEOUT, retry_window=RETRY_WINDOW, slow_retries=SLOW_RETRIES):
        self.key, self.model, self.base_url, self.timeout = key, model, base_url.rstrip("/"), timeout
        self.connect_timeout = connect_timeout
        self.request_timeout = timeout
        self.retry_wait = tuple(retry_wait)  # 第 1、2、3… 次重发前各等几秒；有几个数就最多重发几次
        self.retry_window = retry_window
        self.slow_retries = slow_retries
        self.retry_log = []  # 这次调用里重发过的请求：[{request_no, retry, why, wait}]，成功的也记，看得出网络稳不稳
        self.on_retry = None  # 网页用：重发前告诉它第几次、为什么、等几秒（页面上写"网络不稳，正在重试第 N 次"）；重发后成功了再告诉一次 {"ok": True}
        self._req_no = None

    def _retry_kind(self, e):
        """这个错能不能重发：'net'（连接、握手、读回答途中断开）、'slow'（等回答超时）、None（不重发）。"""
        if isinstance(e, LLMError):
            return {"dropped": "net", "slow": "slow"}.get(e.kind) if e.info.get("phase") == "read" else None
        if isinstance(e, urllib.error.HTTPError):
            return "net" if e.code in RETRY_STATUS else None
        r = e.reason if isinstance(e, urllib.error.URLError) else e
        if isinstance(r, ssl.SSLCertVerificationError):
            return None
        if isinstance(r, ConnectTimeout):
            return "net"
        if isinstance(r, TimeoutError):  # 分不清阶段的超时：按等回答超时算
            return "slow"
        return "net" if retryable(e) else None

    def _with_retries(self, send):
        """send() 发一次请求并读完回答。网络类的错按 retry_wait 越等越久地重发；等回答超时最多重发 slow_retries 次；
        从第一次发出算起超过 retry_window 秒不再重发。最后还不行，报错里写重发过几次。"""
        t0, slow, n = time.time(), 0, 0
        while True:
            try:
                out = send()
                if n and self.on_retry:
                    self.on_retry({"ok": True, "request_no": self._req_no, "retries": n})
                return out
            except (urllib.error.URLError, OSError, LLMError) as e:
                kind = self._retry_kind(e)
                wait = self.retry_wait[n] if n < len(self.retry_wait) else None
                if kind == "slow":
                    slow += 1
                    if slow > self.slow_retries:
                        kind = None
                if not kind or wait is None or time.time() - t0 + wait > self.retry_window:
                    err = e if isinstance(e, LLMError) else self._error(e)
                    err.info["retries"] = n
                    if n:  # 自己重发过还不行：页面上说清楚已经试了多久，不是一出错就放弃
                        err.message = f"{err.message}（码表已自动重发 {n} 次、试了约 {max(1, round((time.time() - t0) / 60))} 分钟，还是不行）"
                        err.args = (err.message,)
                    if isinstance(e, LLMError):
                        raise
                    raise err from e
                n += 1
                why = e.detail if isinstance(e, LLMError) else \
                    redact(f"HTTPError {e.code}" if isinstance(e, urllib.error.HTTPError) else _why(e), 200)
                why = redact(why, 200)
                self.retry_log.append({"request_no": self._req_no, "retry": n, "why": why, "wait": wait})
                print(f"[大模型] {self.name} 第 {self._req_no or '?'} 次请求出错（{why}），{wait} 秒后重发（第 {n} 次重发）",
                      file=sys.stderr, flush=True)
                if self.on_retry:
                    try:
                        self.on_retry({"request_no": self._req_no, "retry": n, "max": len(self.retry_wait), "why": why,
                                       "wait": wait, "window": self.retry_window, "elapsed": round(time.time() - t0, 1)})
                    except Exception:  # 页面那边出错不能影响重发
                        pass
                time.sleep(wait)

    def _post(self, body):
        """发一次请求（不重发：重发在 _with_retries 里，连同读回答一起）。"""
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=data,
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        return _open(req, self.timeout, self.connect_timeout)

    def _ask(self, body):
        """发请求、读完整回答（非流式），网络不稳时重发。返回 message。"""
        def send():
            with self._post(body) as resp:
                try:
                    return json.loads(resp.read())["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError, OSError, http.client.HTTPException) as e:
                    err = self._read_error(e)  # 读回复时超时、断开、格式不对，都按大模型的错说
                    if isinstance(e, (OSError, http.client.HTTPException)):
                        err.info["phase"] = "read"  # 读到一半断开 / 超时：可以重发；格式不对不重发
                    raise err from e
        return self._with_retries(send)

    def _error(self, e):
        """把发请求时的错换成大模型错误。"""
        try:
            raise e
        except urllib.error.HTTPError as e:
            kind = {401: "bad_key", 403: "bad_key", 402: "no_balance", 429: "busy"}.get(e.code, "busy" if e.code >= 500 else "other")
            try:
                said = e.read(300).decode("utf-8", "replace")
            except Exception:  # 读不到服务器的说明也不要紧
                said = ""
            err = self.fail(kind, f"HTTPError {e.code}: {said}".strip())
            err.info["status"] = e.code
            return err
        except (urllib.error.URLError, OSError) as e:
            r = getattr(e, "reason", e)
            if isinstance(r, ConnectTimeout):  # 连接、握手阶段超时：说"连接超时"，不说等回答
                return LLMError("offline", self._say("connect_timeout"), f"连接超时：{type(r.__cause__ or r).__name__}: {r}",
                                timeout_phase="connect")
            if isinstance(getattr(e, "reason", e), TimeoutError):  # 连上了但等不到回复（或分不清阶段）：按等回答超时说
                return self._read_error(getattr(e, "reason", e))
            return LLMError("offline", self._say("offline"), _why(e))

    def investigate_json(self, prompt, root, on_text=None, on_tool=None):
        """Advice JSON mode, with the same read-only source tools and request limits."""
        return self.investigate(prompt, root, on_text=on_text, on_tool=on_tool, json_output=True)

    def investigate(self, prompt, root, on_text=None, on_tool=None, json_output=False):
        tools, messages, t0 = SourceTools(root), [{"role": "user", "content": prompt}], time.time()
        self.retry_log = []
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            self._req_no = rnd + 1
            body = {"model": self.model, "messages": messages}
            if json_output:
                body.update(response_format={"type": "json_object"}, temperature=0.1, max_tokens=8192)
            if rnd < MAX_TOOL_ROUNDS:
                body["tools"] = openai_tools()
            else:
                messages.append({"role": "user", "content": "读代码的次数用完了，请按已读的内容直接给出最终结果。"})
            sent = time.time()
            try:
                msg = self._ask(body)
            except LLMError as e:  # 记下是第几次请求、这次请求等了多久、这次调用一共用了多久
                e.info.update(request_no=rnd + 1, request_seconds=round(time.time() - sent, 1), elapsed=round(time.time() - t0, 1))
                raise
            if msg.get("content"):
                on_text and on_text(msg["content"])
            calls = msg.get("tool_calls") or []
            if not calls:
                self._talk = messages + [{"role": "assistant", "content": msg.get("content") or "（空）"}]
                return msg.get("content") or ""
            messages.append({k: v for k, v in msg.items() if v is not None})
            for tc in calls:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                on_tool and on_tool(describe(tc["function"]["name"], args))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tools.call(tc["function"]["name"], args)})
        return ""

    def followup(self, text):
        if not getattr(self, "_talk", None):
            return None
        messages, sent = self._talk + [{"role": "user", "content": text}], time.time()
        self._req_no = "追问"
        try:
            msg = self._ask({"model": self.model, "messages": messages})
        except LLMError as e:
            e.info.update(request_no="追问", request_seconds=round(time.time() - sent, 1))
            raise
        out = msg.get("content") or ""
        self._talk = messages + [{"role": "assistant", "content": out or "（空）"}]
        return out

    def stream(self, prompt, system=None):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": msgs, "stream": True}
        # 还没收到一个字之前断了（连接、握手、等第一段时断开）：按网络不稳重发；已经交出去一部分文字就不能重来了，直接报错
        resp = self._with_retries(lambda: self._first_piece(body))
        first, resp, it = resp
        with resp:
            if first:
                yield first
            try:
                for piece in it:
                    yield piece
            except (ValueError, AttributeError, IndexError, TypeError, OSError, http.client.HTTPException) as e:
                raise self._read_error(e) from e  # 读到一半超时、断开，或者流里有坏行

    def _first_piece(self, body):
        """流式：发请求并读到第一段文字（或读完）为止；返回 (第一段, 回应, 后面的段)。这之前断开可以放心重发。"""
        resp = self._post(body)

        def pieces():
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                piece = (json.loads(data).get("choices") or [{}])[0].get("delta", {}).get("content")
                if piece:
                    yield piece
        it = pieces()
        try:
            first = next(it, None)
        except (ValueError, AttributeError, IndexError, TypeError, OSError, http.client.HTTPException) as e:
            resp.close()
            err = self._read_error(e)
            if isinstance(e, (OSError, http.client.HTTPException)):
                err.info["phase"] = "read"
            raise err from e
        return first, resp, it

    def test(self):
        self.timeout = 30
        self.retry_wait, self.slow_retries = (1,), 0  # 设置页"检查密钥"要马上有答复：只重发 1 次
        return super().test()


def _version_key(p):
    m = re.search(r"claude-code/([\d.]+)/", str(p))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def find_claude(home=None, path_env=None):
    """找本机的 claude 命令：先看 PATH 和常见位置，再看 Claude 桌面版里自带的那份（取最新版本）。"""
    home = Path(home) if home else Path.home()
    hit = shutil.which("claude", path=path_env if path_env is not None else os.environ.get("PATH", ""))
    if hit:
        return Path(hit)
    for p in [home / ".claude/local/claude", home / ".local/bin/claude"]:
        if p.is_file() and os.access(p, os.X_OK):
            return p
    bundled = sorted(home.glob("Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"),
                     key=_version_key, reverse=True)
    return bundled[0] if bundled else None


def claude_code_status(exe="auto", timeout=10):
    """本机 Claude Code 找没找到、登没登录：只跑官方的 claude auth status，不调用模型、不花钱。
    它的输出里有登录邮箱，这里只取"登没登录"，别的不留。"""
    exe = find_claude() if exe == "auto" else exe
    if not exe:
        return {"found": False, "logged_in": False}
    try:
        out = subprocess.run([str(exe), "auth", "status"], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=timeout)
        logged = bool(json.loads(out.stdout or "{}").get("loggedIn"))
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        logged = False
    return {"found": True, "logged_in": logged}


class ClaudeCode(LLM):
    """本机已登录的 Claude Code（用你自己的会员额度，不用密钥）。只调用官方的 claude 命令，不碰登录凭证。"""
    name = "Claude Code"

    def __init__(self, exe="auto", timeout=900):
        self.exe = find_claude() if exe == "auto" else exe
        self.timeout = timeout

    def _run(self, cmd, cwd=None, on_tool=None, root=None):
        """跑 claude 命令，一段段交出文字；读了什么文件交给 on_tool。最后检查有没有出错。"""
        if not self.exe:
            raise self.fail("not_installed")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace",  # Windows 默认按 GBK 解码，中文会出错
                                    stdin=subprocess.DEVNULL, cwd=cwd, start_new_session=os.name != "nt")
        except OSError as e:
            raise self.fail("not_installed") from e
        killed = []
        watchdog = threading.Timer(self.timeout, lambda: (killed.append(1), _kill_all(proc)))
        watchdog.daemon = True
        watchdog.start()  # 太久没读完就停下，不让网页一直转圈
        got, result = False, None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("type") == "stream_event":
                ev = msg.get("event", {})
                d = ev.get("delta", {})
                if ev.get("type") == "content_block_delta" and d.get("type") == "text_delta":
                    got = True
                    yield d["text"]
            elif msg.get("type") == "assistant" and on_tool:
                for b in (msg.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        on_tool(f"{b.get('name')} {_short(b.get('input') or {}, root)}".strip())
            elif msg.get("type") == "result":
                result = msg
        err = proc.stderr.read()
        proc.wait()
        watchdog.cancel()
        if killed:
            raise self.fail("timeout")
        text = (result or {}).get("result") or err or ""
        if (result and result.get("is_error")) or proc.returncode:
            low = text.lower()
            if "login" in low or "logged in" in low or "auth" in low:
                raise self.fail("not_logged_in")
            if "credit" in low or "usage limit" in low:
                raise self.fail("no_balance")
            raise self.fail("other", text)
        if not got and result and result.get("result"):
            yield result["result"]

    def stream(self, prompt, system=None):
        cmd = [str(self.exe), "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--no-session-persistence"]
        if system:
            cmd += ["--system-prompt", system]
        yield from self._run(cmd)

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        """在源码目录里跑 Claude Code，只给读代码的三个工具（--restricted 还会关掉跑命令的工具、不读本机设置），
        改不了任何文件；会话不留在电脑上。"""
        root = Path(root).resolve()
        cmd = [str(self.exe), "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--no-session-persistence", "--restricted",
               "--tools", READ_ONLY, "--allowedTools", READ_ONLY, "--strict-mcp-config"]
        text = []
        for piece in self._run(cmd, cwd=str(root), on_tool=on_tool, root=root):
            text.append(piece)
            on_text and on_text(piece)
        return "".join(text)


def _kill_all(proc):
    """连它带起来的子进程一起停掉（只停一个，子进程还占着输出，读不到结尾）。"""
    try:
        if os.name != "nt":
            os.killpg(proc.pid, 9)
        else:
            proc.kill()
    except OSError:
        pass


def _short(inp, root):
    """Claude Code 读的是哪个文件 / 搜的什么：换成相对源码目录的路径。"""
    v = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
    if root and v.startswith("/"):
        try:
            return Path(v).resolve().relative_to(root).as_posix()
        except ValueError:
            return Path(v).name
    return v


def make(provider, key=None, cfg=None):
    """按设置选一种接法。provider：claude-api / claude-code / deepseek。"""
    if provider == "claude-code":
        return ClaudeCode()
    stored = cfg.api_key(provider) if cfg else None
    key = key or stored
    if provider == "claude-api":
        if not key:
            raise LLMError("no_key", "还没填 Claude 的密钥：到设置页填上。")
        return ClaudeAPI(key)
    if provider == "deepseek":
        if not key:
            raise LLMError("no_key", "还没填 DeepSeek 的密钥：到设置页填上。")
        return DeepSeek(key)
    raise LLMError("no_provider", "还没选大模型：到设置页选一个。")


def make_llm(cfg):
    return make(cfg.llm_provider, cfg=cfg)
