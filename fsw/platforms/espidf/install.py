"""编译工具帮装：找不到乐鑫编译工具时，码表在后台调用乐鑫官方的安装方式（决策 48）。

Mac：官方 git 仓库 + 官方脚本 install.sh；Windows：官方安装管理器 EIM 的命令行版。
逐行读输出，换算成"第几步、这一步的百分比、最新一行日志"。失败说清卡在哪一步；重试时已经下好的不重下。
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# 乐鑫的正式版本（新的在前）；按项目要的最低版本挑最新的那个
KNOWN_VERSIONS = ["v6.1", "v6.0.1", "v5.5.1", "v5.4.2", "v5.3.3"]
IDF_REPO = "https://github.com/espressif/esp-idf.git"
STEPS = ["检查前提", "下载乐鑫编译工具 {v}", "下载编译器等工具", "装 Python 依赖", "试编译一次"]
OFFLINE = re.compile(r"Could not resolve host|Connection (timed out|refused|reset)|timed out|Network is unreachable|"
                     r"Failed to connect|urlopen error|unable to access|Temporary failure in name resolution|SSL", re.I)


def _vt(v):
    n = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(n + [0] * (3 - len(n)))


def choose_version(min_version):
    ok = [v for v in KNOWN_VERSIONS if not min_version or _vt(v) >= _vt(min_version)]
    return max(ok, key=_vt) if ok else None


def split_progress_lines(chunks):
    """git、下载进度用回车 \\r 在同一行刷新；按 \\r 和 \\n 都切开，才能实时看到百分比。"""
    buf = ""
    for chunk in chunks:
        buf += chunk
        parts = re.split(r"[\r\n]", buf)
        buf = parts.pop()
        for p in parts:
            if p.strip():
                yield p.strip()
    if buf.strip():
        yield buf.strip()


def _runner(cmd, cwd, env):
    proc = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    chunks = iter(lambda: proc.stdout.read1(4096).decode("utf-8", "replace"), "")
    yield from split_progress_lines(chunks)
    return proc.wait()


def _default_try_build(idf, example, tools, on_progress):
    from fsw.platforms.espidf import EspIdf
    return EspIdf(idf).build(example, target="esp32s3", on_progress=on_progress)


def windows_command(version, dest):
    """Windows 用乐鑫官方安装管理器 EIM 的命令行版（实测在 Windows 那台电脑上做）。"""
    return ["eim.exe", "install", "-i", version, "-p", str(dest), "--non-interactive"]


class MacInstaller:
    def __init__(self, dest, version, tools_path=None, runner=_runner, which=shutil.which,
                 try_build=_default_try_build, work_dir=None, target="esp32s3"):
        self.dest, self.version = Path(dest), version
        self.tools = Path(tools_path) if tools_path else None
        self.runner, self.which, self.try_build, self.target = runner, which, try_build, target
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "fsw-try-build"
        self.steps = [s.format(v=version) for s in STEPS]

    def _env(self):
        env = dict(os.environ)
        if self.tools:
            env["IDF_TOOLS_PATH"] = str(self.tools)
        return env

    def _run(self, cmd, cwd, on_line):
        gen, last = self.runner(cmd, cwd, self._env()), []
        while True:
            try:
                line = next(gen)
            except StopIteration as stop:
                return stop.value or 0, last
            last = (last + [line])[-30:]
            on_line(line)

    def _fail(self, step, last, reason=None):
        uniq = [l for i, l in enumerate(last) if i == 0 or l != last[i - 1]]  # 连着重复的进度行只留一行
        log = "\n".join(uniq[-6:])
        if not reason:
            reason = "连不上下载服务器（GitHub 或乐鑫）：看看网络，或者换个网络再点重试" if OFFLINE.search(log) else \
                "磁盘空间不够了" if "No space left" in log else \
                "下载中途被打断了（网络断开或安装程序被关掉）：点重试，已下好的留着，从断的地方接着装" \
                if re.search(r"Terminated|Killed|Interrupted|Broken pipe", log) else "看下面最新一行日志"
        return {"ok": False, "step": step, "log": log,
                "message": f"第 {step + 1} 步「{self.steps[step]}」没装成：{reason}"}

    def run(self, on_event):
        state = {"step": 0, "pct": 0, "log": ""}

        def emit(step=None, pct=None, log=None, st="running"):
            if step is not None:
                state["step"] = step
            if pct is not None:
                state["pct"] = max(0, min(100, int(pct)))
            if log is not None:
                state["log"] = log
            on_event({"state": st, **state})

        # 第 1 步：检查前提
        emit(0, 0, "检查 git、Python、苹果开发者工具")
        for tool in ("git", "python3"):
            if not self.which(tool):
                return self._fail(0, [], f"没找到 {tool}。先装苹果开发者工具：在终端运行 xcode-select --install，弹窗里点\"安装\"")
        code, last = self._run(["xcode-select", "-p"], None, lambda l: emit(log=l))
        if code:
            return self._fail(0, last, "缺苹果开发者工具：在终端运行 xcode-select --install，弹窗里点\"安装\"")

        # 第 2 步：下载乐鑫编译工具（已下好的不重下；下了一半的从头下这一块）
        emit(1, 0, f"从乐鑫官方 GitHub 下载 {self.version}")
        if not ((self.dest / ".git").exists() and (self.dest / "tools/idf.py").exists()):
            if self.dest.exists():
                shutil.rmtree(self.dest)
            self.dest.parent.mkdir(parents=True, exist_ok=True)

            def clone_line(l):
                m = re.search(r"(Receiving|Resolving) objects:\s+(\d+)%", l)
                emit(pct=int(m.group(2)) if m and m.group(1) == "Receiving" else None, log=l)
            code, last = self._run(["git", "clone", "--progress", "--depth", "1", "--branch", self.version,
                                    "--shallow-submodules", IDF_REPO, self.dest], None, clone_line)
            if code:
                return self._fail(1, last)
        subs = len(re.findall(r"^\s*path\s*=", (self.dest / ".gitmodules").read_text(encoding="utf-8"), re.M)) \
            if (self.dest / ".gitmodules").exists() else 0
        done = [0]

        def sub_line(l):
            if l.startswith("Submodule path"):
                done[0] += 1
                emit(pct=100 * done[0] / max(1, subs), log=l)
            else:
                emit(log=l)
        code, last = self._run(["git", "-C", self.dest, "submodule", "update", "--init", "--recursive",
                                "--depth", "1", "--progress"], None, sub_line)
        if code:
            return self._fail(1, last)
        if self.tools:  # 编译工具自己的工具放在哪，记下来，以后编译时用同一个地方
            (self.dest / ".fsw_tools_path").write_text(str(self.tools), encoding="utf-8")

        # 第 3、4 步：官方脚本 install.sh（先装编译器等工具，再装 Python 依赖）
        emit(2, 0, "运行官方安装脚本 install.sh")
        tools = {"total": 0, "done": 0, "cur": 0}

        def install_line(l):
            if state["step"] == 2:
                m = re.match(r"Installing tools: (.+)", l)
                if m:
                    tools["total"] = len([t for t in m.group(1).split(",") if t.strip()])
                elif re.match(r"(Installing|Skipping) \S+@", l):
                    if l.startswith("Skipping"):
                        tools["done"] += 1
                    else:
                        tools["done"] += 1 if tools["cur"] else 0
                        tools["name"] = l.split()[1]
                    tools["cur"] = 0
                elif re.fullmatch(r"\d+%", l):
                    tools["cur"] = int(l[:-1])
                    l = f"{tools.get('name', '工具')} 下载 {l}"
                total = max(1, tools["total"])
                emit(pct=100 * (tools["done"] + tools["cur"] / 100) / total, log=l)
                if "Installing Python environment" in l:
                    emit(3, 0, l)
            else:
                emit(pct=min(95, state["pct"] + 5) if "Installing" in l or "Collecting" in l else None, log=l)
        code, last = self._run(["bash", self.dest / "install.sh", self.target], self.dest, install_line)
        if code:
            return self._fail(state["step"], last)
        emit(3, 100, "Python 依赖装好了")

        # 第 5 步：试编译官方小例子（编在码表自己的目录，不写进编译工具里）
        emit(4, 0, "编译官方小例子 hello_world，确认真的能用")
        example = self.work_dir / f"hello_world-{self.version}"
        if example.exists():
            shutil.rmtree(example)
        shutil.copytree(self.dest / "examples/get-started/hello_world", example)
        r = self.try_build(self.dest, example, self.tools, lambda p, m: emit(pct=p, log=m))
        if not r.get("ok"):
            return self._fail(4, (r.get("error") or "").splitlines() or ["编译没通过"], "装好了但试编译没通过：看下面的报错")
        emit(4, 100, "Project build complete.", st="done")
        return {"ok": True, "path": str(self.dest), "version": self.version}
