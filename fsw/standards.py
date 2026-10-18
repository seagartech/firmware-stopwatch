"""读合理范围（第一版：码表自带的标准文件，fsw/standards/*.json）。

标准文件就是一个场景（fsw-scenario/1 的段、线、合理范围、体验标准、问题规则），另外写了每个计时点在代码里的位置。
真找到的步骤按代码位置对上标准里的计时点（同一个文件、行号相差不超过几行），对上的段和线就用标准里的范围：
- 标准里正好有这一段：直接用；
- 标准里是连着的几段：范围、算慢线都加起来；含推算的段，整段标"推算"；
- 对不上的段：没有范围，只记用时、不判快慢。
问题规则里看线先后的照用；看附加值的（比如"第一包声音到达时设备的状态"）要这个计时点真记了附加值才用：
插计时点时码表按 needed_values 让 AI 顺带记上（只读已有的变量），插入记录里有了才判，没记上的不判。
多轮实测和报告按"这次实际插进去的计时点"算（inserted_page）：删掉的、不测的顺带线、编译出错撤掉的不要，你自己加的要。
"""

import json
from pathlib import Path

from fsw.scenario import experiences

HERE = Path(__file__).parent / "standards"
REF_FILE = HERE / "reference.json"  # 通用参考值固定在码表里；HERE 可以换成别的标准文件夹（测试用）
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
    """码表自带的设备标准。10-18 起不再自带任何设备的（原来那份小智专用的挪到测试素材里）：通用代码不迁就某一个固件；
    以后用户自己放进来的标准照样能对上。"""
    out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(HERE.glob("*.json"))]
    return [d for d in out if d.get("segments")]


def reference():
    """通用的体验参考值（现在只有对话的 1.5 秒），不属于哪个设备。"""
    try:
        return json.loads(REF_FILE.read_text(encoding="utf-8")).get("experience")
    except (OSError, ValueError):
        return None


def _at(s):
    f, _, line = s.rpartition(":")
    return f, int(line)


def _line(s):
    """步骤的行号；不是整数（人改过、老的识别结果里留着 "655-660" 这类）就当没有，不对标准，不出错。"""
    try:
        return int(s.get("l"))
    except (TypeError, ValueError):
        return None


def match_points(steps, standard):
    """真步骤的计时点 → 标准里的计时点（一对一，取最近的）。"""
    cands = []
    for s in steps:
        mine = _line(s)
        if mine is None:
            continue
        for sp, info in standard.get("points", {}).items():
            f, line = _at(info["at"])
            if (s.get("path") or s.get("f")) == f and abs(mine - line) <= NEAR:
                cands.append((abs(mine - line), s["p"], sp))
    out, used = {}, set()
    for _, mine, theirs in sorted(cands):
        if mine not in out and theirs not in used:
            out[mine] = theirs
            used.add(theirs)
    return out


def find(steps, pool=None):
    """挑对得上最多计时点的标准；少于 2 个对上就算没有。pool 不给就用码表自带的（现在没有）。"""
    best, n = None, 1
    for s in (load_all() if pool is None else pool):
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
    extra = [(g["a"], g["b"], g) for g in page.get("xsegs") or []]  # 不在哪条线上的段（你自己加的步骤）：只记用时
    for a, b, g in main + side + extra:
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
    # 要测的这条线中途有"第一下感觉到的结果"（AI 标的 felt=first，比如开始出声、锁开始动）、终点在它之后：
    # 另记一条到那一步的线。体验标准只套在这条上；到最后一下的长短常常取决于内容多少（回答多长、门多重）
    pn = {x["p"]: x["n"] for x in steps}
    felt = {x["p"]: x.get("felt") for x in steps}
    cut = next((i for i, (_, b, _) in enumerate(main[:-1]) if felt.get(b) == "first"), None)
    if cut is not None:
        end = pn[main[cut][1]]
        lines[-1].update(name=f"到{pn[main[-1][1]]}", end=pn[main[-1][1]])  # 两条都按终点叫，免得"开口这条"其实量到全部放完
        lines.insert(len(lines) - 1, {"name": f"到{end}", "end": end, "first": True, "range": None, "slow": None, "std": None,
                                      "segments": [f"{a}-{b}" for a, b, _ in main[:cut + 1]]})
    exps = []  # 体验标准每条线一条：标准里那条线对上了真线，就挂到真线上
    for e in experiences(standard or {}):
        hit = [l for l in lines if l["std"] == e.get("line")]
        if hit and e.get("fair") is not None:
            exps.append({**e, "line": hit[0]["name"]})
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
    sname = next((x["n"] for x in steps if x["p"] == start), steps[0]["n"] if steps else "起点")
    return {"format": "fsw-scenario/1", "name": "真步骤", "start": {"point": start, "name": sname},
            "segments": segments, "lines": [{k: v for k, v in l.items() if k != "std"} for l in lines],
            "experiences": exps, "problems": problems, "standard": (standard or {}).get("name"),
            "point_names": {x["p"]: x["n"] for x in steps},
            "point_kinds": {x["p"]: x["kind"] for x in steps if x.get("felt") and x.get("kind")},
            "optional": [x["p"] for x in steps if x.get("opt")],
            "points": {x["p"]: {"n": x["n"], "f": x.get("f") or Path(x.get("path") or "").name, "l": x.get("l")} for x in steps},
            "matched": mp}


