"""判快慢、结论标签。全部按固定规则从数据算，不用大模型、不由 AI 判断（决策 52）。

两把尺子（10-19 用户定：去掉合理范围清单）：
- 体验标准（用户等不等得了）：按设备类别从标准库挑，挂在"第一下感觉到结果"的那条线上，判整条线；
- 和上次比（第二次起）：每段、每条线和上次比快了、慢了还是持平（两次都满 10 轮才比）。
结论标签（10-20 用户定，决策 167）：
- 必须改：达标率 < 50%（判"差"）；或超过标准 1.5 倍的轮次 > 10%
- 建议改：达标率 < 90%；或比上次慢了（中位数高出 10% 与 0.1 秒中较大的那个以上）
- 达标率：没超过体验标准的轮次占几成（10-20 用户定，换掉自造的"10 次里最慢"）；后两条测满 10 轮才判
- 出错：对错问题（比如回答开头被吞），单独一块，只写出错的轮次，不分必须 / 建议
- 未判：既没有体验标准、也没有上次可比，这次只记用时，明说
- 达标：体验标准内，且没比上次慢；放回答的线不判快慢，也收在这里（页面上只写"另有 N 条达标 / 不判快慢"）
"偶发特别慢"不再单独出标签：偶尔慢由达标率 < 90% 管
"""

import math

from fsw.scenario import experience_of

LEVELS = ["出错", "必须改", "建议改", "未判", "达标"]   # 越重要越靠前：出错（功能坏了）＞ 必须改 ＞ 建议改
TAIL_BIG = 1.5   # 超过标准几倍的轮次 > 10% 算必须改
PASS_BAD, PASS_OK = 0.5, 0.9   # 达标率 < 50% 必须改，< 90% 建议改
FEW = 20          # 少于 20 轮：结论只供参考
SPIKE_RATIO, SPIKE_GAP = 2.5, 0.3   # 偶发特别慢：最慢一次超过一般的 2.5 倍，且多出 0.3 秒以上
OFTEN = 0.1       # 每 10 次 1 次以上算"经常"


def med(a):
    s = sorted(a)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def p90(a):
    """10 次里最慢：从快到慢排，第 90% 那一个（20 轮里是第 18 慢……即第 18 个）。"""
    s = sorted(a)
    return s[max(0, math.ceil(len(s) * 0.9) - 1)]


def stats(vals):
    return {"med": round(med(vals), 6), "p90": round(p90(vals), 6), "max": round(max(vals), 6), "min": round(min(vals), 6)}


ANSWER = "看回答长短"  # 放回答的几段、到全部放完那条：长短跟这次回答说了多少字有关，不判快慢
ANSWER_TXT = "取决于回答长短，不判快慢"
RECORDED = "已记录"  # 第一次测（没有上次可比）：段只记用时
FLAT_S, FLAT_PCT = 0.1, 0.1  # 和上次比：差值要同时大于 0.1 秒、大于上次的 10%，才算慢了 / 快了，否则持平（决策 167）


def vs_prev(now, prev, noise=None):
    """和上次比：{"prev": 上次中位数, "d": 这次 − 上次, "change": 慢了 / 快了 / 持平}；没有上次返回 None。"""
    if prev is None or now is None or prev < 0 or now < 0:  # 负数是计时点先后打乱了（10-20 真测：上次 -0.002 秒），这个数不成立，不比
        return None
    d = now - prev
    flat = abs(d) <= max(FLAT_S, abs(prev) * FLAT_PCT)  # noise 不再用，保留参数只为调用方不用改
    return {"prev": round(prev, 6), "d": round(d, 6), "change": "持平" if flat else "慢了" if d > 0 else "快了"}


def pass_stats(vals, fair):
    """达标率：没超过体验标准（≤ 及格线）的轮次占几成；big 是超过标准 1.5 倍的轮数。"""
    ok = sum(1 for v in vals if v <= fair)
    return {"pass_n": ok, "pass_rate": round(ok / len(vals), 4), "big_n": sum(1 for v in vals if v > fair * TAIL_BIG)}


def experience(line_name, st, exp):
    """exp：这条线的体验标准（每条线一条，没设定的不判）。标准里给了"好"的档（good）才分三档：差 / 可以 / 好；
    只有及格线的照旧两档（10-19 用户定：不自己编"好"的数）。差 = 达标率 < 50%（10-20 用户定，和必须改同一条）。"""
    if not exp or exp.get("line") != line_name or exp.get("fair") is None:
        return None
    if st.get("pass_rate", 1 if st["med"] <= exp["fair"] else 0) < PASS_BAD:
        return "差"
    return "好" if exp.get("good") is not None and st["med"] <= exp["good"] else "可以"


