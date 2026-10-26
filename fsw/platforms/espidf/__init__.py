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


# 乐鑫烧录工具连上芯片时报的型号（新版 "Connected to ESP32-S3 on …"，旧版 "Detecting chip type... ESP32-S3" / "Chip is ESP32-S3 (…)"）
CHIP_SEEN = [re.compile(r"Connected to (ESP32[-\w]*) on"), re.compile(r"Detecting chip type\.*\s*(ESP32[-\w]*)"),
             re.compile(r"Chip is (ESP32[-\w]*)")]
# 烧录时芯片和板子对不上，乐鑫烧录工具的原话："This chip is ESP32-S3, not ESP32-C3. Wrong chip argument?"
WRONG_CHIP = re.compile(r"This chip is (ESP32[-\w]*),? not (ESP32[-\w]*)")


def chip_name(s):
    """芯片名统一写法：ESP32-S3、esp32s3、ESP32S3 都当成 ESP32-S3。"""
    t = re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    return (t[:5] + "-" + t[5:] if t.startswith("ESP32") and len(t) > 5 else t) or None


def chip_in(lines):
    """乐鑫烧录工具的输出里，连上的是哪种芯片；没连上返回 None。"""
    for line in lines:
        for rx in CHIP_SEEN:
            m = rx.search(line)
            if m:
                return chip_name(m.group(1))
    return None


def wrong_chip(lines):
    """烧录报芯片对不上：返回 (设备上的芯片, 固件要的芯片)；不是这个错返回 None。"""
    for line in lines:
        m = WRONG_CHIP.search(line)
        if m:
            return chip_name(m.group(1)), chip_name(m.group(2))
    return None


# 烧录失败分阶段（10-26 8783 真测：esptool 连都没连上，页面却说"写到一半断了、设备上是写了一半的固件"）：
# - busy：调试口打不开（被别的程序占着、没权限）——什么都没写；
# - connect：口打开了，但芯片没回话、没进下载模式——什么都没写，设备里原来的东西没动；
# - write：已经开始擦写（Writing at / Erasing / Flash will be erased）之后断了——设备上可能是写了一半的固件；
# - chip：芯片和固件对不上，乐鑫工具拒绝写——什么都没写；
# - other：别的错，没看到开始写——按没写进去说，原话放详情。
CONNECT_FAIL = re.compile(r"Failed to connect|No serial data received|Wrong boot mode|Timed out waiting for packet header|"
                          r"Invalid head of packet|Possible serial noise or corruption|Download mode .*not detected", re.I)
OPEN_FAIL = re.compile(r"could not open port|Resource busy|Errno 16|Permission denied|could not exclusively lock|"
                       r"No such file or directory: '/dev/|could not open port", re.I)
WRITE_SEEN = re.compile(r"Writing at 0x|Erasing flash|Flash will be erased|Erasing region|Wrote \d+ bytes", re.I)


def flash_stage(lines):
    """乐鑫烧录工具的输出 → (失败在哪个阶段, 是否已经开始写)。阶段见上面。"""
    written = any(WRITE_SEEN.search(l) for l in lines)
    if wrong_chip(lines):
        return "chip", written
    if written:
        return "write", True
    if any(OPEN_FAIL.search(l) for l in lines):
        return "busy", False
    if any(CONNECT_FAIL.search(l) for l in lines):
        return "connect", False
    return "other", False


def _flash_failed(port, tail):
    errs = [l for l in tail if "error" in l.lower() or "fatal" in l.lower()] or tail[-6:]
    stage, written = flash_stage(tail)
    out = {"ok": False, "port": port, "error": "\n".join(errs[-6:]), "stage": stage, "written": written}
    bad = wrong_chip(tail)
    if bad:  # 选错了板子（芯片不同）：乐鑫工具拒绝写入，设备里原来的东西没动
        out["wrong_chip"] = {"device": bad[0], "firmware": bad[1]}
    return out


