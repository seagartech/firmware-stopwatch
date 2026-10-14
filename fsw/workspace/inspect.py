"""先认固件：只读项目文件（不用大模型），认出是不是单片机固件、什么平台、芯片、编译工具版本。

出错时返回 ok=False，kind 说明是哪种情况，message 是给人看的一段话（可含 <b>、<br>）。
"""

import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

SKIP_DIRS = {".git", "build", "managed_components", "node_modules", ".venv", "__pycache__"}
ARCHIVES = (".zip", ".tar.gz", ".tgz", ".tar")
PROJECT_CMAKE = re.compile(r"include\s*\(\s*\$ENV\{IDF_PATH\}/tools/cmake/project\.cmake\s*\)")


def chip_name(target):
    """esp32s3 → ESP32-S3；esp32 → ESP32。"""
    t = (target or "").strip().strip('"').lower()
    m = re.fullmatch(r"esp32([a-z]\d+)?", t)
    if not m:
        return t.upper() or None
    return "ESP32" + (f"-{m.group(1).upper()}" if m.group(1) else "")


def _fail(kind, message, **extra):
    return {"ok": False, "kind": kind, "message": message, **extra}


def _read(p):
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _walk(root, max_depth=4):
    """不进 .git、build 这类文件夹，最多往下几层。"""
    root = Path(root)
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in SKIP_DIRS and depth < max_depth:
                    stack.append((e, depth + 1))
            else:
                yield e


def _is_idf_root(d):
    return PROJECT_CMAKE.search(_read(Path(d) / "CMakeLists.txt")) is not None


def _find_idf_roots(root):
    roots = []
    for f in _walk(root):
        if f.name == "CMakeLists.txt" and _is_idf_root(f.parent):
            roots.append(f.parent)
    # 套在别的项目里面的不算（比如项目里的示例）
    roots.sort(key=lambda p: len(p.parts))
    top = []
    for r in roots:
        if not any(r != t and t in r.parents for t in top):
            top.append(r)
    return top


def _git(root, *args):
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def normalize_repo(url):
    if not url:
        return None
    u = url.strip()
    u = re.sub(r"^git@([^:]+):", r"\1/", u)
    u = re.sub(r"^[a-z+]+://", "", u)
    u = re.sub(r"^[^@/]+@", "", u)
    return re.sub(r"\.git$", "", u).rstrip("/")


def _idf_min(root):
    """项目要的乐鑫编译工具最低版本：main/idf_component.yml 里的 idf 依赖。"""
    for yml in [root / "main" / "idf_component.yml", root / "idf_component.yml"]:
        text = _read(yml)
        m = re.search(r"^\s*idf\s*:\s*(?:\n\s*version\s*:)?\s*['\"]?\s*>=?\s*v?([\d.]+)", text, re.M)
        if m:
            return m.group(1)
    lock = _read(root / "dependencies.lock")
    m = re.search(r"^\s*idf:\s*\n(?:.*\n)*?\s*version:\s*([\d.]+)", lock, re.M)
    return m.group(1) if m else None


def _target_from(text):
    m = re.search(r'^CONFIG_IDF_TARGET="?([a-z0-9]+)"?', text, re.M)
    return m.group(1) if m else None


def _boards(root):
    """一份源码支持多块板子的写法（板子文件夹里有 config.json，写着芯片）。"""
    base = root / "main" / "boards"
    out = []
    if base.is_dir():
        for cfg in base.rglob("config.json"):
            try:
                data = json.loads(_read(cfg))
            except ValueError:
                continue
            if not isinstance(data, dict) or "target" not in data:
                continue
            out.append({
                "id": cfg.parent.relative_to(base).as_posix(),
                "chip": chip_name(data["target"]),
                "type": data.get("type") or cfg.parent.name,
            })
    return sorted(out, key=lambda b: b["id"])


def _idf_info(root):
    root = Path(root)
    cmake = _read(root / "CMakeLists.txt")
    m = re.search(r"^\s*project\s*\(\s*([A-Za-z0-9_.+-]+)\s*\)", cmake, re.M)
    if m:
        name, why = m.group(1), "项目配置里写的名字（CMakeLists.txt 的 project）"
    else:
        name, why = root.name, "项目配置里没写名字，用文件夹名"
    ver = re.search(r'set\s*\(\s*PROJECT_VER\s+"([^"]+)"', cmake)
    info = {
        "ok": True, "kind": "esp-idf", "platform": "ESP-IDF", "root": str(root),
        "name": name, "name_why": why, "version": ver.group(1) if ver else None,
        "idf_min": _idf_min(root), "chip": None, "chip_why": None,
        "board": None, "boards": [], "needs_board": False,
        "repo": normalize_repo(_git(root, "remote", "get-url", "origin")),
        "commit": _git(root, "rev-parse", "--short=7", "HEAD"),
    }
    built = _target_from(_read(root / "sdkconfig"))
    defaults = _target_from(_read(root / "sdkconfig.defaults"))
    boards = _boards(root)
    if built:
        info["chip"], info["chip_why"] = chip_name(built), "上次编译留下的配置（sdkconfig）"
        board = re.search(r"^CONFIG_BOARD_TYPE_([A-Z0-9_]+)=y", _read(root / "sdkconfig"), re.M)
        if board:
            info["board"] = board.group(1).lower().replace("_", "-")
    elif len(boards) > 1:
        info["boards"], info["needs_board"] = boards, True
    elif len(boards) == 1:
        info["chip"], info["board"] = boards[0]["chip"], boards[0]["id"]
        info["chip_why"] = "项目里唯一一块板子的配置"
    elif defaults:
        info["chip"], info["chip_why"] = chip_name(defaults), "项目默认配置（sdkconfig.defaults）"
    else:
        info["chip"], info["chip_why"] = "ESP32", "项目没指定芯片，乐鑫编译工具默认用 ESP32"
    return info


