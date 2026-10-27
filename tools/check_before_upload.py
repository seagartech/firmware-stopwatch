"""上传前把关：扫描要上传的文件里有没有密钥、本机路径、过程文档、私人信息。
在 git 仓库里还查全部历史提交：每次提交加进来的内容，以及作者、提交者、标签的邮箱
（推上去的是整个历史，删掉的旧内容和作者信息别人也能看到）。

用法：python tools/check_before_upload.py [要查的文件夹，默认是 repo/]
发现问题就列出来并以 1 退出；没问题以 0 退出。
不想公开的名字（真名、公司名等）一行一个写在
~/.config/firmware-stopwatch/upload_private_words.txt，这份清单本身不进 repo。
"""

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}

KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
KEY_FILE_NAMES = re.compile(r"(^|_)key\.txt$|^\.env(\..+)?$|^config\.toml$")

LOCAL_PATH_PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z]:\\+Users\\+[^\\\s\"']+"),
]

# 过程文档只留在项目根目录，不进 repo/
PROCESS_DIRS = {"milestones", "transcripts", "handoff"}
PROCESS_NAMES = [
    "decisions.md", "ideas-backlog.md", "AGENTS.md", "spec.md",
    "产品设计考量", "对外文案", "任务路线", "技术计划", "项目原则", "第一版需求",
    "页面总览", "异常处理清单", "设计回看",
    "milestones/", "transcripts/", "handoff/", "docs/规矩", "docs/设计", "docs/验证",
]
# 这两个文件里必须写出过程文档的名字才能检查它们，只对"过程文档"这一类放行
SELF_FILES = {"tools/check_before_upload.py", "tests/test_check_before_upload.py"}

# GitHub 给每个账号的匿名邮箱，只有用它当提交邮箱才不暴露真实邮箱；
# GitHub 网页合并使用 noreply@github.com，是公开的不收信地址；
# 云端 AI 提交用的 noreply@anthropic.com 是公开的不收信地址，不算私人信息（2026-10-21 用户定）
ANON_EMAIL = re.compile(r"@users\.noreply\.github\.com$|^noreply@anthropic\.com$|^noreply@github\.com$")

DEFAULT_PRIVATE_WORDS = Path.home() / ".config" / "firmware-stopwatch" / "upload_private_words.txt"


@dataclass
class Finding:
    file: str
    line: int
    kind: str
    snippet: str

    def __str__(self):
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"  {where}  【{self.kind}】{self.snippet}"


def _mask(text):
    return text[:6] + "…（已打码）" if len(text) > 6 else "…（已打码）"


def files_to_upload(root):
    """在 git 仓库里就按 git 的忽略清单算；不是仓库就整个文件夹扫。"""
    root = Path(root)
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout
        if (root / ".git").exists():
            return sorted(p for p in out.split("\0") if p and (root / p).is_file())
    except (OSError, subprocess.CalledProcessError):
        pass
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts)
    )


def _load_private_words(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [w.strip() for w in lines if w.strip() and not w.strip().startswith("#")]


def _git(root, *args):
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True,
            encoding="utf-8", errors="replace",
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def scan_history(root, private_words=()):
    """查全部历史提交。不是 git 仓库或还没有提交时什么也不查。"""
    root = Path(root)
    if not (root / ".git").exists():
        return []
    findings, seen = [], set()

    def add(where, kind, snippet):
        if (where, kind, snippet) not in seen:
            seen.add((where, kind, snippet))
            findings.append(Finding(where, 0, kind, snippet))

    people = _git(root, "log", "--all", "--format=%h%x00%an%x00%ae%x00%cn%x00%ce")
    tags = _git(root, "for-each-ref", "refs/tags", "--format=%(refname:short)%00%(taggername)%00%(taggeremail)")
    rows = [line.split("\0") for line in people.splitlines()]
    rows += [[t, n, e.strip("<>")] for t, n, e in (line.split("\0") for line in tags.splitlines()) if e]
    for row in rows:
        where = f"历史提交 {row[0]}"
        for name, email in zip(row[1::2], row[2::2]):
            if email and not ANON_EMAIL.search(email):
                add(where, "私人信息", f"提交邮箱不是 GitHub 匿名邮箱：{_mask(email)}")
            for w in private_words:
                if w in name:
                    add(where, "私人信息", _mask(w))

    commit, rel = "", ""
    for line in _git(root, "log", "--all", "-p", "--no-color", "--format=@@commit %h").splitlines():
        if line.startswith("@@commit "):
            commit = line[9:]
        elif line.startswith("+++ "):
            rel = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and rel:
            where = f"历史提交 {commit} {rel}"
            text = line[1:]
            for p in KEY_PATTERNS:
                for m in p.finditer(text):
                    add(where, "密钥", _mask(m.group(0)))
            for p in LOCAL_PATH_PATTERNS:
                for m in p.finditer(text):
                    add(where, "本机路径", m.group(0))
            for w in private_words:
                if w in text:
                    add(where, "私人信息", _mask(w))
    return findings


def scan(root, private_words_file=DEFAULT_PRIVATE_WORDS, history=True):
    root = Path(root)
    private_words = _load_private_words(private_words_file)
    findings = scan_history(root, private_words) if history else []
    for rel in files_to_upload(root):
        parts = rel.split("/")
        name = parts[-1]
        if KEY_FILE_NAMES.search(name):
            findings.append(Finding(rel, 0, "密钥", "这种文件一般放密钥或本机设置，不该上传"))
        if PROCESS_DIRS & set(parts[:-1]) or any(n in rel for n in PROCESS_NAMES):
            findings.append(Finding(rel, 0, "过程文档", "过程文档只留在本机项目文件夹"))
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for p in KEY_PATTERNS:
                for m in p.finditer(line):
                    findings.append(Finding(rel, no, "密钥", _mask(m.group(0))))
            for p in LOCAL_PATH_PATTERNS:
                for m in p.finditer(line):
                    findings.append(Finding(rel, no, "本机路径", m.group(0)))
            if rel not in SELF_FILES:
                for n in PROCESS_NAMES:
                    if n in line:
                        findings.append(Finding(rel, no, "过程文档", f"提到了「{n}」"))
            for w in private_words:
                if w in line:
                    findings.append(Finding(rel, no, "私人信息", _mask(w)))
    return findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent
    findings = scan(root)
    if not findings:
        print(f"上传前检查：没发现问题，可以上传。（查的是 {root.name}/，含历史提交）")
        return 0
    print(f"上传前检查：发现 {len(findings)} 处问题，先别上传：")
    for f in findings:
        print(f)
    print("改掉或移出这些内容后再查一次。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
