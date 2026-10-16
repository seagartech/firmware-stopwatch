"""多轮实测的计算：读设备调试口的计时点行，按轮切分，算每一段、每条线用了多久，查问题。

底子是验证期的收数据小程序：
- 行格式 FSW,P<编号>,<开机后微秒>,<附加值>；FSW,RESET,<微秒>,0 是一轮开始。
- 有的计时点只在内存里记"最后一次"、到后面才打印，所以日志里的先后不等于发生的先后：一律按时间戳算。
- 同一轮同一个计时点只认第一次。开始收的时候已经在一轮中间的那半轮不算。
- 一轮要的计时点都收到了就算这一轮（不用等下一轮开始）；下一轮开始了还没收齐，就是"没收全"，不算、自动补测。
- 连起点都没有的一轮不算一轮（比如对话类喊唤醒词那一下），不报没收全。
设备输出原样存一份（码表不改日志去向），这里只读。纯计算，不用大模型。
"""

import re
from dataclasses import dataclass, field

POINT = re.compile(r"FSW,(P\d+|RESET),(-?\d+),(-?\d+)")


def parse_line(line):
    m = POINT.search(line or "")
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


@dataclass
class Spec:
    """测什么：起点、每一段（从哪个计时点到哪个）、每条线由哪几段接成、什么算问题、一轮要收齐哪些计时点。"""
    start: str
    segments: dict
    lines: list
    problems: list = field(default_factory=list)
    need: list | None = None
    names: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.need is None:
            pts = {self.start}
            for l in self.lines:
                for a, b in l["segs"]:
                    pts.update((a, b))
            for a, b in self.segments.values():
                pts.update((a, b))
            self.need = sorted(pts, key=lambda p: int(p[1:]))

    @classmethod
    def from_scenario(cls, s):
        segs = {k: [g["from"], g["to"]] for k, g in s["segments"].items()}
        lines = [{"name": l["name"], "segs": [segs[k] for k in l["segments"]]} for l in s["lines"]]
        names = dict(s.get("point_names") or {})
        names.setdefault(s["start"]["point"], s["start"]["name"])
        for p in (s.get("per_round") or {}).get("phases") or []:
            names.setdefault(p["point"], p["name"])
        for g in s["segments"].values():
            names.setdefault(g["to"], g["name"] + "（终点）")
        return cls(start=s["start"]["point"], segments=segs, lines=lines, problems=s.get("problems") or [], names=names)

    @classmethod
    def from_page(cls, page, standard=None):
        """真找完的步骤（结果页数据）：顺带那条线也从起点算；有标准时接上合理范围（见 fsw/standards.py）。"""
        from fsw.standards import real_scenario
        return cls.from_scenario(real_scenario(page, standard))


def _rule(rule, t, vals, segs, ends):
    k = rule.get("type")
    if k == "any":
        return any(_rule(r, t, vals, segs, ends) for r in rule.get("rules", []))
    if k == "line_order":
        a, b = ends.get(rule["first"]), ends.get(rule["second"])
        return a is not None and b is not None and a > b
    if k == "value_not":
        return rule["point"] in vals and vals[rule["point"]] != rule["value"]
    if k == "point_before":
        return rule["a"] in t and rule["b"] in t and t[rule["a"]] < t[rule["b"]]
    if k == "segment_over":
        v = segs.get(rule["segment"])
        return v is not None and v > rule["seconds"]
    if k == "line_over":
        v = ends.get(rule["line"])
        return v is not None and v > rule["seconds"]
    return False


def compute(cur, spec):
    t = {p: us / 1_000_000 for p, (us, _) in cur.items()}
    vals = {p: v for p, (_, v) in cur.items()}
    segs = {k: (round(t[b] - t[a], 6) if a in t and b in t else None) for k, (a, b) in spec.segments.items()}
    ends = {}
    for l in spec.lines:
        parts = [(t[b] - t[a]) if a in t and b in t else None for a, b in l["segs"]]
        ends[l["name"]] = round(sum(parts), 6) if parts and None not in parts else None
    probs = [p["key"] for p in spec.problems if _rule(p.get("rule") or {}, t, vals, segs, ends)]
    return {"segs": segs, "ends": [ends[l["name"]] for l in spec.lines], "probs": probs}


STALE_US = 500_000  # 比起点早半秒以上，算这一轮开始之前的


