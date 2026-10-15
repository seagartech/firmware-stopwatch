"""读合理范围（第一版：码表自带的标准文件，fsw/standards/*.json）。

标准文件就是一个场景（fsw-scenario/1 的段、线、合理范围、体验标准、问题规则），另外写了每个计时点在代码里的位置。
真找到的步骤按代码位置对上标准里的计时点（同一个文件、行号相差不超过几行），对上的段和线就用标准里的范围：
- 标准里正好有这一段：直接用；
- 标准里是连着的几段：范围、算慢线都加起来；含推算的段，整段标"推算"；
- 对不上的段：没有范围，只记用时、不判快慢。
问题规则里看线先后的照用；看附加值的（比如"第一包声音到达时设备的状态"）要这个计时点真记了附加值才用：
插计时点时码表按 needed_values 让 AI 顺带记上（只读已有的变量），插入记录里有了才判，没记上的不判。
"""

import json
from pathlib import Path

HERE = Path(__file__).parent / "standards"
NEAR = 8  # 行号相差几行以内算同一个位置


def digest(standard):
    """标准的指纹：合理范围、算慢线、体验标准有任何改动都会变。"""
    import hashlib
    if not standard:
        return None
    return hashlib.sha1(json.dumps(standard, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def changed_at(standard):
    """标准文件最后改的时间（给提示条用）。"""
    import time
    for p in HERE.glob("*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("name") == (standard or {}).get("name"):
                return time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime))
        except (OSError, ValueError):
            continue
    return "最近"


def load_all():
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(HERE.glob("*.json"))]


def _at(s):
    f, _, line = s.rpartition(":")
    return f, int(line)


def match_points(steps, standard):
    """真步骤的计时点 → 标准里的计时点（一对一，取最近的）。"""
    cands = []
    for s in steps:
        for sp, info in standard.get("points", {}).items():
            f, line = _at(info["at"])
            if (s.get("path") or s.get("f")) == f and abs(int(s["l"]) - line) <= NEAR:
                cands.append((abs(int(s["l"]) - line), s["p"], sp))
    out, used = {}, set()
    for _, mine, theirs in sorted(cands):
        if mine not in out and theirs not in used:
            out[mine] = theirs
            used.add(theirs)
    return out


def find(steps):
    """挑对得上最多计时点的标准；少于 2 个对上就算没有。"""
    best, n = None, 1
    for s in load_all():
        k = len(match_points(steps, s))
        if k > n:
            best, n = s, k
    return best


def _chain(standard, a, b):
    """标准里从计时点 a 连到 b 的几段（直接一段优先）。"""
    edges = {}
    for k, g in standard["segments"].items():
        edges.setdefault(g["from"], []).append((g["to"], k))

    def walk(p, seen):
        if p == b:
            return []
        for to, k in sorted(edges.get(p, []), key=lambda x: x[0] != b):
            if to not in seen:
                rest = walk(to, seen | {to})
                if rest is not None:
                    return [k] + rest
        return None
    return walk(a, {a}) if a != b else None


def _sum_range(standard, keys):
    gs = [standard["segments"][k] for k in keys]
    if any(g.get("range") is None for g in gs):
        return None, None, "measured"
    lo = round(sum(g["range"][0] for g in gs), 6)
    hi = None if any(g["range"][1] is None for g in gs) else round(sum(g["range"][1] for g in gs), 6)
    slow = None if any(g.get("slow") is None for g in gs) else round(sum(g["slow"] for g in gs), 6)
    basis = "estimated" if any(g.get("basis") == "estimated" for g in gs) else "measured"
    return [lo, hi], slow, basis


def _value_rules(rule):
    if rule.get("type") == "any":
        return [x for r in rule.get("rules", []) for x in _value_rules(r)]
    return [rule] if rule.get("type") == "value_not" else []


def needed_values(steps, standard):
    """真步骤里哪几个计时点要顺带记附加值：标准里看附加值的问题规则，它的计时点对上了真插的哪一个。"""
    if not standard:
        return {}
    back = {theirs: mine for mine, theirs in match_points(steps, standard).items()}
    out = {}
    for p in standard.get("problems") or []:
        for r in _value_rules(p["rule"]):
            mine = back.get(r["point"])
            if mine:
                vo = r.get("value_of") or {}
                x = out.setdefault(mine, {"what": vo.get("what", "附加值"), "ok": vo.get("ok", ""), "how": vo.get("how", ""),
                                          "value": r["value"], "for": []})
                x["for"].append(p.get("name", p["key"]))
    return out


