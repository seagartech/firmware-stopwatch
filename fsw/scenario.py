"""场景和通用数据格式（fsw-scenario/1）。

一个场景 = 一个起点 + 最多 4 条线；每条线有终点和按顺序的几段；每段有中文名、谁负责（设备里 / 设备外）、
合理范围、是实测还是推算。场景还带：每轮做什么、固定清单、什么算问题、有没有体验标准。
场景是读完代码后得出的（recognized 写"读代码认出来的"），不是事先选的。分叉、循环第一版不做；
一轮里会重复的那段（比如每次调用工具）用 each=true 的线表示，按每次分别算。

单位一律是秒。起点是"设备最早知道的时刻"；之前看不见的那段写在 start.unseen 里，只推算、不计入。
"""

FORMAT = "fsw-scenario/1"
SET_FORMAT = "fsw-scenario-set/1"
SIDES = {"device": "设备里", "outside": "设备外"}
BASIS = {"measured": "实测", "estimated": "推算"}
MODES = {"round": "按轮测（每轮触发一次）", "event": "按事件测（事件自己发生）"}
LEVELS = {"warn", "bad"}
RULES = {
    "line_order": ("first", "second"),      # 第一条线的终点反而比第二条晚
    "value_not": ("point", "value"),        # 某计时点的附加值不是预期的
    "point_before": ("a", "b"),             # 计时点 a 反而早于 b
    "segment_over": ("segment", "seconds"),  # 某段超过多少秒
    "line_over": ("line", "seconds"),       # 某条线超过多少秒
    "any": ("rules",),                      # 满足其中任一条
}


class ScenarioError(ValueError):
    pass


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_range(where, r, errs):
    if r is None:
        return
    if not (isinstance(r, list) and len(r) == 2 and _num(r[0]) and (r[1] is None or _num(r[1]))):
        errs.append(f"{where}的合理范围要写成 [最少秒数, 最多秒数]")
    elif r[1] is not None and r[0] > r[1]:
        errs.append(f"{where}的合理范围写反了：{r[0]} 比 {r[1]} 大")


def _check_rule(rule, s, errs, where):
    t = rule.get("type") if isinstance(rule, dict) else None
    if t not in RULES:
        errs.append(f"{where}：不认识的问题规则「{t}」，只认 {'、'.join(RULES)}")
        return
    for field in RULES[t]:
        if field not in rule:
            errs.append(f"{where}：规则 {t} 缺 {field}")
    lines = {l.get("name") for l in s.get("lines", [])}
    if t == "line_order":
        for f in ("first", "second"):
            if rule.get(f) not in lines:
                errs.append(f"{where}：没有「{rule.get(f)}」这条线")
    if t == "segment_over" and rule.get("segment") not in s.get("segments", {}):
        errs.append(f"{where}：没有「{rule.get('segment')}」这一段")
    if t == "line_over" and rule.get("line") not in lines:
        errs.append(f"{where}：没有「{rule.get('line')}」这条线")
    if t == "any":
        for r in rule.get("rules", []):
            _check_rule(r, s, errs, where)


