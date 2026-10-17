"""项目工作区：把源码复制到纯英文路径（原文件夹不动）、算源码指纹、建码表自己的项目文件夹。

码表项目文件夹放步骤、句子清单、合理范围等，不跟着代码走；认"是不是同一个项目"看仓库地址和项目配置，不看路径和名字。
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from fsw.workspace.inspect import SKIP_DIRS, inspect_source

# 编译时会重写的文件：核对工作副本、还原后逐字节比对时不算
GENERATED = {"sdkconfig", "sdkconfig.old", "dependencies.lock"}
# 不算进指纹的：编译时自动生成的。sdkconfig 是用户的配置（menuconfig 改的），要算进指纹，只改配置也算源码改了
FP_SKIP = {"sdkconfig.old", "dependencies.lock"}
# 源码改了、重新复制工作副本时作废的东西：都是按旧源码生成的（插入备份、插入方案、计时版固件）。
# 原版固件（firmware/orig）留着：设备上可能还是旧源码的计时版，还原时要烧它；它记着是哪份源码编的，不会被当成新源码的原版。
# 设备上现在是哪一版（on-device.json）、上次插入的参数（fsw-args.json）也留着，"还原成原版"要用
STALE = ("fsw-backup", "fsw-plan.json", "firmware/fsw")
HEADER_NAME = "fw_stopwatch.h"
_lock = threading.RLock()  # 复制、还原工作副本一次只做一件（后台编原版和找关键步骤可能同时开始）


def ascii_work_root(data_dir):
    """乐鑫编译器读不了中文路径：结果文件夹是纯英文就放它下面，否则换到纯英文的临时目录。"""
    data_dir = Path(data_dir).expanduser()
    for cand in [data_dir / "work", Path(tempfile.gettempdir()) / "fsw-work",
                 Path("C:/fsw-work") if os.name == "nt" else Path("/tmp/fsw-work")]:
        if str(cand).isascii():
            return cand
    raise RuntimeError("找不到纯英文的工作目录")


def _source_files(root, skip=GENERATED):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name not in skip:
                yield Path(dirpath) / name


def fingerprint(root):
    """源码指纹：所有源文件（含 sdkconfig）的相对路径和内容一起算哈希，不含编译产物和 .git。"""
    root = Path(root)
    h = hashlib.sha256()
    for f in _source_files(root, FP_SKIP):
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


def work_ok(root, work):
    """工作副本是不是源码的完整、干净副本：源码里的每个文件副本里都有、一字节不差，也没有源码里没有的计时点工具。
    编译时生成的文件（build、sdkconfig 等）不算。"""
    root, work = Path(root), Path(work)
    if not work.is_dir():
        return False
    for f in _source_files(root):
        w = work / f.relative_to(root)
        if not w.is_file() or w.stat().st_size != f.stat().st_size or w.read_bytes() != f.read_bytes():
            return False
    return not _stray_header(root, work)


def _stray_header(root, work):
    """副本里有、源码里没有的计时点工具：上次插的计时点没撤干净。"""
    return any(not (Path(root) / f.relative_to(work)).exists()
               for f in _source_files(work, ()) if f.name == HEADER_NAME)


def _copy(root, work):
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(root, work, ignore=shutil.ignore_patterns(*SKIP_DIRS))


LAST = "fsw-last"  # 改完再测（任务 22）：作废前把上次的插入方案和原版文件留一份，看计时点那几处改没改


def _keep_last(proj):
    """作废前留一份上次的插入方案和插计时点前的原版文件（两样都有才留），供"计时点那几处没动就沿用"比对。"""
    proj = Path(proj)
    plan, files = proj / "fsw-plan.json", proj / "fsw-backup" / "files"
    if not (plan.is_file() and files.is_dir()):
        return
    last = proj / LAST
    shutil.rmtree(last, ignore_errors=True)
    last.mkdir(parents=True)
    shutil.copy2(plan, last / "fsw-plan.json")
    shutil.copytree(files, last / "files")


def _drop_stale(proj):
    _keep_last(proj)
    for name in STALE:
        p = Path(proj) / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _clean(root, work, backup):
    """源码没变：把副本还原成干净的。有插入记录就按记录还原（记录和副本对得上才还原，编好的原版留着）；
    还原不了、或者还原后和源码对不上（副本缺文件、被改过），就重新复制。返回是否重新复制了。"""
    from fsw import instrument as ins  # 插计时点的模块反过来用到这里，放到用的时候再导入
    if ins.current(backup) is not None and not ins.restore(work, backup)["ok"]:
        ins.discard(backup)
    if work_ok(root, work):
        return False
    _copy(root, work)
    ins.discard(backup)
    return True


def ensure_clean(project_dir):
    """编原版、找关键步骤、插计时点、还原之前调用：保证工作副本里没有上次留下的计时点。
    没有插入记录、也没有多出来的计时点工具，就什么都不做（不逐个文件比对，很快）。"""
    from fsw import instrument as ins
    proj = Path(project_dir)
    try:
        rec = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": True, "did": None}
    work, backup = Path(rec["work"]), proj / "fsw-backup"
    root = rec.get("root") or rec.get("source")
    with _lock:
        has_root = bool(root) and Path(root).is_dir()
        if ins.current(backup) is None and not (has_root and _stray_header(root, work)):
            return {"ok": True, "did": None}
        if ins.current(backup) is not None and ins.restore(work, backup)["ok"] \
                and not (has_root and _stray_header(root, work)):
            return {"ok": True, "did": "restored"}
        if not has_root:
            return {"ok": False, "message": "工作副本里还留着上次插的计时点，原来的源码又找不到了，没法重新复制。回首页重新选一次源码。"}
        _copy(root, work)
        ins.discard(backup)
        fp = fingerprint(root)
        changed = fp != rec.get("fingerprint")
        if changed:  # 源码在这期间改过：按改过的源码记下来，跟旧源码绑定的东西作废
            _drop_stale(proj)
            rec.update(fingerprint=fp, updated=datetime.now().isoformat(timespec="seconds"),
                       history=rec.get("history", []) + [fp])
            (proj / "project.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "did": "copied", "changed": changed}


def prepare(src, data_dir, board=None, unpack_to=None):
    info = inspect_source(src, unpack_to=unpack_to)
    if not info.get("ok"):
        return info
    if info["needs_board"]:
        chosen = next((b for b in info["boards"] if b["id"] == board), None)
        if not chosen and board is None:  # 再测：用上次选的板子，不再让人重选（决策 130）
            last = find_project(Path(data_dir).expanduser(), project_key(info))
            try:
                last_board = json.loads((last / "project.json").read_text(encoding="utf-8")).get("board") if last else None
            except (OSError, ValueError):
                last_board = None
            chosen = next((b for b in info["boards"] if b["id"] == last_board), None)
            if chosen:
                info["board_from"] = "last"
        if not chosen:
            return {"ok": True, "status": "needs_board", "info": info}
        info.update(chip=chosen["chip"], board=chosen["id"], needs_board=False,
                    chip_why="上次选的板子的配置" if info.get("board_from") == "last" else "你选的板子的配置")
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
    backup = proj / "fsw-backup"
    with _lock:
        if status != "unchanged" or not work.exists():
            # 新的或改过的源码：重新复制，按旧源码插的备份、插入方案、计时版固件都作废（不然下次插入前的还原会把旧文件盖上来）
            _copy(root, work)
            if status == "unchanged":  # 源码没变、只是副本没了：插入方案还能用，只作废插入备份
                from fsw import instrument as ins
                ins.discard(backup)
            else:
                _drop_stale(proj)
            copied = True
        else:
            # 没改过的源码不重新复制：编好的原版（build 文件夹）留着，还原和算计时点开销都要用；
            # 但上次没还原留下的计时点要撤掉，副本缺文件、被改过要重新复制
            copied = _clean(root, work, backup)
    rec = {
        "key": key, "name": info["name"], "platform": info["platform"], "chip": info["chip"],
        "board": info["board"], "idf_min": info["idf_min"], "repo": info["repo"], "commit": info["commit"],
        "source": str(Path(src).expanduser()), "root": str(root), "fingerprint": fp, "work": str(work),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "history": (old or {}).get("history", []) + ([] if status == "unchanged" else [fp]),
    }
    rec_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "status": status, "info": info, "fingerprint": fp,
            "work": str(work), "project_dir": str(proj), "copied": copied}