def _lsof_runner(port):
    r = subprocess.run(["lsof", "-F", "pc", "--", port], capture_output=True, text=True, errors="replace", timeout=10)
    return r.stdout


def port_holders(port, own_pid=None, runner=_lsof_runner):
    """哪些程序开着这个调试口（烧录前查；Windows 上没有 lsof，返回空）。不算码表自己。
    返回 [{"pid": 123, "name": "screen"}]；查不了（没有 lsof、超时）也返回空，不挡烧录。"""
    if os.name == "nt":
        return []
    own = os.getpid() if own_pid is None else own_pid
    try:
        text = runner(port) or ""
    except (OSError, subprocess.SubprocessError):
        return []
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            cur = {"pid": int(line[1:]), "name": ""}
            if cur["pid"] != own and all(x["pid"] != cur["pid"] for x in out):
                out.append(cur)
        elif line.startswith("c") and cur is not None:
            cur["name"] = line[1:]
    return out


# 手动进下载模式：通用说法；知道是哪块板子时再补一句这块板子的按法（按板子说明书写的）
DOWNLOAD_MODE_GENERIC = "按板子说明书让它进下载模式（多数板子是按住 BOOT 键，再按一下复位键 RST，先松 RST 再松 BOOT）"
DOWNLOAD_MODE_TIPS = [(re.compile(r"m5stack.*core-?s3", re.I), "M5Stack CoreS3：长按复位键约 3 秒，看到内部绿灯亮就是进了下载模式")]


def download_mode_tip(board=None):
    tip = next((t for rx, t in DOWNLOAD_MODE_TIPS if board and rx.search(str(board))), None)
    return f"{DOWNLOAD_MODE_GENERIC}；{tip}" if tip else DOWNLOAD_MODE_GENERIC


def _list_ports():
    from serial.tools import list_ports
    return [{"device": p.device, "vid": p.vid, "description": p.description, "serial": p.serial_number}
            for p in list_ports.comports()]


def port_key(port, list_ports=_list_ports):
    """认出是哪台设备：USB 序列号（乐鑫自带 USB 口的就是芯片的 MAC 地址），没有序列号就用口的名字。"""
    try:
        p = next((x for x in list_ports() if x.get("device") == port), None)
    except Exception:
        p = None
    return f"{p.get('vid')}:{p['serial']}" if p and p.get("serial") else port


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


