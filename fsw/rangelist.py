"""两次测量的段、线怎么对上（"和上次比"用）。

合理范围清单 2026-10-19 去掉了（用户："删"）：判快慢只用体验标准和"和上次比"，项目文件夹里留着的老
合理范围清单.md 码表不再读，也不删。这里只留按计时点在代码里的位置配对：重新找步骤后编号（P7、P8……）会重排，
按位置才认得出两次是不是同一段、同一条线。
"""

MIN_ROUNDS = 10  # 测满这么多轮才拿来和上次比：跑流程的几轮不算数


def at_now(scenario):
    return {p: x.get("at") for p, x in (scenario.get("points") or {}).items() if x.get("at")}


def _seg_sig(g, at):
    a, b = at.get(g.get("from")), at.get(g.get("to"))
    return (a, b) if a and b else None


def line_sig(scenario, l, at):
    segs = [scenario["segments"].get(k) for k in l.get("segments") or []]
    if not segs or None in segs:
        return None
    a, b = at.get(segs[0].get("from")), at.get(segs[-1].get("to"))
    return [a, b] if a and b else None


def pair_segments(old, old_at, new, new_at):
    """两份段（老的 → 新的）谁是谁：{老编号: 新编号}。两边都记了计时点位置就按位置对（编号重排也认得出）；
    有一边没记，就要名字一样，再按编号或起止计时点对。对不上的不在结果里。"""
    out = {}
    if old_at and new_at:
        idx = {}
        for k, g in old.items():
            s = _seg_sig(g, old_at)
            if s:
                idx.setdefault(s, []).append(k)
        for k, g in new.items():
            s = _seg_sig(g, new_at)
            hit = idx.get(s) if s else None
            if hit and len(hit) == 1:  # 同一处代码插了两个点（极少）：分不清，不对
                out[hit[0]] = k
        return out
    by_pts = {(g.get("from"), g.get("to")): k for k, g in old.items()}
    for k, g in new.items():
        ok = k if k in old else by_pts.get((g.get("from"), g.get("to")))
        if ok is not None and old[ok].get("name") == g.get("name"):
            out[ok] = k
    return out


def pair_lines(old_names, old_sigs, new_names, new_sigs):
    """两份测量线谁是谁：{老名字: 新名字}。两边都知道线的起点、终点在代码里的位置就按位置对（线改了名也认得出）；
    否则按名字对。"""
    out = {}
    for n, s in zip(new_names, new_sigs):
        hit = [o for o, os_ in zip(old_names, old_sigs) if os_ and s and list(os_) == list(s)]
        if len(hit) == 1:
            out[hit[0]] = n
        elif not s or not any(old_sigs):
            if n in old_names:
                out[n] = n
    return out
