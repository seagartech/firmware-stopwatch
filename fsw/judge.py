"""判快慢、两把尺子、结论标签。全部按固定规则从数据算，不用大模型、不由 AI 判断（决策 52）。

两把尺子：
- 体验标准（用户等不等得了）：只挂在有公认标准的那条线上（比如对话开口 1.5 秒），定结论；
- 合理范围（这套做法有没有做坏）：每段、每条线都有，放技术细节。
每条线按两把尺子的组合给一句话：
- 体验差 + 在合理范围内：每段都没做坏，是做法本身慢，要换做法才会快
- 体验差 + 超出合理范围：先查哪段做坏了
- 体验可以 + 超出合理范围：可能有隐患
- 都正常：正常
结论标签：
- 必须改：中位数超过慢阈值，或体验标准判"差"，或 10 次里最慢超过慢阈值；对错问题（比如回答开头被吞）出现就算
- 建议改：偶发特别慢、先后反了，但没到上一条（不到每 10 次 1 次以上）；或超出合理范围但未超过慢阈值
- 可以不改：在合理范围内
"""

import math

from fsw.scenario import experience_of

LEVELS = ["必须改", "建议改", "可以不改"]
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


def verdict(st, rng, slow, basis="measured"):
    """每段 / 每条线按中位数判：正常（不超过合理范围上限）、偏慢（超过上限、未超过慢阈值）、超过慢阈值；
    推算的段只标"推算"；没有范围的不判。"""
    if basis == "estimated":
        return "推算"
    if not rng:
        return "没有范围"
    lo, hi = rng
    if slow is not None and st["med"] > slow:
        return "超过慢阈值"
    if hi is not None and st["med"] > hi:
        return "偏慢"
    return "正常"


def experience(line_name, st, exp):
    """exp：这条线的体验标准（每条线一条，没设定的不判）。"""
    if not exp or exp.get("line") != line_name or exp.get("fair") is None:
        return None
    return "差" if st["med"] > exp["fair"] else "可以"


def combo(exp, inside, verdict="正常", big=None):
    if verdict == "没有范围" and exp is None:
        return "还没有合理范围：只记下用时，先不判快慢"
    if verdict == "没有范围" and exp == "差" and big:  # 第一次测：没有范围可查"哪段做坏了"，直接说慢在哪一段
        return f"体验差：慢在「{big[0]}」（{big[1]:.2f} 秒，占 {big[2]}%）"
    if exp is None:
        return "只按合理范围判：正常" if inside else "只按合理范围判：先查哪段做坏了"
    if exp == "差":
        return "每段都没做坏，是做法本身慢，要换做法才会快" if inside else "先查哪段做坏了"
    return "正常" if inside else "可能有隐患"


def _big(line, seg_st, segs_def, st):
    """这条线里用时最大的一段：(名字, 中位数, 占这条线中位数的百分比)。"""
    mine = [k for k in line["segments"] if k in seg_st]
    if not mine or not st["med"]:
        return None
    k = max(mine, key=lambda k: seg_st[k]["med"])
    return segs_def[k]["name"], seg_st[k]["med"], round(seg_st[k]["med"] / st["med"] * 100)