def _translate(rule, back, values):
    """标准里的问题规则换成真计时点的编号；换不过来的（点没插、附加值没记）返回 None。"""
    t = rule.get("type")
    if t == "any":
        rs = [x for x in (_translate(r, back, values) for r in rule.get("rules", [])) if x]
        return None if not rs else rs[0] if len(rs) == 1 else {**rule, "rules": rs}
    if t == "value_not":
        mine = back.get(rule["point"])
        if not mine or mine not in values:
            return None
        expect = values[mine].get("expect")
        return {**rule, "point": mine, "value": rule["value"] if expect is None else expect}
    if t == "point_before":
        a, b = back.get(rule["a"]), back.get(rule["b"])
        return {**rule, "a": a, "b": b} if a and b else None
    return None


def real_scenario(page, standard, values=None):
    """真找完的步骤（结果页数据）+ 标准 → 判快慢用的场景。没有标准就只有段和线、没有范围。"""
    steps = page.get("steps") or []
    mp = match_points(steps, standard) if standard else {}
    main = [(g["a"], g["b"], g) for g in page.get("segs") or []]
    side = [(g["a"], g["b"], g) for g in page.get("fsegs") or []]
    if side:  # 顺带那条线从起点算：先走要测的这条，到分叉的地方再拐过去
        prefix = []
        for a, b, g in main:
            if a == side[0][0]:
                break
            prefix.append((a, b, g))
        side = prefix + side
    segments = {}
    for a, b, g in main + side:
        k = f"{a}-{b}"
        rng, slow, basis = None, None, "measured"
        if standard and a in mp and b in mp:
            ch = _chain(standard, mp[a], mp[b])
            if ch:
                rng, slow, basis = _sum_range(standard, ch)
        segments[k] = {"name": g.get("t") or k, "what": g.get("t") or "", "owner": g.get("who", ""),
                       "side": "outside" if g.get("out") else "device", "from": a, "to": b,
                       "range": rng, "slow": slow, "basis": basis}

    def std_line(a, b):
        if not standard or a not in mp or b not in mp:
            return None
        for l in standard["lines"]:
            segs = [standard["segments"][k] for k in l["segments"]]
            if segs and segs[0]["from"] == mp[a] and segs[-1]["to"] == mp[b]:
                return l
        return None

    ln = page.get("ln") or {}
    lines = []
    for name, items in ([(ln.get("face", "顺带的那条"), side)] if side else []) + [(ln.get("talk", "要测的这条"), main)]:
        if not items:
            continue
        sl = std_line(items[0][0], items[-1][1])
        lines.append({"name": name, "end": name, "segments": [f"{a}-{b}" for a, b, _ in items],
                      "range": sl["range"] if sl else None, "slow": sl["slow"] if sl else None, "std": sl["name"] if sl else None})
    exp = None
    if standard and standard.get("experience"):
        hit = [l for l in lines if l["std"] == standard["experience"]["line"]]
        if hit:
            exp = {**standard["experience"], "line": hit[0]["name"]}
    problems = []
    names = {l["std"]: l["name"] for l in lines if l["std"]}
    back = {theirs: mine for mine, theirs in mp.items()}
    for p in (standard or {}).get("problems") or []:
        r = p["rule"]
        if r["type"] == "line_order":
            if r["first"] in names and r["second"] in names:
                problems.append({**p, "rule": {**r, "first": names[r["first"]], "second": names[r["second"]]}})
        elif t := _translate(r, back, values or {}):
            problems.append({**p, "rule": t})
    start = main[0][0] if main else None
    return {"format": "fsw-scenario/1", "name": "真步骤", "start": {"point": start, "name": (steps[0]["n"] if steps else "起点")},
            "segments": segments, "lines": [{k: v for k, v in l.items() if k != "std"} for l in lines],
            "experience": exp, "problems": problems, "standard": (standard or {}).get("name"),
            "point_names": {x["p"]: x["n"] for x in steps},
            "matched": mp}