def pick_errors(tail):
    """编译输出里挑报错：先找编译器的报错行（文件:行: error:）连同"在哪里展开的宏"那几行（报错指到头文件里时靠它找到真正的那一句），
    再找 CMake 的报错段（原话常常分好几行），找不到再退而求其次。最多 8 行。"""
    cm = next((i for i, l in enumerate(tail) if l.startswith("CMake Error")), None)
    errs = [l for l in tail if re.search(r":\d+(:\d+)?: (fatal )?error:|:\d+(:\d+)?: note: (in expansion of|expanded from) macro", l)] \
        or (tail[cm:cm + 8] if cm is not None else []) \
        or [l for l in tail if re.search(r"\berror\b", l, re.I) and not re.search(r"\s-W|xtensa-.*-g(cc|\+\+)\s", l)] \
        or tail[-8:]
    return errs[:8]


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
        out = {"ok": False, "seconds": secs, "error": "\n".join(pick_errors(tail))}
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
        return _flash_failed(port, tail)

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

    def read_chip(self, port):
        """读设备上是哪种芯片（乐鑫烧录工具连一下就断开）。设备会重启一下。
        返回 {"chip": "ESP32-S3"}；连不上（口卡住、设备不能自动进下载模式）返回 {"chip": None, "error": 原话}。"""
        cmd = [self._idf_py()[0], "-m", "esptool", "--port", port, "chip-id"]
        code, tail = self._run(cmd, Path.home(), lambda line: None)
        chip = chip_in(tail)
        if chip:
            return {"chip": chip}
        errs = [l for l in tail if "error" in l.lower() or "fatal" in l.lower()] or tail[-4:]
        return {"chip": None, "error": "\n".join(errs[-4:])}

    CONNECT_RETRIES = 2   # 没连上（什么都没写）时再试几次
    RETRY_WAIT = 2        # 每次重试前等几秒（让设备从上一次复位里缓过来）

    def flash_saved(self, fw, port, on_progress=lambda pct, msg: None, log=None, on_attempt=lambda n, before: None):
        """烧存下的那份固件（乐鑫官方 esptool，和 idf.py flash 用同样的参数）。

        没连上芯片（连接阶段失败，还没开始写）时自动再试，最多 CONNECT_RETRIES 次：先用同样的复位方式再来一次，
        再换 USB 复位（乐鑫自带 USB 口的芯片用它更稳）。已经开始写之后断了不自动重试，交给人看线。
        log：每次尝试的乐鑫工具完整输出都追加写进这个文件（出错后能查原因，不只留最后一行）。"""
        fw = Path(fw)
        args = json.loads((fw / "flasher_args.json").read_text(encoding="utf-8"))
        extra = args.get("extra_esptool_args") or {}
        base = extra.get("before", "default-reset")
        usb = "usb_reset" if "_" in base else "usb-reset"   # 老版本 esptool 用下划线写法
        befores = [base] + [base, usb if base != usb else ("default_reset" if "_" in base else "default-reset")][:self.CONNECT_RETRIES]
        files = []
        for off, rel in args["flash_files"].items():
            files += [off, rel]

        def line_cb(line):
            pct = flash_progress(line)
            if pct is not None:
                on_progress(pct, line.strip()[:200])

        logf = None
        if log:
            try:
                Path(log).parent.mkdir(parents=True, exist_ok=True)
                logf = open(log, "a", encoding="utf-8")
            except OSError:
                logf = None
        t, attempts, r = time.time(), [], None
        try:
            for n, before in enumerate(befores, 1):
                if n > 1:
                    self.sleep(self.RETRY_WAIT)
                on_attempt(n, before)
                cmd = [self._idf_py()[0], "-m", "esptool", "--chip", extra.get("chip", "auto"), "-p", port, "-b", "460800",
                       "--before", before, "--after", extra.get("after", "hard-reset"),
                       "write-flash", *args.get("write_flash_args", []), *files]
                if logf:
                    logf.write(f"\n===== 第 {n} 次烧录 {time.strftime('%Y-%m-%d %H:%M:%S')} 口 {port} 复位方式 {before}\n$ {' '.join(cmd)}\n")
                    logf.flush()

                def cb(line):
                    if logf:
                        logf.write(line + "\n")
                    line_cb(line)
                t1 = time.time()
                code, tail = self._run(cmd, fw, cb)
                if code == 0:
                    attempts.append({"n": n, "before": before, "ok": True, "stage": None, "seconds": round(time.time() - t1, 1)})
                    if logf:
                        logf.write(f"===== 第 {n} 次：烧好了\n")
                    on_progress(100, "烧录完成")
                    return {"ok": True, "port": port, "seconds": round(time.time() - t, 1), "error": None,
                            "attempts": attempts, "log": str(log) if log else None}
                r = _flash_failed(port, tail)
                attempts.append({"n": n, "before": before, "ok": False, "stage": r["stage"], "seconds": round(time.time() - t1, 1)})
                if logf:
                    logf.write(f"===== 第 {n} 次失败：阶段 {r['stage']}，{'已开始写' if r['written'] else '还没开始写'}（退出码 {code}）\n")
                    logf.flush()
                if r["stage"] != "connect":  # 只有"没连上、什么都没写"才自动再试
                    break
        finally:
            if logf:
                logf.close()
        r.update(attempts=attempts, seconds=round(time.time() - t, 1), log=str(log) if log else None)
        return r

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