def exp_txt(e, es, med):
    """体验标准那半句：好 / 可以（离好还差多少）/ 达到及格线（这个标准没给"好"的档）。"""
    if e == "好":
        return f"体验好（{es.get('short') or '体验标准'}：≤{es['good']} 秒算好）"
    if e == "可以" and es.get("good") is not None:
        return f"体验可以，离好（{es['good']} 秒）还差 {med - es['good']:.2f} 秒"
    if e == "可以":
        return f"达到及格线（{es.get('short') or '体验标准'}，≤{es['fair']} 秒），这个标准没给\"好\"的档"
    return None


def combo(exp, vs=None, big=None, es=None, med=None):
    """每条线一句话：体验标准 + 和上次比。"""
    if exp == "差" and big:
        head = f"体验差：慢在「{big[0]}」（{big[1]:.2f} 秒，占 {big[2]}%）"
    elif exp in ("好", "可以") and es and med is not None:
        head = exp_txt(exp, es, med)
    else:
        head = {"差": "体验差", "可以": "体验可以", "好": "体验好"}.get(exp)
    tail = None if not vs else "和上次比持平" if vs["change"] == "持平" else f"比上次{vs['change']} {abs(vs['d']):.2f} 秒"
    if not head and not tail:
        return "没有体验标准、也没有上次可比：只记下用时，先不判快慢"
    return "；".join(x for x in (head, tail) if x)


def _big(line, seg_st, segs_def, st):
    """这条线里用时最大的一段：(名字, 中位数, 占这条线中位数的百分比)。"""
    mine = [k for k in line["segments"] if k in seg_st]
    if not mine or not st["med"]:
        return None
    k = max(mine, key=lambda k: seg_st[k]["med"])
    return segs_def[k]["name"], seg_st[k]["med"], round(seg_st[k]["med"] / st["med"] * 100)


def merge_overruns(scenario, rounds):
    """"记最后一次"的中间点越过了下一步（比如"最后一包录音发出"：服务器回了听写之后设备还会再发几包），
    这一步后面那段就会变成负数、前面那段多算：只要有一轮这样，就把前后两段合成一段（从前一步直接到下一步），写明为什么。
    返回 (合并后的场景, 合并后的每一轮, 合并说明)；没有要合并的原样返回。"""
    segs = scenario.get("segments") or {}
    last = set(scenario.get("last_points") or []) - {(scenario.get("start") or {}).get("point")}
    notes = []
    for k2, g2 in list(segs.items()):
        a = g2["from"]
        k1 = next((k for k, g in segs.items() if g["to"] == a and k != k2), None)
        if a not in last or not k1 or not any((r["segs"].get(k2) or 0) < 0 for r in rounds):
            continue
        g1 = segs[k1]
        pn = scenario.get("point_names") or {}
        # 段的编号是"起点-终点"写法的（比如 P1-P2）跟着改成新的起止；名字用"前一步到下一步"，原来两段的名字放进合并说明
        nk = f"{g1['from']}-{g2['to']}" if k1 == f"{g1['from']}-{a}" else k1
        name = f"{pn[g1['from']]}到{pn[g2['to']]}" if g1["from"] in pn and g2["to"] in pn else f"{g1['name']}、{g2['name']}"
        merged = {**{x: v for x, v in g1.items() if x not in ("range", "slow")}, "to": g2["to"], "name": name}  # 合理范围不再用（10-19）
        segs = {(nk if k == k1 else k): (merged if k == k1 else g) for k, g in segs.items() if k != k2}
        rounds = [{**r, "segs": {**{k: v for k, v in r["segs"].items() if k not in (k1, k2)},
                                 nk: None if r["segs"].get(k1) is None or r["segs"].get(k2) is None
                                 else round(r["segs"][k1] + r["segs"][k2], 6)}} for r in rounds]
        lines = [{**l, "segments": [nk if k == k1 else k for k in l["segments"] if k != k2]} for l in scenario["lines"]]
        notes.append({"segment": nk, "point": a, "names": [g1["name"], g2["name"]],
                      "why": f"「{(scenario.get('point_names') or {}).get(a, a)}」这一步在下一步之后还在发生（有的轮比下一步还晚），"
                             f"分不出两段各占多少，合成一段算"})
        scenario = {**scenario, "segments": segs, "lines": lines}
    return scenario, rounds, notes


