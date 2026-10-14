"""大模型适配：Claude（API 密钥）、本机已登录的 Claude Code、DeepSeek 三种接法统一成一个样子。

每种都有：
  stream(prompt) → 边回答边一段段返回文字
  complete(prompt) → 等它答完，返回整段
  test() → 测试能不能用，不能用时分清是密钥错、没余额、连不上还是别的，说人话
密钥只在内存里用，不打印、不写进日志和返回的说法里。
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

TEST_PROMPT = "只回一个字：好"
CLAUDE_MODEL = "claude-opus-5-5"
DEEPSEEK_MODEL = "deepseek-chat"


class LLMError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


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
        }.get(kind, f"{self.name}返回了错误：{detail[:200]}")

    def fail(self, kind, detail=""):
        return LLMError(kind, self._say(kind, detail))

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

    def __init__(self, reply, chunk=8, test_kind=None):
        self.reply, self.chunk, self.test_kind, self.prompts = reply, chunk, test_kind, []

    def stream(self, prompt, system=None):
        self.prompts.append(prompt)
        if self.test_kind:
            raise self.fail(self.test_kind)
        for i in range(0, len(self.reply), self.chunk):
            yield self.reply[i:i + self.chunk]


class ClaudeAPI(LLM):
    name = "Claude"
    site = " Claude 开发者平台（console.anthropic.com）"

    def __init__(self, key, model=CLAUDE_MODEL, base_url=None, max_retries=2):
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=key, base_url=base_url, max_retries=max_retries, timeout=600)

    def stream(self, prompt, system=None):
        import anthropic
        kw = {"system": system} if system else {}
        try:
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
        except anthropic.AuthenticationError as e:
            raise self.fail("bad_key") from e
        except anthropic.PermissionDeniedError as e:
            raise self.fail("bad_key") from e
        except anthropic.RateLimitError as e:
            raise self.fail("busy") from e
        except anthropic.InternalServerError as e:
            raise self.fail("busy") from e
        except anthropic.APIStatusError as e:
            msg = str(getattr(e, "message", "") or e)
            if e.status_code == 402 or "credit balance" in msg.lower():
                raise self.fail("no_balance") from e
            raise self.fail("other", f"{e.status_code} {msg}") from e
        except anthropic.APIConnectionError as e:
            raise self.fail("offline") from e

    def test(self):
        self.client = self.client.with_options(max_retries=0, timeout=30)
        return super().test()


class DeepSeek(LLM):
    name = "DeepSeek"
    site = " DeepSeek 开放平台（platform.deepseek.com）"

    def __init__(self, key, model=DEEPSEEK_MODEL, base_url="https://api.deepseek.com", timeout=600):
        self.key, self.model, self.base_url, self.timeout = key, model, base_url.rstrip("/"), timeout

    def stream(self, prompt, system=None):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps({"model": self.model, "messages": msgs, "stream": True}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            kind = {401: "bad_key", 403: "bad_key", 402: "no_balance", 429: "busy"}.get(e.code, "busy" if e.code >= 500 else "other")
            raise self.fail(kind, f"{e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise self.fail("offline") from e
        with resp:
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


class ClaudeCode(LLM):
    """本机已登录的 Claude Code（用你自己的会员额度，不用密钥）。只调用官方的 claude 命令，不碰登录凭证。"""
    name = "Claude Code"

    def __init__(self, exe="auto", timeout=900):
        self.exe = find_claude() if exe == "auto" else exe
        self.timeout = timeout

    def stream(self, prompt, system=None):
        if not self.exe:
            raise self.fail("not_installed")
        cmd = [str(self.exe), "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--no-session-persistence"]
        if system:
            cmd += ["--system-prompt", system]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise self.fail("not_installed") from e
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
            elif msg.get("type") == "result":
                result = msg
        err = proc.stderr.read()
        proc.wait(timeout=self.timeout)
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
