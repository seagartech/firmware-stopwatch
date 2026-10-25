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
MAX_RETRIES = 2  # 每次请求最多重发 2 次


def retryable(e):
    """发请求阶段出的、可以放心重发的错（对方还没开始回答，重发不会多算钱）：
    连接被重置或中断、TLS 中途断开、代理断开、查不到域名，以及 HTTP 429/502/503。
    超时不重发：urllib 分不清是连不上还是连上后在等回答（后者重发会重复计费）；证书不对也不重发（重发也一样）。"""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in RETRY_STATUS
    r = e.reason if isinstance(e, urllib.error.URLError) else e
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
            "busy": f"{self.name}那边现在太忙，过一会儿再试。",
            "refused": f"{self.name}拒绝回答这次请求。",
            "not_installed": "这台电脑上没找到 Claude Code。装好并登录后再选这一项，或者改用填密钥的方式。",
            "not_logged_in": "找到了 Claude Code，但还没登录：在终端里运行 claude，按提示登录一次。",
            "timeout": f"{self.name}太久没读完（超过 {getattr(self, 'timeout', 900) // 60} 分钟），码表先停下了。点重试再来一次。",
            "slow": f"{self.name}太久没有回复，码表先停下了：可能是网络慢或服务器忙，过一会儿点重试。",
            "dropped": f"和{self.name}的连接中途断了：看看网络稳不稳，再点重试。",
        }.get(kind, f"{self.name}返回了错误：{detail[:200]}")

    def fail(self, kind, detail="", **info):
        return LLMError(kind, self._say(kind, redact(detail)), detail, **info)

    def _read_error(self, e):
        """读回复途中出的错（超时、断连、坏行）：归成大模型错误，原始报错留在 detail 里。"""
        detail = f"{type(e).__name__}: {e}"
        if isinstance(e, TimeoutError):
            return LLMError("slow", self._say("slow"), detail)
        if isinstance(e, (OSError, http.client.HTTPException)):
            return LLMError("dropped", self._say("dropped"), detail)
        return LLMError("other", self._say("other", "回复的格式不对"), detail)

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        """自己读源码目录（只读）再回答，返回最后的完整回答。on_text 收到一段段文字，on_tool 收到"读了什么"。"""
        raise NotImplementedError

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

    def __init__(self, reply, chunk=8, test_kind=None, tool_calls=None):
        self.reply, self.chunk, self.test_kind, self.prompts = reply, chunk, test_kind, []
        self.tool_calls = tool_calls or []

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
                break
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
            results = []
            for u in uses:
                on_tool and on_tool(describe(u.name, u.input))
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": tools.call(u.name, u.input)})
            messages.append({"role": "user", "content": results})
        return "".join(final)

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

    def __init__(self, key, model=DEEPSEEK_MODEL, base_url="https://api.deepseek.com", timeout=600, retry_wait=(1, 2)):
        self.key, self.model, self.base_url, self.timeout = key, model, base_url.rstrip("/"), timeout
        self.retry_wait = retry_wait  # 第 1、2 次重发前各等几秒
        self.retry_log = []  # 这次调用里重发过的请求：[{request_no, retry, why, wait}]，成功的也记，看得出网络稳不稳
        self._req_no = None

    def _post(self, body):
        """发一次请求；发请求阶段的连接类错误和 429/502/503 等一会儿重发，最多 MAX_RETRIES 次。读回答途中出的错不在这里，不重发。"""
        data = json.dumps(body).encode()
        for attempt in range(MAX_RETRIES + 1):
            req = urllib.request.Request(self.base_url + "/chat/completions", data=data,
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except (urllib.error.URLError, OSError) as e:
                if attempt < MAX_RETRIES and retryable(e):
                    wait = self.retry_wait[min(attempt, len(self.retry_wait) - 1)]
                    why = redact(f"HTTPError {e.code}" if isinstance(e, urllib.error.HTTPError) else _why(e), 200)
                    self.retry_log.append({"request_no": self._req_no, "retry": attempt + 1, "why": why, "wait": wait})
                    print(f"[大模型] {self.name} 第 {self._req_no or '?'} 次请求出错（{why}），{wait} 秒后重发（第 {attempt + 1} 次重发）",
                          file=sys.stderr, flush=True)
                    time.sleep(wait)
                    continue
                err = self._error(e)
                err.info["retries"] = attempt
                raise err from e

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
            if isinstance(getattr(e, "reason", e), TimeoutError):  # 连上了但等不到回复：不是没联网
                return self._read_error(getattr(e, "reason", e))
            return LLMError("offline", self._say("offline"), _why(e))

    def investigate(self, prompt, root, on_text=None, on_tool=None):
        tools, messages, t0 = SourceTools(root), [{"role": "user", "content": prompt}], time.time()
        self.retry_log = []
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            self._req_no = rnd + 1
            body = {"model": self.model, "messages": messages}
            if rnd < MAX_TOOL_ROUNDS:
                body["tools"] = openai_tools()
            else:
                messages.append({"role": "user", "content": "读代码的次数用完了，请按已读的内容直接给出最终结果。"})
            sent = time.time()
            try:
                with self._post(body) as resp:
                    try:
                        msg = json.loads(resp.read())["choices"][0]["message"]
                    except (ValueError, KeyError, IndexError, TypeError, OSError, http.client.HTTPException) as e:
                        raise self._read_error(e) from e  # 读回复时超时、断开、格式不对，都按大模型的错说
            except LLMError as e:  # 记下是第几次请求、这次请求等了多久、这次调用一共用了多久
                e.info.update(request_no=rnd + 1, request_seconds=round(time.time() - sent, 1), elapsed=round(time.time() - t0, 1))
                raise
            if msg.get("content"):
                on_text and on_text(msg["content"])
            calls = msg.get("tool_calls") or []
            if not calls:
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

    def stream(self, prompt, system=None):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        resp = self._post({"model": self.model, "messages": msgs, "stream": True})
        with resp:
            try:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    piece = (json.loads(data).get("choices") or [{}])[0].get("delta", {}).get("content")
                    if piece:
                        yield piece
            except (ValueError, AttributeError, IndexError, TypeError, OSError, http.client.HTTPException) as e:
                raise self._read_error(e) from e  # 读到一半超时、断开，或者流里有坏行

    def test(self):
        self.timeout = 30
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