def _not_firmware(root):
    files = list(_walk(root, max_depth=6))
    names = {f.name for f in files}
    count = lambda ext: sum(1 for f in files if f.suffix == ext)
    if any(f.suffix == ".ioc" for f in files) or any(f.name.lower().startswith("startup_stm32") for f in files):
        return _fail("unsupported", "<b>认出是 STM32 固件，第一版还不支持。</b><br>依据：找到 STM32CubeMX 工程文件（.ioc）或 STM32 的启动文件。<br>码表第一版只支持乐鑫 ESP32 系列，STM32 在计划里（乐鑫 → STM32 → Pico）。", platform="STM32")
    if "pico_sdk_import.cmake" in names:
        return _fail("unsupported", "<b>认出是树莓派 Pico 固件，第一版还不支持。</b><br>依据：找到 pico_sdk_import.cmake。<br>码表第一版只支持乐鑫 ESP32 系列，Pico 在计划里（乐鑫 → STM32 → Pico）。", platform="Pico")
    if "platformio.ini" in names or any(f.suffix == ".ino" for f in files):
        return _fail("unsupported", "<b>认出是 PlatformIO / Arduino 项目，第一版还不支持。</b><br>码表第一版只支持乐鑫官方编译工具（ESP-IDF）编的项目。", platform="PlatformIO/Arduino")
    if "pom.xml" in names or "build.gradle" in names:
        what = f"一个 Java 项目：找到 {'pom.xml' if 'pom.xml' in names else 'build.gradle'} 和 {count('.java')} 个 .java 文件"
    elif "package.json" in names:
        what = f"一个网页或 Node 项目：找到 package.json 和 {count('.js') + count('.ts')} 个 .js/.ts 文件"
    elif "pyproject.toml" in names or count(".py"):
        what = f"一个 Python 项目：找到 {count('.py')} 个 .py 文件"
    else:
        what = f"{len(files)} 个文件，没有单片机项目的特征"
    return _fail("not_firmware", f"<b>这不像单片机固件源码，测不了。</b><br>里面是{what}；<br>没找到编译固件用的配置（CMakeLists.txt、Makefile、platformio.ini 都没有）。<br>码表测的是烧进单片机里的程序，请换一个文件夹。")


def _unpack(arc, dest):
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    name = arc.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(arc) as z:
                for m in z.namelist():
                    if Path(m).is_absolute() or ".." in Path(m).parts:
                        raise ValueError("压缩包里有不安全的路径")
                z.extractall(dest)
        else:
            with tarfile.open(arc) as t:
                t.extractall(dest, filter="data")
    except (zipfile.BadZipFile, tarfile.TarError, ValueError, OSError, EOFError) as e:
        return None, str(e)
    entries = [e for e in dest.iterdir() if e.name != "__MACOSX"]
    return (entries[0] if len(entries) == 1 and entries[0].is_dir() else dest), None


def inspect_source(path, unpack_to=None):
    p = Path(str(path)).expanduser()
    if not p.exists():
        return _fail("missing", f"找不到这个位置：{path}<br>看看路径是不是写错了，或者文件夹被挪走了。")
    archive = None
    if p.is_file():
        if p.name.lower().endswith(ARCHIVES):
            if unpack_to is None:
                unpack_to = tempfile.mkdtemp(prefix="fsw-unpack-")  # 不在你的文件夹里解
            root, err = _unpack(p, unpack_to)
            if root is None:
                return _fail("bad_archive", f"<b>压缩包解不开。</b><br>{p.name} 可能没下载完整或已损坏（{err}）。请重新下载，或者直接选解开后的文件夹。")
            archive, p = str(p), root
        else:
            return _fail("single_file", f"\"{p.name}\" 是单个文件。编译要整个项目，请拖整个固件文件夹，或者它的压缩包。")
    if not any(p.iterdir()):
        return _fail("empty", "<b>这个文件夹是空的。</b><br>请换一个装着固件源码的文件夹。")
    roots = _find_idf_roots(p)
    if not roots:
        return _not_firmware(p)
    if len(roots) > 1:
        projects = [{"root": str(r), "name": _idf_info(r)["name"]} for r in roots]
        return _fail("multiple", f"这个文件夹里有 {len(roots)} 个固件项目，请选一个。", projects=projects, archive=archive)
    info = _idf_info(roots[0])
    info["archive"] = archive
    return info