class Collector:
    """一行行喂进来，一轮收齐就交出这一轮。"""

    def __init__(self, spec, att=0, n=0):
        self.spec, self.att, self.n = spec, att, n
        self.last = ""
        self._open()

    def _open(self):
        """开始读（或停下再接着读）的那一刻就当作一轮的开头：设备可能在码表开始读之前就已经开始这一轮了，
        没看到"开始"那一行。这一轮收齐了照样算；只收到半轮就提示再做一次（决策 101）。"""
        self.cur, self.done, self.started, self.first = {}, False, True, True
        self.occ = {}
        self.reset_us = None

    def feed(self, line):
        p = parse_line(line)
        if not p:
            return []
        self.last = line.strip()
        name, us, val = p
        out = []
        if name == "RESET":
            # 没有起点的一轮不算一轮（比如对话类喊唤醒词那一下：设备进聆听就清零，但没有"你说完"），不报没收全
            if self.first and self.cur and not self.done:
                # 开始读（或接着测重新打开调试口）时这一轮已经开始了：缺了的点可能是断开那一下丢的，算"开头没收到"，不算没收全（决策 112）
                out.append({"half": True})
            elif self.started and self.cur and not self.done and self.spec.start in self.cur:
                out.append(self._bad())
            self.started, self.cur, self.done, self.first = True, {}, False, False
            self.occ = {}
            self.reset_us = us
            return out
        if not self.started or self.done:  # 半轮、或这一轮已经收齐：后面的不管
            return out
        self.cur.setdefault(name, (us, val))
        self.occ.setdefault(name, []).append((us, val))  # 同一个点一轮里记了几次（"每次都记"的点）：算的时候挑起点之后的第一次
        if all(x in self.cur for x in self.spec.need):
            self.done = True
            out.append(self._ok_or_stale())
        return out

    def progress(self):
        got = len([x for x in self.spec.need if x in (self.cur or {})]) if not self.done else 0
        return got, len(self.spec.need)

    def finish(self):
        """停下时：正在收、还没收齐的这一轮不算（也不算"没收全"）。接着收时，从那一刻起当作新一轮的开头。"""
        self._open()
        return []

    def _ok_or_stale(self):
        """比起点还早的点不是这一轮的（决策 111）：比如上一轮剩下的一包声音在聆听时到了，"只记第一次"就记成了它，
        这一轮真正的那一次没记到。这样的轮不能算（也不能拿它判问题），写清楚是哪个点，自动补一轮。"""
        t0 = self.cur[self.spec.start][0]
        stale = []
        for x in self.spec.need:
            if x == self.spec.start:
                continue
            after = [o for o in self.occ.get(x, [self.cur[x]]) if o[0] >= t0 - STALE_US]
            if after:
                self.cur[x] = after[0]  # 决策 123：起点之后的第一次才是这一轮的（比如进聆听时先换一次表情，回答来了再换）
            else:
                stale.append(x)
        if not stale:
            return self._ok()
        self.att += 1
        return {"ok": False, "att": self.att, "stale": True,
                "missing": [self.spec.names.get(x, x) + "（记到的是这一轮开始之前的）" for x in stale]}

    def _ok(self):
        self.att += 1
        self.n += 1
        # 三个设备时刻（秒）：开始聆听、起点、最后一个计时点。码表算"说话、设备反应、放完回答"各多久用（决策 99）
        t = [getattr(self, "reset_us", None), self.cur[self.spec.start][0], max(v[0] for v in self.cur.values())]
        times = [round(x / 1e6, 3) for x in t] if None not in t else None
        return {"ok": True, "n": self.n, "att": self.att, **compute(self.cur, self.spec), "t": times}

    def _bad(self):
        self.att += 1
        miss = [self.spec.names.get(x, x) for x in self.spec.need if x not in self.cur]
        return {"ok": False, "att": self.att, "missing": miss}


def collect(lines, spec):
    c = Collector(spec)
    out = []
    for l in lines:
        out += c.feed(l)
    return [r for r in out + c.finish() if not r.get("half")]  # 整份日志一起算时，开头那半轮不用提示


def demo_lines(demo, rounds, start=0, drop_at=0):
    """演示数据回放成设备调试口的输出（没有设备时用）：每轮各计时点的时间按演示数据的每段用时排出来；
    "附加值不是某个数"这类问题，在演示数据标了出问题的那几轮把附加值写成别的数。
    drop_at=k：第 k 次只打一个计时点就开始下一轮（模拟没收全）。"""
    s, res = demo["scenario"], demo["result"]
    seg_v = res["segments"]
    n = len(next(iter(seg_v.values())))
    witness = []  # (计时点, 正常的附加值, 出问题的轮)
    for p in s.get("problems") or []:
        rules = p["rule"]["rules"] if p["rule"]["type"] == "any" else [p["rule"]]
        for r in rules:
            if r["type"] == "value_not":
                witness.append((r["point"], r["value"], set(res["problems"].get(p["key"], []))))
    att = 0
    for r in range(start, start + rounds):
        att += 1
        base = 5_000_000 + r * 7_000_000
        marks = {s["start"]["point"]: 0.0}
        for l in s["lines"]:
            tt = 0.0
            for k in l["segments"]:
                tt += seg_v[k][r % n]
                marks.setdefault(s["segments"][k]["to"], tt)
        if drop_at and att == drop_at:
            yield f"FSW,RESET,{base - 3_000_000},0"
            yield f"FSW,{s['start']['point']},{base - 2_900_000},0"
            att += 1
        yield f"FSW,RESET,{base - 100_000},0"
        for p, val, bad in witness:
            yield f"FSW,{p},{base},{val - 1 if (r % n) + 1 in bad else val}"
        for p, tt in sorted(marks.items(), key=lambda x: x[1]):
            yield f"FSW,{p},{base + round(tt * 1_000_000)},0"
