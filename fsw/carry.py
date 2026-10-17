"""改完再测（任务 22）：改了代码，但插计时点的那几处代码没动，就沿用上次的关键步骤和插法，不再调用大模型。

怎么判断"没动"：上次插计时点时留了原版文件（fsw-backup/files/）。每一处插入点取原版里前后几行，
到新代码里找一模一样的一段；每一处都找到了，就按找到的位置把行号挪过去（上面加了几行，下面的行号跟着变）。
有一处找不到，就整份不沿用，照第一次那样重新找、重新插，并说清是哪几处变了。
"""

import json
from pathlib import Path

from fsw import instrument as ins

BEFORE, AFTER = 3, 2  # 插入点前面几行、后面几行要一模一样


def _lines(p):
    return Path(p).read_text(encoding="utf-8", errors="replace").splitlines()


def _find(old, at, new):
    """old 里第 at 行（插在它后面）前后几行，在 new 里的位置；找不到返回 None，有几处一样的取离原位置最近的。"""
    lo, hi = max(0, at - BEFORE), min(len(old), at + AFTER)
    block = [x.rstrip() for x in old[lo:hi]]
    if not block or not any(b.strip() for b in block):
        return None
    n = len(block)
    hits = [i for i in range(len(new) - n + 1) if [x.rstrip() for x in new[i:i + n]] == block]
    if not hits:
        return None
    i = min(hits, key=lambda h: abs(h - lo))
    return at + (i - lo)


def _last_identify(project_dir, what, provider):
    """这个项目上次找关键步骤的结果（要测的一样、用的大模型一样），最新的那份。"""
    best = None
    for f in (Path(project_dir) / "identify").glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("ok") and r.get("what", "").strip() == what.strip() and r.get("provider") == provider:
            if best is None or f.stat().st_mtime > best[0]:
                best = (f.stat().st_mtime, r)
    return best[1] if best else None


def carry(project_dir, work, what, provider):
    """{"ok": True, "result": 新的识别结果, "plan": 新的插入方案（json 文本）, "moved": 挪了行号的处数}；
    沿用不了：{"ok": False, "why": 一句话, "changed": [变了的计时点名字]}。不调用大模型。"""
    proj, work = Path(project_dir), Path(work)
    try:
        plan = json.loads((proj / "fsw-plan.json").read_text(encoding="utf-8"))
        data = ins._json_block(plan["reply"])
        inserts = data["inserts"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"ok": False, "why": "上次没留下插入方案", "changed": []}
    r = _last_identify(proj, what, provider)
    if not r:
        return {"ok": False, "why": "上次没找过这一项（要测的不一样）", "changed": []}
    names = {s["id"]: s["name"] for s in r["steps"]}
    names.update({f"P{ins.number(s['p'], plan.get('steps') or [])}": s["n"] for s in plan.get("steps") or [] if "p" in s})
    old_files, new_files, changed, shift = {}, {}, [], {}
    for x in inserts:
        f = x.get("file")
        try:
            old = old_files.setdefault(f, _lines(proj / "fsw-backup" / "files" / f))
            new = new_files.setdefault(f, _lines(work / f))
        except OSError:
            changed.append(names.get(x.get("step"), x.get("step")))
            continue
        to = _find(old, int(x["after_line"]), new)
        if to is None:
            changed.append(names.get(x.get("step"), x.get("step")) if x.get("step") not in ("include", "reset") else "计时点辅助代码")
            continue
        shift[(x["step"], f)] = to - int(x["after_line"])
        x["after_line"] = to
    if changed:
        return {"ok": False, "why": "这几处代码变了", "changed": sorted(set(changed))}
    steps = []
    for s in r["steps"]:
        d = next((v for (k, f), v in shift.items() if k == s["id"] and f == s["file"]), None)
        if d is None:  # 这一步没有对应的插入（不该发生）：不冒险，重新找
            return {"ok": False, "why": f"「{s['name']}」对不上上次的插法", "changed": [s["name"]]}
        steps.append({**s, "line": s["line"] + d})
    new_plan_steps = []
    for s in plan.get("steps") or []:
        k = f"P{ins.number(s['p'], plan['steps'])}"
        d = next((v for (st, f), v in shift.items() if st == k and f == s.get("path")), 0)
        new_plan_steps.append({**s, "l": s["l"] + d})
    moved = sum(1 for v in shift.values() if v)
    return {"ok": True, "result": {**r, "steps": steps}, "plan": json.dumps(data, ensure_ascii=False, indent=1),
            "plan_steps": new_plan_steps, "moved": moved}


def sig(steps):
    """插入方案和这次的步骤对不对得上：只看计时点、文件、行号。"""
    return [(s.get("p"), s.get("path"), s.get("l")) for s in steps or []]