def judge(scenario, rounds):
    """scenario：fsw-scenario/1（段、线、合理范围、体验标准、问题规则）；rounds：多轮实测收齐的每一轮。"""
    n = len(rounds)
    segs_def, lines_def = scenario["segments"], scenario["lines"]
    out = {"n": n, "few": n < FEW, "segments": [], "lines": [], "labels": [], "range_list": scenario.get("range_list")}

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
        spiky = k not in playing and st["max"] > st["med"] * SPIKE_RATIO and st["max"] - st["med"] > SPIKE_GAP
        out["segments"].append({"k": k, "name": g["name"], "owner": g.get("owner", ""), **st,
                                "range": g.get("range"), "slow": g.get("slow"), "outside": g.get("side") == "outside",
                                "verdict": ANSWER if k in playing else verdict(st, g.get("range"), g.get("slow"), g.get("basis", "measured")),
                                "spiky": spiky,
                                "spiky_rounds": [r["n"] for r in rounds if r["segs"].get(k) is not None
                                                 and r["segs"][k] > st["med"] * SPIKE_RATIO] if spiky else []})

    for i, l in enumerate(lines_def):
        ends = [r["ends"][i] for r in rounds if r["ends"][i] is not None]
        if not ends:
            continue
        st = stats(ends)
        # 有"到第一声"那条时，"到全部放完"那条主要看回答说了多少字：不按合理范围判快慢
        by_answer = bool(first) and l is lines_def[-1] and l is not first
        v = ANSWER if by_answer else verdict(st, l.get("range"), l.get("slow"))
        es = experience_of(scenario, l["name"])
        e = experience(l["name"], st, es)
        out["lines"].append({"name": l["name"], "end": l.get("end", l["name"]), **st, "range": l.get("range"),
                             "slow": l.get("slow"), "verdict": v, "exp": e, "inside": v == "正常", "new": bool(l.get("new")),
                             "exp_std": {k: es.get(k) for k in ("fair", "good", "source", "short")} if es else None,
                             "combo": ANSWER_TXT if by_answer and e is None else combo(e, v == "正常", v, _big(l, seg_st, segs_def, st)),
                             "over_slow": 0 if by_answer else sum(1 for x in ends if l.get("slow") is not None and x > l["slow"]),
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
    main = out["lines"][-1] if out["lines"] else None
    if main:
        mine = [k for k in main["segments"] if k in seg_st]
        out["biggest"] = max(mine, key=lambda k: seg_st[k]["med"]) if mine else None

    labels = []
    line_by = {l["name"]: l for l in out["lines"]}
    for l in out["lines"]:
        slow = None if l["verdict"] == ANSWER else l["slow"]
        p90_ok = n >= 10  # 少于 10 轮，"10 次里最慢"没意义，不拿它判
        if (slow is not None and (l["med"] > slow or (p90_ok and l["p90"] > slow))) or l["exp"] == "差":
            lv = "必须改"
        elif l["verdict"] == "偏慢":
            lv = "建议改"
        else:
            lv = "可以不改"
        if True:  # 每条线都列：没问题的收在"另有 N 条不用改"里，条数才对得上
            why = ("体验标准判差" if l["exp"] == "差" else "中位数超过慢阈值" if slow is not None and l["med"] > slow
                   else "10 次里最慢超过慢阈值" if slow is not None and p90_ok and l["p90"] > slow
                   else "超出合理范围但未超过慢阈值" if lv == "建议改" else "还没有合理范围" if l["verdict"] == "没有范围"
                   else ANSWER_TXT if l["verdict"] == ANSWER
                   else "在合理范围内")
            labels.append({"k": f"line:{l['name']}", "kind": "line", "line": l["name"], "level": lv, "why": why})
    for p in scenario.get("problems") or []:
        rs = [r["n"] for r in rounds if p["key"] in (r.get("probs") or [])]
        if not rs:
            continue
        often = len(rs) / max(1, n) > OFTEN
        lv = "必须改" if p.get("level") == "bad" or often else "建议改"
        labels.append({"k": f"problem:{p['key']}", "kind": "problem", "name": p.get("name", p["key"]), "rounds": rs,
                       "order": (p.get("rule") or {}).get("type") == "line_order",
                       "level": lv, "why": "对错问题，出现就要改" if p.get("level") == "bad" else
                       "超过每 10 次 1 次" if often else "偶尔出现"})
    for g in out["segments"]:
        if not g["spiky"]:
            continue
        hit = [l for l in out["lines"] if g["k"] in l["segments"] and l["slow"] is not None and l["verdict"] != ANSWER and n >= 10 and l["p90"] > l["slow"]]
        labels.append({"k": f"spiky:{g['k']}", "kind": "spiky", "segment": g["k"], "rounds": g["spiky_rounds"],
                       "level": "必须改" if hit else "建议改",
                       "why": "偶发特别慢，而且每 10 次就有 1 次让人等太久" if hit else "偶发特别慢"})
    # 同一个根因：某段"偶发特别慢"只发生在某个对错问题出现的那几轮里（比如回答开头被吞那一轮，解码送喇叭也特别慢），
    # 算同一个问题：挂到那个问题上，不单独列
    for x in labels:
        if x["kind"] != "spiky":
            continue
        host = next((p for p in labels if p["kind"] == "problem" and not p["order"] and set(x["rounds"]) <= set(p["rounds"])), None)
        if host:
            x["with"] = host["k"]
    order = {k: i for i, k in enumerate(LEVELS)}
    out["labels"] = sorted(labels, key=lambda x: order[x["level"]])  # 同一级里：线、问题、偶发特别慢（和原型的顺序一样）
    return out
