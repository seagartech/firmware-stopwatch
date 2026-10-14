"""项目工作区：把源码复制到纯英文路径（原文件夹不动）、算源码指纹、建码表自己的项目文件夹。

码表项目文件夹放步骤、句子清单、合理范围等，不跟着代码走；认"是不是同一个项目"看仓库地址和项目配置，不看路径和名字。
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from fsw.workspace.inspect import SKIP_DIRS, inspect_source

# 编译时自动生成、会变的文件，不算进指纹
GENERATED = {"sdkconfig", "sdkconfig.old", "dependencies.lock"}


def ascii_work_root(data_dir):
    """乐鑫编译器读不了中文路径：结果文件夹是纯英文就放它下面，否则换到纯英文的临时目录。"""
    data_dir = Path(data_dir).expanduser()
    for cand in [data_dir / "work", Path(tempfile.gettempdir()) / "fsw-work",
                 Path("C:/fsw-work") if os.name == "nt" else Path("/tmp/fsw-work")]:
        if str(cand).isascii():
            return cand
    raise RuntimeError("找不到纯英文的工作目录")


def _source_files(root):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name not in GENERATED:
                yield Path(dirpath) / name


def fingerprint(root):
    """源码指纹：所有源文件的相对路径和内容一起算哈希，不含编译产物和 .git。"""
    root = Path(root)
    h = hashlib.sha256()
    for f in _source_files(root):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()[:16]


def project_key(info):
    if info.get("repo"):
        return "git:" + info["repo"]
    return f"cfg:{info['platform']}:{info['name']}"


def _slug(name, key):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "project"
    return f"{s}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def find_project(data_dir, key):
    data_dir = Path(data_dir).expanduser()
    if not data_dir.is_dir():
        return None
    for rec in data_dir.glob("*/project.json"):
        try:
            if json.loads(rec.read_text(encoding="utf-8")).get("key") == key:
                return rec.parent
        except (OSError, ValueError):
            continue
    return None


def prepare(src, data_dir, board=None, unpack_to=None):
    info = inspect_source(src, unpack_to=unpack_to)
    if not info.get("ok"):
        return info
    if info["needs_board"]:
        chosen = next((b for b in info["boards"] if b["id"] == board), None)
        if not chosen:
            return {"ok": True, "status": "needs_board", "info": info}
        info.update(chip=chosen["chip"], board=chosen["id"], needs_board=False,
                    chip_why="你选的板子的配置")
    root = Path(info["root"])
    key = project_key(info)
    data_dir = Path(data_dir).expanduser()
    proj = find_project(data_dir, key) or data_dir / _slug(info["name"], key)
    proj.mkdir(parents=True, exist_ok=True)
    rec_path = proj / "project.json"
    old = json.loads(rec_path.read_text(encoding="utf-8")) if rec_path.exists() else None

    fp = fingerprint(root)
    work = ascii_work_root(data_dir) / proj.name / "src"
    status = "new" if old is None else "unchanged" if old.get("fingerprint") == fp else "changed"
    if status != "unchanged" or not work.exists():
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(root, work, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    # 没改过的源码不重新复制：编好的原版（build 文件夹）留着，还原和算计时点开销都要用
    rec = {
        "key": key, "name": info["name"], "platform": info["platform"], "chip": info["chip"],
        "board": info["board"], "idf_min": info["idf_min"], "repo": info["repo"], "commit": info["commit"],
        "source": str(Path(src).expanduser()), "fingerprint": fp, "work": str(work),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "history": (old or {}).get("history", []) + ([] if status == "unchanged" else [fp]),
    }
    rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "status": status, "info": info, "fingerprint": fp,
            "work": str(work), "project_dir": str(proj)}
