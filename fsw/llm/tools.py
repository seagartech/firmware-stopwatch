"""给大模型读源码用的三个只读工具：看目录、读文件（带行号）、搜索。只能看源码目录里面，不能改任何东西。

验证期的盲测小程序里用过同样的三个工具，这里照搬。
Claude Code 自带读代码的工具，不用这里；Claude（API 密钥）和 DeepSeek 用这里。
"""

import re
from pathlib import Path

MAX_READ_LINES = 400
MAX_GREP_HITS = 200
MAX_LIST_ENTRIES = 500
SKIP = {".git", "build", "managed_components", "node_modules"}

SPECS = [
    ("list_dir", "列出源码里某个目录的内容。路径相对于源码根目录，'.' 表示根目录。",
     {"path": {"type": "string"}}, ["path"]),
    ("read_file", f"读取源码文件的一段，返回带行号的内容。行号从 1 开始，单次最多 {MAX_READ_LINES} 行。",
     {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
    ("grep", "在源码目录或文件里按正则表达式搜索，返回 文件:行号:内容。",
     {"pattern": {"type": "string"}, "path": {"type": "string", "description": "相对路径，默认 '.'"}}, ["pattern"]),
]


def openai_tools():
    return [{"type": "function", "function": {"name": n, "description": d,
                                              "parameters": {"type": "object", "properties": p, "required": r}}}
            for n, d, p, r in SPECS]


def anthropic_tools():
    return [{"name": n, "description": d, "input_schema": {"type": "object", "properties": p, "required": r}}
            for n, d, p, r in SPECS]


def describe(name, args):
    """给进度显示用的一句：read_file main/app.c"""
    return f"{name} {args.get('path') or args.get('pattern') or ''}".strip()


class SourceTools:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _resolve(self, rel):
        p = (self.root / (rel or ".")).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError("路径不在源码目录内，拒绝访问")
        return p

    def _rel(self, p):
        return p.relative_to(self.root).as_posix() or "."

    def list_dir(self, path="."):
        p = self._resolve(path)
        if not p.is_dir():
            return f"不是目录：{path}"
        entries = sorted(e for e in p.iterdir() if e.name not in SKIP)
        lines = [self._rel(e) + ("/" if e.is_dir() else "") for e in entries[:MAX_LIST_ENTRIES]]
        if len(entries) > MAX_LIST_ENTRIES:
            lines.append(f"……共 {len(entries)} 项，只显示前 {MAX_LIST_ENTRIES} 项")
        return "\n".join(lines)

    def read_file(self, path, start_line=1, end_line=None):
        p = self._resolve(path)
        if not p.is_file():
            return f"不是文件：{path}"
        text = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line or 1))
        end = int(end_line) if end_line else start + MAX_READ_LINES - 1
        end = min(end, start + MAX_READ_LINES - 1, len(text))
        body = "\n".join(f"{i}\t{text[i - 1]}" for i in range(start, end + 1))
        return f"{self._rel(p)}（共 {len(text)} 行，显示 {start}-{end}）\n{body}"

    def grep(self, pattern, path="."):
        p = self._resolve(path)
        rx = re.compile(pattern)
        files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file() and not SKIP & set(f.relative_to(self.root).parts))
        hits = []
        for f in files:
            try:
                for n, line in enumerate(f.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{self._rel(f)}:{n}:{line.strip()[:200]}")
                        if len(hits) >= MAX_GREP_HITS:
                            return "\n".join(hits) + f"\n……超过 {MAX_GREP_HITS} 条，请缩小范围"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(hits) or "没有匹配"

    def call(self, name, args):
        try:
            if name == "list_dir":
                return self.list_dir(args.get("path", "."))
            if name == "read_file":
                return self.read_file(args["path"], args.get("start_line", 1), args.get("end_line"))
            if name == "grep":
                return self.grep(args["pattern"], args.get("path", "."))
            return f"没有这个工具：{name}"
        except Exception as e:  # 工具出错时把原因告诉模型，让它换个做法
            return f"出错：{e}"