def judge(scenario, rounds, prev=None):
    """scenario：fsw-scenario/1（段、线、体验标准、问题规则）；rounds：多轮实测收齐的每一轮；
    prev：上次的中位数（{"segs", "lines", "noise", "noise_lines"}，见网页后台的"和上次比"），第一次测没有。"""
    n = len(rounds)
    segs_def, lines_def = scenario["segments"], scenario["lines"]
    pv = prev if prev and prev.get("segs") is not None and n >= 10 else None  # 两次都满 10 轮才比
    out = {"n": n, "few": n < FEW, "segments": [], "lines": [], "labels": []}

    # 有"到第一声"那条线时，第一声之后到全部放完的那几段是在放回答：长短跟回答说了多少有关，不算"偶发特别慢"
    first = next((l for l in lines_def if l.get("first")), None)
    playing = set(lines_def[-1]["segments"]) - set(first["segments"]) if first else set()
    seg_st = {}
    for k, g in segs_def.items():
        vals = [r["segs"][k] for r in rounds if r["segs"].get(k) is not None]
        if not vals:
            continue
        st = stats(vals)
        seg_st[k] = st
        # 放回答那几段里每轮都差不多长的（比如放完设备里剩下的声音，每轮 0.12～0.14 秒）跟回答长短无关：照常判
        by_answer = k in playing and st["max"] - st["min"] > SPIKE_GAP
        spiky = not by_answer and st["max"] > st["med"] * SPIKE_RATIO and st["max"] - st["med"] > SPIKE_GAP
        vs = None if by_answer or not pv else vs_prev(st["med"], pv["segs"].get(k), (pv.get("noise") or {}).get(k))
        out["segments"].append({"k": k, "name": g["name"], "owner": g.get("owner", ""), **st,
                                "outside": g.get("side") == "outside",
                                "verdict": "推算" if g.get("basis") == "estimated" else ANSWER if by_answer else vs["change"] if vs else RECORDED, "vs": vs,
                                "spiky": spiky,
                                "spiky_rounds": [r["n"] for r in rounds if r["segs"].get(k) is not None
                                                 and r["segs"][k] > st["med"] * SPIKE_RATIO] if spiky else []})

    for i, l in enumerate(lines_def):
        ends = [r["ends"][i] for r in rounds if r["ends"][i] is not None]
        if not ends:
            continue
        st = stats(ends)
        # 有"到第一声"那条时，"到全部放完"那条主要看回答说了多少字：不判快慢
        by_answer = bool(first) and l is lines_def[-1] and l is not first
        es = experience_of(scenario, l["name"])
        if es and es.get("line") == l["name"] and es.get("fair") is not None and not by_answer:
            st = {**st, **pass_stats(ends, es["fair"])}
            fair = es["fair"]  # 超标的是第几轮：写建议时交给 AI，免得它写"码表没告诉我是第几轮"（10-20）
            st["over_rounds"] = [r["n"] for r in rounds if r["ends"][i] is not None and r["ends"][i] > fair]
            st["big_rounds"] = [r["n"] for r in rounds if r["ends"][i] is not None and r["ends"][i] > fair * TAIL_BIG]
        e = experience(l["name"], st, es)
        vs = None if by_answer or not pv else vs_prev(st["med"], (pv.get("lines") or {}).get(l["name"]), (pv.get("noise_lines") or {}).get(l["name"]))
        out["lines"].append({"name": l["name"], "end": l.get("end", l["name"]), **st, "count": len(ends),
                             "verdict": ANSWER if by_answer else vs["change"] if vs else RECORDED, "exp": e, "vs": vs,
                             "exp_std": {k: es.get(k) for k in ("fair", "good", "source", "short", "category", "start_gap")} if es else None,
                             "combo": ANSWER_TXT if by_answer and e is None else combo(e, vs, _big(l, seg_st, segs_def, st), es, st["med"]),
                             "segments": l["segments"]})

    # 第一次反应：每轮几条线里最早到的那一下
    if out["lines"]:
        first = min(out["lines"], key=lambda x: x["med"])
        firsts = [min(x for x in r["ends"] if x is not None) for r in rounds if any(x is not None for x in r["ends"])]
        out["first"] = {"line": first["name"], **stats(firsts)}
    if len(out["lines"]) >= 2:
        # 先给的反应比"第一个能感觉到的结果"早多少：有"到第一声"那条就和它比，不和"全部放完"比
        j = next((i for i, l in enumerate(lines_def) if l.get("first") and i > 0), len(lines_def) - 1)
        a, b = out["lines"][0], next((x for x in out["lines"] if x["name"] == lines_def[j]["name"]), out["lines"][-1])
        diffs = [r["ends"][j] - r["ends"][0] for r in rounds if None not in (r["ends"][0], r["ends"][j])]
        out["lead"] = {"first": a["name"], "second": b["name"], **stats(diffs)}

    # 要测的那条线（最后一条）里最大头的一段
    # 和报告、写建议同一条主线：看回答长短的线不算（10-20 修）
    keep = [x for x in out["lines"] if x["verdict"] != ANSWER] or out["lines"]
    main = keep[-1] if keep else None
    if main:
        mine = [k for k in main["segments"] if k in seg_st]
        out["biggest"] = max(mine, key=lambda k: seg_st[k]["med"]) if mine else None
    # 最值得优化的一段（10-19 用户定，固定点名）：第一下感觉到结果的那条线上，设备自己（代码上能改）的段里中位数最大的；
    # 设备外的（服务器、网络）、看回答长短的、推算的不算。只写占这条线多少，不分等级
    want = next((l for l in lines_def if l.get("first")), lines_def[-1] if lines_def else {})  # 体验标准挂的那条线
    tgt = next((x for x in out["lines"] if x["name"] == want.get("name")), main)
    if tgt:
        byk = {g["k"]: g for g in out["segments"]}
        cand = [k for k in tgt["segments"] if k in byk and not byk[k]["outside"] and byk[k]["verdict"] not in (ANSWER, "推算")]
        if cand:
            k = max(cand, key=lambda k: byk[k]["med"])
            out["best_fix"] = {"k": k, "name": byk[k]["name"], "med": byk[k]["med"], "line": tgt["name"],
                               "share": round(byk[k]["med"] / tgt["med"] * 100, 1) if tgt["med"] else None,
                               # 设备上能改的段合计占多少：最大那段占得少时，看得出是整条线本来就主要花在设备外
                               "device_share": round(sum(max(0, byk[x]["med"]) for x in cand) / tgt["med"] * 100, 1) if tgt["med"] else None}

    labels = []
    line_by = {l["name"]: l for l in out["lines"]}
    def rate_txt(l):  # 「达标率 80%，10 次里 2 次超过标准 2.0 秒」
        return f"达标率 {l['pass_rate'] * 100:.0f}%，{l['count']} 次里 {l['count'] - l['pass_n']} 次超过标准 {l['exp_std']['fair']} 秒"

    sevs = {}
    for l in out["lines"]:  # 每条线都列：没问题的收在"另有 N 条达标"里，条数才对得上
        vs = l["vs"]
        fair = (l.get("exp_std") or {}).get("fair")
        sev = 0   # 同一级里谁更重：超标越多越靠前
        if l["verdict"] == ANSWER:
            lv, why = "达标", ANSWER_TXT
        elif l["exp"] == "差":
            lv, why, sev = "必须改", rate_txt(l), 2 - l["pass_rate"]
        elif "pass_rate" in l and l["count"] >= 10 and l["big_n"] / l["count"] > round(1 - PASS_OK, 6):  # 1 - 0.9 算出来是 0.0999…，不取整会把正好 10% 判成大于 10%
            lv, why, sev = "必须改", f"{l['count']} 次里 {l['big_n']} 次超过标准 {fair} 秒的 {TAIL_BIG} 倍", 1 + l["big_n"] / l["count"]
        elif "pass_rate" in l and l["count"] >= 10 and l["pass_rate"] < PASS_OK:
            lv, why, sev = "建议改", rate_txt(l), 10 + 1 - l["pass_rate"]   # 超标的排在"比上次慢"前面
        elif vs and vs["change"] == "慢了":
            lv, why, sev = "建议改", f"比上次慢了 {vs['d']:.2f} 秒", vs["d"]
        elif l["exp"] is None and not vs:
            lv, why = "未判", "没有体验标准，也没有上次可比，这次只记用时"
        else:
            lv = "达标"
            why = "；".join(x for x in (exp_txt(l["exp"], l["exp_std"] or {}, l["med"]) if l["exp"] else None,
                                         None if not vs else "和上次比持平" if vs["change"] == "持平" else f"比上次快了 {-vs['d']:.2f} 秒") if x)
        labels.append({"k": f"line:{l['name']}", "kind": "line", "line": l["name"], "level": lv, "why": why,
                       **({"rounds": l["big_rounds"], "tail": TAIL_BIG} if why.endswith(f"{TAIL_BIG} 倍") else {})})  # 点名了超过几倍的轮：逐轮图标红这几轮
        sevs[f"line:{l['name']}"] = sev
    for p in scenario.get("problems") or []:  # 对错问题：功能出错，不是快慢，单独一块，只写出错的轮次
        rs = [r["n"] for r in rounds if p["key"] in (r.get("probs") or [])]
        if not rs:
            continue
        labels.append({"k": f"problem:{p['key']}", "kind": "problem", "name": p.get("name", p["key"]), "rounds": rs,
                       "order": (p.get("rule") or {}).get("type") == "line_order",
                       "level": "出错", "why": f"{n} 轮里出现 {len(rs)} 轮"})
        sevs[f"problem:{p['key']}"] = len(rs)   # 出错轮次越多越靠前
    order = {k: i for i, k in enumerate(LEVELS)}
    out["labels"] = sorted(labels, key=lambda x: (order[x["level"]], -sevs.get(x["k"], 0)))  # 先按重要性分级，同一级里越重越靠前
    return out
