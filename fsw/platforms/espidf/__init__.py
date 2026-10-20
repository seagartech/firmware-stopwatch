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


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# 第一次编译要从乐鑫组件库下载组件；连不上时乐鑫的原话
NET_FAIL = re.compile(r"Cannot establish a connection to the component registry|Failed to resolve component|"
                      r"(ConnectionError|ConnectTimeout|ReadTimeout|NameResolutionError|Max retries exceeded)", re.I)
NET_MESSAGE = "组件没下载下来，不是源码的问题：第一次编译要从乐鑫组件库下载组件，这台电脑没连上（网络或代理）。网络好了点「重试」。"


class ToolEnvError(RuntimeError):
    """乐鑫编译工具的环境没加载起来（export 脚本没跑通）：是电脑上编译工具的问题，不是用户源码的问题。"""

    message = "乐鑫编译工具没加载起来，不是源码的问题。到设置页重新检查或安装编译工具，然后点「重试」。"

    def __init__(self, detail=""):
        super().__init__(self.message)
        self.detail = detail


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
    cands = [env.get("IDF_PATH")] + (list(extra) if isinstance(extra, (list, tuple)) else [extra])
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
    def export_env(self):
        """跑 export 脚本时用的环境：码表帮装的编译工具，它自己的工具放在另一个地方（.fsw_tools_path 里记着）。

        码表自己跑在 uv 建的 Python 里，这套 Python 不能传给乐鑫：export 脚本会拿 PATH 上第一个 python 去找
        "idfX.Y_pyA.B_env"，码表的 Python 版本和乐鑫装环境时用的不一样就找不到。去掉以后和用户在终端里手动加载一样。
        """
        env = dict(os.environ)
        for k in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__"):
            env.pop(k, None)
        own = {os.path.normcase(str(Path(d) / ("Scripts" if os.name == "nt" else "bin")))
               for d in (os.environ.get("VIRTUAL_ENV"), sys.prefix if sys.prefix != sys.base_prefix else None) if d}
        if "PATH" in env:
            env["PATH"] = os.pathsep.join(d for d in env["PATH"].split(os.pathsep)
                                          if os.path.normcase(d.rstrip("/\\")) not in own)
        rec = self.idf_path / ".fsw_tools_path"
        if rec.exists():
            env["IDF_TOOLS_PATH"] = rec.read_text(encoding="utf-8").strip()
        # 乐鑫按 PATH 上第一个合用的 python 的版本去找"idfX.Y_pyA.B_env"。这台电脑上已经装好的那套环境放到 PATH 最前面，
        # 就一定用它：电脑上有好几个 Python 时（比如 Mac 自带 3.9、另装了 3.10，环境却是用 3.12 装的）也不会挑错
        py_env = self.installed_python_env(env)
        if py_env:
            env["PATH"] = os.pathsep.join([str(py_env)] + ([env["PATH"]] if env.get("PATH") else []))
        return env

    def installed_python_env(self, env):
        """乐鑫为这个版本装好的 Python 环境的 bin（Windows 是 Scripts）文件夹；装了几套就挑 Python 版本最高的。没有返回 None。"""
        v = _read_version(self.idf_path)
        if not v:
            return None
        tools = Path(env.get("IDF_TOOLS_PATH") or Path.home() / ".espressif")
        sub, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python3")
        major_minor = ".".join(v.split(".")[:2])
        found = [d / sub for d in (tools / "python_env").glob(f"idf{major_minor}_py*_env") if (d / sub / exe).exists()]
        return max(found, key=lambda d: version_tuple(d.parent.name.split("_py")[-1])) if found else None

    def env(self):
        if self._env is None:
            if os.name == "nt":
                cmd = ["powershell", "-NoProfile", "-Command",
                       f'. "{self.idf_path / "export.ps1"}" *> $null; Get-ChildItem env: | ForEach-Object {{ "$($_.Name)=$($_.Value)" }}']
                r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=self.export_env())
                pairs = [l.split("=", 1) for l in r.stdout.splitlines() if "=" in l]
                said = r.stderr
            else:
                # export 脚本自己说的话转到 stderr 留着：没跑通时给人看"详情"
                cmd = ["bash", "-c", f'. "{self.idf_path / "export.sh"}" 1>&2 && env -0']
                r = subprocess.run(cmd, capture_output=True, env=self.export_env())
                pairs = [l.split("=", 1) for l in r.stdout.decode("utf-8", "replace").split("\0") if "=" in l]
                said = r.stderr.decode("utf-8", "replace")
            env = dict(pairs)
            if "IDF_PYTHON_ENV_PATH" not in env:  # 没跑通不记下来，装好或修好后再点一次就能重新加载
                tail = [l for l in ANSI.sub("", said).splitlines() if l.strip()][-8:]
                raise ToolEnvError("\n".join(tail))
            self._env = env
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

        stage = {"compiling": False, "total": 0}

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
                # 引导程序是单独编的一小组，自己也数"第几个 / 共几个"（比如 [1/1]）。只按总数最多的那一组算，否则一下跳到 99%
                if p[1] < stage["total"]:
                    return
                if p[1] > stage["total"]:
                    if stage["total"]:
                        best[0] = 5  # 先看到的是小的那组：换成大的那组重新算
                    stage["total"] = p[1]
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
        # 先找编译器的报错行（文件:行: error:），再找 CMake 的报错段（原话常常分好几行），找不到再退而求其次
        cm = next((i for i, l in enumerate(tail) if l.startswith("CMake Error")), None)
        errs = [l for l in tail if re.search(r":\d+(:\d+)?: (fatal )?error:", l)] \
            or (tail[cm:cm + 8] if cm is not None else []) \
            or [l for l in tail if re.search(r"\berror\b", l, re.I) and not re.search(r"\s-W|xtensa-.*-g(cc|\+\+)\s", l)] \
            or tail[-8:]
        out = {"ok": False, "seconds": secs, "error": "\n".join(errs[:8])}
        if NET_FAIL.search("\n".join(tail)):  # 下载组件连不上：是网络的问题，不是源码编不过
            out.update(cause="network", message=NET_MESSAGE)
        return out

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

    # ---- 任务 14：两份固件（原版、计时版）各存一份，随时烧哪个 ----
    @staticmethod
    def save_firmware(src, dest):
        """编好后把烧录要用的几个文件存一份（按 build/flasher_args.json）。没编好返回 None。"""
        build = Path(src) / "build"
        try:
            args = json.loads((build / "flasher_args.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        dest = Path(dest)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for rel in args["flash_files"].values():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(build / rel, dest / rel)
        (dest / "flasher_args.json").write_text(json.dumps(args, ensure_ascii=False, indent=1), encoding="utf-8")
        app = (args.get("app") or {}).get("file") or list(args["flash_files"].values())[-1]
        return {"files": len(args["flash_files"]), "app_bytes": (dest / app).stat().st_size}

    def flash_saved(self, fw, port, on_progress=lambda pct, msg: None):
        """烧存下的那份固件（乐鑫官方 esptool，和 idf.py flash 用同样的参数）。"""
        fw = Path(fw)
        args = json.loads((fw / "flasher_args.json").read_text(encoding="utf-8"))
        extra = args.get("extra_esptool_args") or {}
        cmd = [self._idf_py()[0], "-m", "esptool", "--chip", extra.get("chip", "auto"), "-p", port, "-b", "460800",
               "--before", extra.get("before", "default-reset"), "--after", extra.get("after", "hard-reset"),
               "write-flash", *args.get("write_flash_args", [])]
        for off, rel in args["flash_files"].items():
            cmd += [off, rel]

        def line_cb(line):
            pct = flash_progress(line)
            if pct is not None:
                on_progress(pct, line.strip()[:200])

        t = time.time()
        code, tail = self._run(cmd, fw, line_cb)
        if code == 0:
            on_progress(100, "烧录完成")
            return {"ok": True, "port": port, "seconds": round(time.time() - t, 1), "error": None}
        errs = [l for l in tail if "error" in l.lower() or "fatal" in l.lower()] or tail[-6:]
        return {"ok": False, "port": port, "error": "\n".join(errs[-6:])}

    @staticmethod
    def read_for(ser, seconds, on_line=lambda line: None, stop=None):
        """读调试口一段时间，一行行交出来（试一次用）；stop() 为真就提前停（比如计时点都收到了）。"""
        end, buf, out = time.time() + seconds, b"", []
        while time.time() < end and not (stop and stop()):
            chunk = ser.read(ser.in_waiting or 1)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.rstrip(b"\r").decode("utf-8", "replace")
                out.append(text)
                on_line(text)
        return out

    _OPEN = {}   # 码表自己现在开着的调试口：{串口对象: 谁开的}（10-20 真测：码表进程自己占着口没放，试测一直"打不开"，要重启才好）

    @staticmethod
    def open_port(port, baud=115200):
        import threading
        import serial
        ser = serial.Serial(port, baud, timeout=0.2)
        EspIdf._OPEN[ser] = threading.current_thread().name
        return ser

    @staticmethod
    def close_port(ser):
        EspIdf._OPEN.pop(ser, None)
        try:
            ser.close()
        except Exception:
            pass

    @staticmethod
    def release_ports():
        """把码表自己还开着的调试口全部关掉（烧录、试一次、开测之前用）；返回关了几个。
        读口的线程再读会报错，各自的处理会让它们退出或重连。"""
        n = 0
        for ser in list(EspIdf._OPEN):
            EspIdf.close_port(ser)
            n += 1
        return n
