"""乐鑫 ESP-IDF：找编译工具、编译（看进度、只重编改过的）、烧录（没插设备就等）、读调试口。

Mac 和 Windows 的差别（编译工具装在哪、怎么加载环境、串口叫什么）都在这里处理。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 常见 USB 转串口芯片：乐鑫自带 USB、CH340、CP210x、FTDI、PL2303
USB_SERIAL_VIDS = {0x303A, 0x1A86, 0x10C4, 0x0403, 0x067B}
NINJA = re.compile(r"^\[(\d+)/(\d+)\]")
DEP_LINE = re.compile(r"^\[\d+/\d+\]\s+\S+\s+\([^)]*\)$")
FLASH_PCT = [re.compile(r"Writing at 0x[0-9a-fA-F]+.*?\(\s*(\d+)\s*%\)"),
             re.compile(r"Writing at 0x[0-9a-fA-F]+.*?\s(\d+)(?:\.\d+)?%")]


# 编译前几个阶段：(输出里认的话, 进度, 给人看的说法)
CONFIG_STAGES = [
    (r"Processing \d+ dependencies", 1, "第一次编译要先下载用到的组件，一般 1 分钟左右（看网速）"),
    (r"-- Configuring done", 3, "项目配置读完了，正在生成编译清单"),
    (r"-- Generating done|Build files have been written", 5, "开始编译"),
]


def version_tuple(v):
    parts = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def _read_version(idf_path):
    text = (Path(idf_path) / "tools/cmake/version.cmake")
    try:
        t = text.read_text(encoding="utf-8")
    except OSError:
        return None
    nums = [re.search(rf"IDF_VERSION_{k}\s+(\d+)", t) for k in ("MAJOR", "MINOR", "PATCH")]
    if not all(nums):
        return None
    return ".".join(m.group(1) for m in nums)


def find_idf(home=None, env=None, extra=None):
    """找电脑上装的乐鑫编译工具：环境变量 IDF_PATH、设置里写的、常见位置、乐鑫安装器的记录。只按位置找文件。"""
    home = Path(home) if home else Path.home()
    env = os.environ if env is None else env
    cands = [env.get("IDF_PATH"), extra]
    cands += sorted(str(p) for p in (home / "esp").glob("esp-idf*")) if (home / "esp").is_dir() else []
    cands += [str(home / "esp" / "esp-idf")]
    rec = home / ".espressif" / "idf-env.json"
    try:
        for item in json.loads(rec.read_text(encoding="utf-8")).get("idfInstalled", {}).values():
            cands.append(item.get("path"))
    except (OSError, ValueError, AttributeError):
        pass
    if os.name == "nt":
        cands += [str(p) for p in Path("C:/Espressif/frameworks").glob("esp-idf*")] + [str(p) for p in Path("C:/esp").glob("*")]
    seen, out = set(), []
    for c in cands:
        if not c:
            continue
        p = Path(c).expanduser().resolve()
        if p in seen or not (p / "tools/idf.py").exists():
            continue
        seen.add(p)
        v = _read_version(p)
        if v:
            out.append({"path": str(p), "version": v})
    return out


def pick_idf(installs, min_version):
    ok = [i for i in installs if not min_version or version_tuple(i["version"]) >= version_tuple(min_version)]
    return max(ok, key=lambda i: version_tuple(i["version"])) if ok else None


def ninja_progress(line):
    m = NINJA.match(line.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def flash_progress(line):
    for pat in FLASH_PCT:
        m = pat.search(line)
        if m:
            return int(m.group(1))
    return None


def _list_ports():
    from serial.tools import list_ports
    return [{"device": p.device, "vid": p.vid, "description": p.description} for p in list_ports.comports()]


def find_ports(list_ports=_list_ports):
    """只留 USB 转串口的设备；Mac 上同一个设备有 tty. 和 cu. 两个名字，用 cu.。"""
    ports = [p for p in list_ports() if p.get("vid") in USB_SERIAL_VIDS]
    names = [p["device"] for p in ports]
    return [n for n in names if not (n.startswith("/dev/tty.") and n.replace("/dev/tty.", "/dev/cu.", 1) in names)]


def read_lines(ser):
    """从调试口一行行读设备输出（原样，不改日志）。"""
    buf = b""
    while True:
        chunk = ser.read(ser.in_waiting or 1)
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.rstrip(b"\r").decode("utf-8", "replace")


def _subprocess_runner(cmd, cwd, env):
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1)
    for line in proc.stdout:
        yield line.rstrip("\n")
    return proc.wait()


class EspIdf:
    name = "ESP-IDF"

    def __init__(self, idf_path, runner=_subprocess_runner, env=None, list_ports=_list_ports, sleep=time.sleep):
        self.idf_path = Path(idf_path)
        self.runner, self._env, self.list_ports, self.sleep = runner, env, list_ports, sleep

    # ---- 加载乐鑫的环境（相当于在终端里先 source export.sh）----
    def env(self):
        if self._env is None:
            if os.name == "nt":
                cmd = ["powershell", "-NoProfile", "-Command",
                       f'. "{self.idf_path / "export.ps1"}" *> $null; Get-ChildItem env: | ForEach-Object {{ "$($_.Name)=$($_.Value)" }}']
                out = subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()
                pairs = [l.split("=", 1) for l in out if "=" in l]
            else:
                cmd = ["bash", "-c", f'. "{self.idf_path / "export.sh"}" >/dev/null 2>&1 && env -0']
                out = subprocess.run(cmd, capture_output=True).stdout.decode("utf-8", "replace")
                pairs = [l.split("=", 1) for l in out.split("\0") if "=" in l]
            self._env = dict(pairs)
            if "IDF_PYTHON_ENV_PATH" not in self._env:
                raise RuntimeError("加载乐鑫编译工具的环境失败：export 脚本没跑通。可以在设置页重新安装编译工具。")
        return self._env

    def _idf_py(self):
        env = self.env()
        py_dir = Path(env.get("IDF_PYTHON_ENV_PATH", "")) / ("Scripts" if os.name == "nt" else "bin")
        py = py_dir / ("python.exe" if os.name == "nt" else "python")
        return [str(py) if py.exists() else sys.executable, str(self.idf_path / "tools/idf.py")]

    def _run(self, cmd, cwd, on_line):
        gen, tail = self.runner(cmd, str(cwd), self.env()), []
        while True:
            try:
                line = next(gen)
            except StopIteration as stop:
                return stop.value or 0, tail
            tail = (tail + [line])[-40:]
            on_line(line)

    # ---- 板子：一份源码支持多块板子时，把选的那块板子的配置写成一个额外的默认配置 ----
    @staticmethod
    def _board_fragment(src, board):
        cfg = Path(src) / "main" / "boards" / board / "config.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        btype = data.get("type") or Path(board).name
        lines = [f"CONFIG_BOARD_TYPE_{re.sub(r'[^A-Za-z0-9]+', '_', btype).upper()}=y"]
        builds = data.get("builds") or [{}]
        lines += builds[0].get("sdkconfig_append", [])
        frag = Path(src) / "sdkconfig.fsw-board"
        frag.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return frag.name

    def build(self, src, target, board=None, on_progress=lambda pct, msg: None):
        """编译。build 文件夹留着，第二次只重编改过的文件。进度按编译器的"第几个 / 共几个"算。"""
        src = Path(src)
        defaults = [n for n in ("sdkconfig.defaults", f"sdkconfig.defaults.{target}") if (src / n).exists()]
        if board:
            defaults.append(self._board_fragment(src, board))
        cache = src / "build" / "CMakeCache.txt"
        if cache.exists():
            m = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$", cache.read_text(encoding="utf-8", errors="replace"), re.M)
            if m and Path(m.group(1).strip()).resolve() != src.resolve():
                shutil.rmtree(src / "build")  # build 文件夹是别的位置留下的（源码挪过），清掉从头编
        old = src / "sdkconfig"
        if old.exists() and f'CONFIG_IDF_TARGET="{target}"' not in old.read_text(encoding="utf-8", errors="replace"):
            old.unlink()  # 换了芯片，旧配置作废
        cmd = self._idf_py() + [f"-DIDF_TARGET={target}"]
        if defaults:
            cmd.append("-DSDKCONFIG_DEFAULTS=" + ";".join(defaults))
        cmd.append("build")
        best = [0]
        on_progress(0, "准备编译：读项目配置、下载要用的组件")

        stage = {"compiling": False}

        def line_cb(line):
            s = line.strip()
            # 编译器开始数"第几个 / 共几个"之前，按阶段给说法（第一次从头编这段要一分多钟）
            for pat, pct, msg in CONFIG_STAGES:
                if not stage["compiling"] and re.search(pat, s) and pct > best[0]:
                    best[0] = pct
                    on_progress(pct, msg)
                    if pct == 5:
                        stage["compiling"] = True
                    return
            p = ninja_progress(s)
            if p and not DEP_LINE.match(s):  # "[1/41] 组件名 (版本)" 是下载组件清单，不是编译进度
                stage["compiling"] = True
                pct = min(99, 5 + int(94 * p[0] / max(1, p[1])))
                if pct > best[0]:
                    best[0] = pct
                    on_progress(pct, s[:200])

        t = time.time()
        code, tail = self._run(cmd, src, line_cb)
        secs = round(time.time() - t, 1)
        if code == 0:
            on_progress(100, "编译完成")
            return {"ok": True, "seconds": secs, "error": None}
        # 先找编译器的报错行（文件:行: error:），找不到再退而求其次
        errs = [l for l in tail if re.search(r":\d+(:\d+)?: (fatal )?error:", l)] \
            or [l for l in tail if re.search(r"\berror\b", l, re.I) and not re.search(r"\s-W|xtensa-.*-g(cc|\+\+)\s", l)] \
            or tail[-8:]
        return {"ok": False, "seconds": secs, "error": "\n".join(errs[:8])}

    def wait_for_port(self, on_waiting=lambda: None, cancelled=lambda: False):
        """烧录前才查设备：没插就停下等，插上自动继续。"""
        while not cancelled():
            ports = find_ports(self.list_ports)
            if ports:
                return ports[0]
            on_waiting()
            self.sleep(1)
        return None

    def flash(self, src, port=None, on_waiting=lambda: None, on_progress=lambda pct, msg: None, cancelled=lambda: False):
        port = port or self.wait_for_port(on_waiting, cancelled)
        if not port:
            return {"ok": False, "port": None, "error": "取消了：没等到设备"}
        state = {"last": 0}

        def line_cb(line):
            pct = flash_progress(line)
            if pct is not None:
                state["last"] = pct
                on_progress(pct, line.strip()[:200])

        t = time.time()
        code, tail = self._run(self._idf_py() + ["-p", port, "flash"], src, line_cb)
        if code == 0:
            on_progress(100, "烧录完成")
            return {"ok": True, "port": port, "seconds": round(time.time() - t, 1), "error": None}
        errs = [l for l in tail if "error" in l.lower() or "fatal" in l.lower()] or tail[-6:]
        return {"ok": False, "port": port, "error": "\n".join(errs[-6:])}

    @staticmethod
    def open_port(port, baud=115200):
        import serial
        return serial.Serial(port, baud, timeout=0.2)