def check(s):
    """检查一个场景写得对不对；不对就用大白话说哪里不对。"""
    errs = []
    name = s.get("name") or "（没写名字的场景）"
    if s.get("format") != FORMAT:
        errs.append(f"格式标记要写 {FORMAT}")
    if not s.get("recognized"):
        errs.append("要写 recognized：场景是怎么来的（比如\"读代码认出来的\"）")
    if s.get("mode") not in MODES:
        errs.append(f"mode 只能是 {'、'.join(MODES)}")
    start = s.get("start") or {}
    if not start.get("name") or not start.get("point"):
        errs.append("起点要写名字和计时点（start.name、start.point）")
    segs = s.get("segments") or {}
    for k, g in segs.items():
        label = f"「{g.get('name', k)}」"
        for f in ("name", "owner", "from", "to"):
            if not g.get(f):
                errs.append(f"{label}缺 {f}")
        if g.get("side") not in SIDES:
            errs.append(f"{label}的 side 要写设备里（device）或设备外（outside）")
        if g.get("basis") not in BASIS:
            errs.append(f"{label}的 basis 要写实测（measured）或推算（estimated）")
        _check_range(label, g.get("range"), errs)
        r, slow = g.get("range"), g.get("slow")
        if slow is not None and r and r[1] is not None and slow < r[1]:
            errs.append(f"{label}的算慢线 {slow} 秒比合理范围上限 {r[1]} 秒还低")
    lines = s.get("lines") or []
    if not 1 <= len(lines) <= 4:
        errs.append(f"一个场景最多 4 条线、至少 1 条，现在有 {len(lines)} 条")
    names = [l.get("name") for l in lines]
    if len(set(names)) != len(names):
        errs.append("线的名字重复了")
    for l in lines:
        ln = f"「{l.get('name')}」"
        keys = l.get("segments") or []
        if not keys:
            errs.append(f"{ln}一段都没有")
            continue
        missing = [k for k in keys if k not in segs]
        if missing:
            errs.append(f"{ln}里没有这一段：{'、'.join(missing)}")
            continue
        first = segs[keys[0]]
        if not l.get("each") and start.get("point") and first["from"] != start["point"]:
            errs.append(f"{ln}要从起点 {start['point']} 出发，第一段「{first['name']}」是从 {first['from']} 开始的")
        for a, b in zip(keys, keys[1:]):
            if segs[a]["to"] != segs[b]["from"]:
                errs.append(f"{ln}里「{segs[a]['name']}」和「{segs[b]['name']}」接不上（{segs[a]['to']} → {segs[b]['from']}）")
        _check_range(ln, l.get("range"), errs)
    exp = s.get("experience")
    if exp is not None and (exp.get("line") not in names or not _num(exp.get("fair"))):
        errs.append("体验标准要挂在一条存在的线上，并写多少秒算差（fair）")
    order = s.get("expect_order")
    if order is not None and (len(order) != 2 or any(o not in names for o in order)):
        errs.append("expect_order 要写两条存在的线：先到的在前")
    for gap in s.get("gaps") or []:
        if gap.get("earlier") not in names or gap.get("later") not in names:
            errs.append(f"差值「{gap.get('name')}」要指向两条存在的线")
    for p in s.get("problems") or []:
        where = f"问题「{p.get('name')}」"
        if p.get("level") not in LEVELS:
            errs.append(f"{where}的 level 只能是 warn 或 bad")
        _check_rule(p.get("rule"), s, errs, where)
    if s.get("mode") == "round" and not (s.get("per_round") or {}).get("items"):
        errs.append("按轮测要写每轮做什么和固定清单（per_round.items）")
    if s.get("mode") == "event":
        ev = s.get("event") or {}
        if not ev.get("trigger") or not _num(ev.get("max_wait")):
            errs.append("按事件测要写事件怎么发生（event.trigger）和最多等多久（event.max_wait）")
    if errs:
        raise ScenarioError(f"场景「{name}」写得不对：" + "；".join(errs))
    return s


def load_scenarios(data):
    """读一个场景，或一组场景（按事件测时一个事件一个场景）。"""
    if isinstance(data, dict) and data.get("format") == SET_FORMAT:
        return [check(s) for s in data.get("scenarios", [])]
    return [check(data)]


def _fmt(x):
    return repr(float(x))


def to_page(demo):
    """演示数据（场景 + 一次测量结果 + 页面要的其他内容）→ 网页用的数据，页面代码不用改。"""
    s = check(demo["scenario"])
    res, extra = demo["result"], demo["page"]
    start, unseen = s["start"], s["start"].get("unseen") or {}
    exp = s.get("experience")
    S = {
        "crumb": extra["crumb"],
        "startShort": start["short"],
        "start": start["title"],
        "est": {"label": "推算段：" + unseen.get("name", ""), "note": unseen.get("note", "")},
        "exp": {"fair": exp["fair"], "label": f"{exp['fair']} 秒：超过算体验差（{exp['source']}）"} if exp else None,
        "segs": {k: {"name": g["name"], "what": g["what"], "owner": g["owner"],
                     "code": f"{g['from']}→{g['to']}",
                     "ok": f"{_fmt(g['range'][0])}～{_fmt(g['range'][1])}",
                     "v": res["segments"][k]} for k, g in s["segments"].items()},
        "lines": [{"name": l["name"], "end": l["end"], "phrase": l["phrase"], "segs": l["segments"],
                   "ok": l["range"], "slow": l["slow"]} for l in s["lines"]],
        "firstBeforeSecond": bool(s.get("expect_order")),
        "fixes": extra["fixes"], "tech": extra["tech"], "fine": extra["fine"],
    }
    pr = s["per_round"]
    C = {k: pr[k] for k in ("dev", "act", "unit", "cnt", "say", "said", "mode", "who", "listName",
                            "per", "why", "wait", "items", "miss", "beepNote")}
    C["ph"] = [p["name"] for p in pr["phases"]]
    C["pcode"] = [int(p["point"].lstrip("P")) for p in pr["phases"]]
    C["probs"] = [{"k": p["key"], "t": p["name"], "c": p["level"], "rounds": res["problems"].get(p["key"], [])}
                  for p in s.get("problems", [])]
    return {"S": S, "C": C}