def _merge(parts):
    """几段接成一段（中间的计时点没插）：名字连起来，都是外部的才算外部。"""
    if len(parts) == 1:
        return dict(parts[0])
    who = []
    for g in parts:
        if g.get("who") and g["who"] not in who:
            who.append(g["who"])
    out = {"a": parts[0]["a"], "b": parts[-1]["b"], "t": "＋".join(g.get("t") or f"{g['a']}-{g['b']}" for g in parts),
           "who": "、".join(who)}
    if all(g.get("out") for g in parts):
        out["out"] = True
    return out


def _contract(segs, keep):
    """一条线上的段，去掉没插的计时点：经过它的前后两段接成一段；线头、线尾没插的那段不要。"""
    out, acc = [], []
    for g in segs:
        if acc and g["a"] != acc[-1]["b"]:  # 接不上（不连续）：前面没收住的不要
            acc = []
        if not acc and g["a"] not in keep:
            continue
        acc.append(g)
        if g["b"] in keep:
            out.append(_merge(acc))
            acc = []
    return out


def inserted_page(page, steps=None, inserted=None):
    """按这次实际插进去的计时点改写结果页数据，多轮实测、报告、"以前测过的"重算都用它（不用 AI 最初找到的那份）。
    steps：插计时点时用的步骤（核对后的，含你自己加的 U1、U2……）；不给就用结果页的步骤。
    inserted：插入记录里真插上的计时点编号（P3、P21……）；不给就当 steps 全插上了。
    - 编号统一换成计时点编号（你加的 U1 → P21），和设备打印的一致；
    - 没插的计时点（删掉的、不测的顺带线、编译出错撤掉的）：经过它的前后两段接成一段，线头线尾的那段不要；
    - 结果页的段里没有的计时点（你自己加的）：记一段"从起点到这一步"，不放进哪条线。"""
    from fsw.instrument import point_id
    steps = list(steps if steps is not None else page.get("steps") or [])
    orig = {s["p"]: s for s in page.get("steps") or []}

    ren, kept = {}, []
    for s in steps:
        k = point_id(s, steps)
        if inserted is not None and k not in inserted:
            continue
        ren[s["p"]] = k
        o = {**orig.get(s["p"], {}), **{f: s[f] for f in ("n", "path", "l", "line") if s.get(f) is not None}}
        kept.append({**o, "p": k, "f": o.get("f") or Path(o.get("path") or "").name})
    keep = set(ren)
    # 要测的这条线上只在某种触发方式才走的计时点（opt，比如点屏幕开始）不放进线里：只记"从起点到这一步"。
    # 顺带那条线（比如表情）的点不一定每轮都有也照样留在线上：哪一轮没收到，只是那一轮这条线算不出来
    keep_line = keep - {s["p"] for s in steps if (orig.get(s["p"]) or s).get("opt")
                        and (orig.get(s["p"]) or s).get("line") != "face"}
    main = _contract(page.get("segs") or [], keep_line)
    side = list(page.get("fsegs") or [])
    while side and side[0]["a"] not in keep:  # 顺带的线从一个没插的点分出去：往前接到要测的这条线上插了的那个点
        prev = next((g for g in page.get("segs") or [] if g["b"] == side[0]["a"]), None)
        if not prev:
            break
        side.insert(0, prev)
    side = _contract(side, keep_line)
    if side and side[-1]["b"] in {x for g in main for x in (g["a"], g["b"])}:
        side = []  # 顺带那条线自己的点一个都没插（不测这条线）：只剩和要测的这条重合的那段，不算一条线

    def re_id(g):
        return {**g, "a": ren[g["a"]], "b": ren[g["b"]]}
    main, side = [re_id(g) for g in main], [re_id(g) for g in side]
    used = {x for g in main + side for x in (g["a"], g["b"])}
    start = main[0]["a"] if main else None
    xsegs = [{"a": start, "b": s["p"], "t": s["n"], "who": ""} for s in kept if start and s["p"] not in used]
    return {**page, "steps": kept, "segs": main, "fsegs": side, "xsegs": xsegs,
            "main": [s["p"] for s in kept if s.get("line") != "face"]}




def with_default_experience(sc):
    """第一下感觉到的结果是声音的（AI 标的 kind），那条线还没有体验标准时，挂上码表自带标准里的
    对话体验参考值（1.5 秒，出处写在标准里）。不是声音的、或已经有体验标准的不动。"""
    from fsw.scenario import experience_of
    lines = sc.get("lines") or []
    kinds = sc.get("point_kinds") or {}
    # 套在终点是"第一下感觉到的结果"的线上：有"到第一下"那条就用它；没有就用要测的这条（它的终点就是唯一那一下）
    line = next((l for l in lines if l.get("first")), None) or (lines[-1] if lines else None)
    if not line or any(experience_of(sc, l["name"]) for l in lines):
        return sc
    end = ((sc.get("segments") or {}).get((line.get("segments") or [""])[-1]) or {}).get("to")
    if kinds.get(end) != "声音":  # 参考值说的是"人说完到听到回答"：只套在终点是听到声音的线上（AI 标的 kind，不按名字猜）
        return sc
    ref = reference()
    if not ref or ref.get("fair") is None:
        return sc
    return {**sc, "experiences": list(sc.get("experiences") or []) + [{**ref, "line": line["name"]}]}
