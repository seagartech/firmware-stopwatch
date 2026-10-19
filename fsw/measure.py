"""多轮实测的计算：读设备调试口的计时点行，按轮切分，算每一段、每条线用了多久，查问题。

底子是验证期的收数据小程序：
- 行格式 FSW,P<编号>,<开机后微秒>,<附加值>；FSW,RESET,<微秒>,0 是一轮开始。
- 有的计时点只在内存里记"最后一次"、到后面才打印，所以日志里的先后不等于发生的先后：一律按时间戳算。
- 同一轮同一个计时点只认第一次。开始收的时候已经在一轮中间的那半轮不算。
- 一轮在下一轮开始（RESET）或收全之后等一会儿（settle）才收尾：每次都记的点（FSW_MARK）在起点之前可能已经记过一次，
  这一轮真正那一次可能比别的点都晚到，不能"每个点到过一次"就收尾。收尾时每个点取起点之后的第一次（按时间戳）。
  起点之后每个点都到了，进度就显示"收全了"（ready），不用等收尾。下一轮开始了还没收全，就是"没收全"，不算、自动补测。
- 连起点都没有的一轮不算一轮（比如对话类喊唤醒词那一下），不报没收全。
设备输出原样存一份（码表不改日志去向），这里只读。纯计算，不用大模型。
"""

import re
import time
from dataclasses import dataclass, field

POINT = re.compile(r"FSW,(P\d+|RESET),(-?\d+),(-?\d+)")
LATE = re.compile(r"FSW,LATE,(P\d+),(-?\d+),(-?\d+)")  # 一轮开始时补打的"记最后一次"（见 fw_stopwatch.h 的 fsw_round_reset）


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
    last: set = field(default_factory=set)  # 一轮里取最后一次的点（比如回答全部放完、门完全打开）：中途可能先报一次
    soft: set = field(default_factory=set)  # 只在"看内容长短、不判快慢"那条线上用的点：一轮缺了它，其余的线照样算，不整轮补测
    marks: set = field(default_factory=set)  # 插成"记最后一次"的点（含中间点）：可能越过下一步，不拿来查下一步的先后

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
        spec = cls(start=s["start"]["point"], segments=segs, lines=lines, problems=s.get("problems") or [], names=names,
                   last=set(s.get("last_points") or []) & {l["segs"][-1][1] for l in lines if l["segs"]})  # 只认线的终点（起点等别的点"记最后一次"是插点做法，不影响收尾）
        # 有"第一下感觉到的结果"那条线时，最后一条线（到全部做完，比如回答放完）看内容长短、报告不判快慢（同 judge）：
        # 只在这条线上用的点缺了，不算没收全（真测第 38 次：回答完整放了，固件那一轮没打出"声音放完"，整轮作废让人重说）
        first = next((i for i, l in enumerate(s["lines"]) if l.get("first")), None)
        if first is not None and first != len(lines) - 1:
            other = {p for i, l in enumerate(lines) if i != len(lines) - 1 for g in l["segs"] for p in g}
            spec.soft = {p for g in lines[-1]["segs"] for p in g} - other - {spec.start}
        spec.marks = set(s.get("last_points") or [])
        opt = set(s.get("optional") or [])  # 只在某种触发方式才走的计时点：有就记，一轮收齐不等它
        if opt:
            spec.need = [p for p in spec.need if p not in opt] or spec.need
        return spec

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
        g = l["segs"]
        if ends[l["name"]] is None and g and g[0][0] in t and g[-1][1] in t and all(g[i][1] == g[i + 1][0] for i in range(len(g) - 1)):
            # 中间缺一个点（比如打断回答后那一轮"收到开始说话"不是这一轮的）：一条线首尾相接，用时就是首尾两点之差，照样算
            ends[l["name"]] = round(t[g[-1][1]] - t[g[0][0]], 6)
    probs = [p["key"] for p in spec.problems if _rule(p.get("rule") or {}, t, vals, segs, ends)]
    return {"segs": segs, "ends": [ends[l["name"]] for l in spec.lines], "probs": probs}


def suspects(rnd, scenario):
    """第 1 轮收齐后自动校验计时点位置（决策 158）：哪一段终点比起点先记到（用时为负），两个计时点的先后对不上，多半插错了位置。
    不按"经过服务器的段至少多久"判：服务器常把两条消息连着发，真实只有一两毫秒（第 4 次真机自验误停过）。"""
    out = []
    last = set(scenario.get("last_points") or []) - {(scenario.get("start") or {}).get("point")}
    for k, g in (scenario.get("segments") or {}).items():
        v = (rnd.get("segs") or {}).get(k)
        if v is None or v >= 0:
            continue
        if g.get("from") in last:  # "记最后一次"的中间点越过了下一步：不是插错，报告里把前后两段合成一段（见 judge.merge_overruns）
            continue
        out.append({"segment": k, "name": g.get("name") or k, "seconds": v, "why": "这段终点比起点先记到，两个计时点的先后对不上"})
    return out


STALE_US = 500_000  # 比起点早半秒以上，算这一轮开始之前的
LATE_US = 1_000_000  # 一轮结束标记之后这么久内到的尾巴点（比如最后一声放完）还算上一轮的
LATE_MAX = 2         # 上一轮最多差这么几个点，才等它的尾巴
LAST_QUIET = 10.0    # 有"取最后一次"的点时，下一轮一直没开始，最后一次之后再安静这么久才收尾（实测：回答两句之间服务器停了好几秒）


class Collector:
    """一行行喂进来，一轮收尾时交出这一轮：下一轮开始（RESET）时，或收全之后调用 flush 时。"""

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
        self.ready, self.ready_at = False, None
        self.prev = None  # 上一轮差几个尾巴点、在等（见 _park）
        self.last_cur = {}

    def skip_round(self):
        """正在进行的这一轮不算（比如试一次时只喊了唤醒词的那一轮）：到下一个结束标记前收到的都不管，也不报开头没收到。"""
        self.started, self.first, self.cur, self.occ = False, False, {}, {}

    def _park(self, us):
        """设备回到等待（打出结束标记）时，最后一声可能还没放完：这一轮只差最后一两个点时先不判没收全，
        结束标记之后 LATE_US 内到的这几个点算回这一轮（实测：最后一声放完比结束标记晚约 0.13 秒）。"""
        ends = {l["segs"][-1][1] for l in self.spec.lines if l["segs"]}
        miss = self._miss_after()
        if not miss or len(miss) > LATE_MAX or not set(miss) <= ends:  # 只等线的终点（最后一声）：别的点缺了照常判没收全
            return False
        self.prev = {"cur": self.cur, "occ": self.occ, "reset_us": self.reset_us, "miss": set(miss), "until": us + LATE_US,
                     "at": time.monotonic()}
        return True

    def _miss_after(self):
        """这一轮还差的点：起点之后没记到的都算（起点之前那一次是上一轮剩下的，比如唤醒回应放完的最后一声）。"""
        if self.spec.start not in self.cur:
            return [x for x in self.spec.need if x not in self.cur]
        return [x for x in self.spec.need if x != self.spec.start and not self._after(x)]

    def _bad_or_stale(self):
        """等尾巴没等到：要的点都有、只是有的只在起点之前记到过，照旧写清"记到的是这一轮开始之前的"；真缺点就判没收全。
        缺的只是"不判快慢"那条线上的点（见 Spec.soft）：这一轮照样算，那条线这一轮空着。"""
        if all(x in self.cur for x in self.spec.need):
            return self._ok_or_stale()
        miss = set(self._miss_after()) if self.spec.start in self.cur else None
        if miss and self.spec.soft and miss <= self.spec.soft:
            return self._ok_or_stale(skip=miss)
        return self._bad()

    def _before_end(self, name):
        """name 是某条线的终点时，这条线终点前一个点；不是终点返回 None。"""
        for l in self.spec.lines:
            if l["segs"] and l["segs"][-1][1] == name:
                return l["segs"][-1][0]
        return None

    def _leftover(self, name, us):
        """结束标记之后马上到的某条线的终点，上一轮（有点、但缺这个终点）还在等它，而这一轮终点前一个点还没到：
        是上一轮最后一声放完，不是这一轮的（比如唤醒回应那一轮，最后一声比结束标记晚到）。"""
        if self.reset_us is None or us > self.reset_us + LATE_US or name in self.cur:
            return False
        if not self.last_cur or name in self.last_cur:
            return False
        b = self._before_end(name)
        return b is not None and b not in self.cur

    def _prev_close(self):
        """等尾巴等到头了：收齐过的已经交出去了（见 feed 里的结束标记），不用再交；差点的判没收全。返回要交出的结果（列表）。"""
        if self.prev.get("sent"):
            self.prev = None
            return []
        return [self._prev_do(self._ok_or_stale if self.prev.get("ready") else self._bad_or_stale)]

    def _prev_do(self, fn):
        """拿上一轮的数据跑 fn（收尾、判没收全），跑完换回这一轮。"""
        mine = (self.cur, self.occ, self.reset_us)
        self.cur, self.occ, self.reset_us = self.prev["cur"], self.prev["occ"], self.prev["reset_us"]
        try:
            return fn()
        finally:
            self.cur, self.occ, self.reset_us = mine
            self.prev = None

    def _prev_late(self, name, us, val):
        """这一行是不是上一轮的尾巴：是就记回上一轮，收齐了交出上一轮。返回 (算上一轮的吗, 交出的结果)。"""
        pv = self.prev
        if us > pv["until"]:
            return False, self._prev_close()
        if pv.get("ready"):  # 已经收齐、在等"取最后一次"的终点的尾巴：结束标记之后才报出来的最后一声算回上一轮
            if name in self.spec.last and self._before_end(name) not in self.cur:
                pv["occ"].setdefault(name, []).append((us, val))
                return True, self._prev_update()
            return False, []
        if name not in pv["miss"]:
            return False, []
        if self._before_end(name) in self.cur:  # 这一轮终点前一个点已经到了：这个终点是这一轮的，上一轮等不到了
            return False, [self._prev_do(self._bad_or_stale)]
        pv["cur"].setdefault(name, (us, val))
        pv["occ"].setdefault(name, []).append((us, val))
        pv["miss"].discard(name)
        return True, ([self._prev_do(self._ok_or_stale)] if not pv["miss"] else [])

    def feed(self, line):
        p = parse_line(line)
        if not p:
            m = LATE.search(line or "")
            # 只当"取最后一次"的终点用（比如设备先回到等待、后放空声音的最后一声）；别的点（比如没走完的起点）补打出来也不算。
            if not m or m.group(1) not in self.spec.last:
                return []
            # 补打的那次晚过了它的下一步（比如听写结果回来后又发出的一小段录音）：不是这一步该算的那次，不要
            # （真测第 41 次第 1 轮：拿它顶替正常打出的那次，先后反了 0.014 秒）
            nxt = [self.cur[b][0] for a, b in self.spec.segments.values() if a == m.group(1) and b in self.cur]
            if nxt and int(m.group(2)) > min(nxt):
                return []
            p = (m.group(1), int(m.group(2)), int(m.group(3)))
        self.last = line.strip()
        name, us, val = p
        out = []
        if self.prev and name != "RESET":
            late, got = self._prev_late(name, us, val)
            out += got
            if late:
                return out
        if name != "RESET" and self._leftover(name, us):  # 上一轮的尾巴（上一轮已判过）：不记进这一轮
            return out
        if name == "RESET":
            if self.prev:  # 等尾巴时又一轮结束了：上一轮收尾（差点的算没收全）
                out += self._prev_close()
            if self.ready and not self.done and self.spec.last:
                # 收齐了，但"取最后一次"的终点（回答全部放完）可能在结束标记之后几毫秒才报出来（真测第 27 次）：
                # 这一轮先交出去（页面上格子马上变绿，不用等），再等 LATE_US，晚到的最后一声只更新这一轮的数（见 _prev_update）
                r = self._close()
                out.append(r)
                self.prev = {"cur": self.cur, "occ": self.occ, "reset_us": self.reset_us, "miss": set(), "until": us + LATE_US,
                             "at": time.monotonic(), "ready": True, "sent": r}
            elif self.ready and not self.done:  # 上一轮已经收全：下一轮开始了，收尾
                out.append(self._close())
            # 没有起点的一轮不算一轮（比如对话类喊唤醒词那一下：设备进聆听就清零，但没有"你说完"），不报没收全
            elif self.first and self.cur and not self.done:
                # 开始读（或接着测重新打开调试口）时这一轮已经开始了：缺了的点可能是断开那一下丢的，算"开头没收到"，不算没收全（决策 112）
                out.append({"half": True})
            elif self.started and self.cur and not self.done and self.spec.start in self.cur:
                # 点都记到了、但有的只有起点之前的那一次：是上一轮剩下的，写清楚是哪个点（见 _ok_or_stale）
                if not self._miss_after():
                    out.append(self._ok_or_stale())
                elif not self._park(us):  # 只差最后一声（哪怕起点之前记到过一次）：等结束标记之后的尾巴
                    out.append(self._bad_or_stale())
            elif self.started and not self.done and len([x for x in self.spec.need if x in self.cur]) * 2 >= len(self.spec.need):
                # 大部分点都到了、就是没有起点：是一次真的对话，只是设备没记到起点。不算一轮，但要报出来（决策 133）
                out.append({"nostart": True})
            self.last_cur = dict(self.cur)  # 刚结束的这一轮记到了哪些点（看晚到的尾巴是谁的）
            self.started, self.cur, self.done, self.first = True, {}, False, False
            self.occ = {}
            self.reset_us = us
            self.ready, self.ready_at = False, None
            return out
        if not self.started or self.done:  # 半轮、或这一轮已经收尾：后面的不管
            return out
        if name == self.spec.start and self.ready and self._missed_reset(us):
            return out + self._split(us, val)
        self.cur.setdefault(name, (us, val))
        self.occ.setdefault(name, []).append((us, val))  # 同一个点一轮里记了几次（"每次都记"的点）：收尾时挑起点之后的第一次
        if not self.ready and self._complete():
            self.ready, self.ready_at = True, time.monotonic()  # 收全了：进度马上显示，收尾等下一轮开始或 flush
        elif self.ready and name in self.spec.last:
            self.ready_at = time.monotonic()  # 取最后一次的点又报了一次：这一轮还没完，从这次起重新等
        return out

    def _missed_reset(self, us):
        """这一轮已经收全，又来一个起点，而且起点之后又有一大半的点：设备已经开始了下一轮，结束标记没收到
        （比如试一次收齐后到正式实测开始读之间调试口没在读，那一下丢了；10-19 真测：试一次之后说的第一句被并进第 1 轮、白说了）。"""
        if any(o[0] >= us - STALE_US for o in self.occ.get(self.spec.start, [])):
            return False  # 起点在这一轮里报了好几次（比如杂音），不是新的一轮（见 _latest_start）
        later = [x for x in self.spec.need if x != self.spec.start and any(o[0] > us for o in self.occ.get(x, []))]
        return len(later) * 2 >= len(self.spec.need) - 1

    def _split(self, us, val):
        """按这个新起点把收到的分成两轮：之前的交出这一轮，之后的（含起点）当作下一轮，等它自己收全、收尾。"""
        cut = us - STALE_US
        new = {k: [o for o in v if o[0] >= cut] for k, v in self.occ.items()}
        self.occ = {k: [o for o in v if o[0] < cut] for k, v in self.occ.items() if any(o[0] < cut for o in v)}
        self.cur = {k: (v if v[0] < cut else min(self.occ[k])) for k, v in self.cur.items() if k in self.occ}
        out = [self._close()]
        self.last_cur = dict(self.cur)
        self.started, self.done, self.first = True, False, False
        self.occ = {k: v for k, v in new.items() if v}
        self.occ.setdefault(self.spec.start, []).append((us, val))
        self.cur = {k: min(v) for k, v in self.occ.items()}
        self.reset_us = None
        self.ready, self.ready_at = False, None
        if self._complete():
            self.ready, self.ready_at = True, time.monotonic()
        return out

    def _after(self, x):
        """这一轮里计时点 x 记在起点之后（容差 STALE_US）的那几次，按时间先后。"""
        t0 = self.cur[self.spec.start][0]
        # 起点之前半秒内的也算（"记最后一次"的点，时间戳可能略早于起点）；一次一记的中间点必须在起点之后：
        # 打断回答后服务器补发的"开始说话"早于你说完 0.44 秒，算进来就成了负数段（10-19 真测：一段 -0.94 秒）
        tol = STALE_US if x in self.spec.marks or not self.spec.marks or x in self._line_ends() else 0
        got = sorted(o for o in self.occ.get(x, [self.cur[x]] if x in self.cur else []) if o[0] >= t0 - tol)
        b = self._before_end(x)
        if got and b and b != x:
            # 线的终点（比如喇叭最后一声）中途可能多打一次（播放卡了一下）：只认它前一步之后的那次，前一步还没到就当还没收到
            tb = [o[0] for o in ([self.cur[b]] if b == self.spec.start else self._after(b))] if b in self.cur else []
            got = [o for o in got if o[0] >= tb[0]] if tb else []
        return got

    def _line_ends(self):
        return {l["segs"][-1][1] for l in self.spec.lines if l["segs"]}

    def _complete(self):
        """起点到了，而且要的每个点在起点之后都记到了。"""
        return self.spec.start in self.cur and all(x == self.spec.start or self._after(x) for x in self.spec.need)

    def progress(self):
        """正在收的这一轮：收到几个 / 共要几个。已经收全、等收尾时算全到；刚收尾、下一轮还没开始时是 0。"""
        if self.done:
            return 0, len(self.spec.need)
        if self.ready:  # 有"取最后一次"的点：收尾前那一步不算到，免得中途停顿就显示这一轮记好了
            return len(self.spec.need) - (1 if self.spec.last & set(self.spec.need) else 0), len(self.spec.need)
        if self.spec.start in self.cur:  # 起点之前记的那次不算（每次都记的点）：不然还差着就显示收全了
            return len([x for x in self.spec.need if x == self.spec.start or self._after(x)]), len(self.spec.need)
        return len([x for x in self.spec.need if x in (self.cur or {})]), len(self.spec.need)

    def flush(self, settle=0.0):
        """收全了、已经等了 settle 秒（设备可能不再开始下一轮，比如最后一轮）：收尾，交出这一轮。"""
        out = []
        if self.prev and time.monotonic() - self.prev["at"] > LATE_US / 1e6 + 0.5:  # 等尾巴等过了时（设备之后没再输出）
            out += self._prev_close()
        if self.spec.last:
            settle = max(settle, LAST_QUIET)
        if self.ready and not self.done and time.monotonic() - self.ready_at >= settle:
            out.append(self._close())
        return out

    def _close(self):
        self.done = True
        return self._ok_or_stale()

    def finish(self):
        """停下时：已经收全的这一轮照算；正在收、还没收全的这一轮不算（也不算"没收全"）。
        接着收时，从那一刻起当作新一轮的开头。"""
        out = self.flush()
        if self.prev:
            out += self._prev_close()
        self._open()
        return out

    def _ok_or_stale(self, skip=()):
        """比起点还早的点不是这一轮的（决策 111）：比如上一轮剩下的一包声音在聆听时到了，"只记第一次"就记成了它，
        这一轮真正的那一次没记到。这样的轮不能算（也不能拿它判问题），写清楚是哪个点，自动补一轮。
        skip：这一轮没记到、也不要的点（不判快慢那条线上的），从这一轮里去掉，用到它的段和线这一轮空着。"""
        self._latest_start()
        stale = []
        for x in skip:
            self.cur.pop(x, None)
        for x in self.spec.need:
            if x == self.spec.start or x in skip:
                continue
            after = self._after(x)
            if after:
                # 决策 123：起点之后的第一次才是这一轮的（比如进聆听时先换一次表情，回答来了再换）；
                # 取最后一次的点（回答全部放完）中途停顿时会先报一次，取最后那次
                self.cur[x] = after[-1] if x in self.spec.last else after[0]
            else:
                stale.append(x)
        gap = self._gaps(stale)
        stale = [x for x in stale if x not in gap]
        if not stale:
            return self._ok(gap)
        self.att += 1
        return {"ok": False, "att": self.att, "stale": True,
                "missing": [self.spec.names.get(x, x) + "（记到的是这一轮开始之前的）" for x in stale]}

    def _gaps(self, stale):
        """中间的点（不是起点、不是哪条线的终点）这一轮没有真正的那一次：只是这个点用到的两段这一轮空着，整轮照样算，线的用时按首尾算。
        比如打断回答（点屏幕、插话）后，服务器还会补发一个"开始说话"，落在下一轮你说完之前，下一轮真正的那一次不再来
        （10-19 真测 5 轮因此作废；用户："打断是正常用法，不能判错"）。同一个点轮轮如此（多半插错了位置）由收点那边连着几轮点名。"""
        ends = {l["segs"][-1][1] for l in self.spec.lines if l["segs"]} - self.spec.soft
        gap = [x for x in stale if x != self.spec.start and x not in ends]
        for x in gap:
            self.cur.pop(x, None)
        return gap

    def _latest_start(self):
        """起点一轮里报了好几次（比如设备每听到一阵杂音停下就判一次"说完了"）：取紧挨着下一步之前的那一次，
        不取第一次。不然人真正说完之前几十秒的那次杂音会被当成起点（真测第 31 次：说完到开口记成 41 秒）。"""
        st = self.spec.start
        occ = sorted(self.occ.get(st, []))
        if len(occ) < 2:
            return
        nxt = {g[0][1] for g in (l["segs"] for l in self.spec.lines) if g and g[0][0] == st}  # 每条线起点之后的第一步
        ts = [o[0] for x in nxt for o in self.occ.get(x, []) if o[0] >= occ[0][0]]
        if not ts:
            return
        before = [o for o in occ if o[0] <= min(ts)]
        if before:
            self.cur[st] = before[-1]

    def _ok(self, gap=()):
        self.att += 1
        self.n += 1
        r = {"ok": True, "n": self.n, "att": self.att, **self._numbers()}
        if gap:
            r["gap"] = [self.spec.names.get(x, x) for x in gap]
        return r

    def _numbers(self):
        # 三个设备时刻（秒）：开始聆听、起点、最后一个计时点。码表算"说话、设备反应、放完回答"各多久用（决策 99）
        t = [getattr(self, "reset_us", None), self.cur[self.spec.start][0], max(v[0] for v in self.cur.values())]
        times = [round(x / 1e6, 3) for x in t] if None not in t else None
        return {**compute(self.cur, self.spec), "t": times}

    def _prev_update(self):
        """已经交出去的上一轮，最后一声晚到了：按它重算这一轮的数，交一个"更新"（同一轮，不算新的一轮、不改轮数）。"""
        sent = self.prev["sent"]
        if not sent.get("ok"):
            return []
        mine = (self.cur, self.occ, self.reset_us)
        self.cur, self.occ, self.reset_us = self.prev["cur"], self.prev["occ"], self.prev["reset_us"]
        try:
            for x in self.spec.last:
                after = self._after(x)
                if after:
                    self.cur[x] = after[-1]
            new = {**sent, **self._numbers(), "update": True}
        finally:
            self.cur, self.occ, self.reset_us = mine
        self.prev["sent"] = new
        return [new]

    def _bad(self):
        self.att += 1
        miss = [self.spec.names.get(x, x) for x in self.spec.need if x not in self.cur]
        return {"ok": False, "att": self.att, "missing": miss}


def collect(lines, spec):
    c = Collector(spec)
    out = []
    for l in lines:
        out += c.feed(l)
    return merge_updates([r for r in out + c.finish() if not r.get("half") and not r.get("nostart")])


def merge_updates(results):
    """结果里的"更新"（已经交出去的一轮，最后一声晚到后重算的数）换掉同一轮原来的那条。"""
    out = []
    for r in results:
        if r.get("update"):
            k = next((i for i in range(len(out) - 1, -1, -1) if out[i].get("ok") and out[i].get("n") == r["n"]), None)
            if k is not None:
                out[k] = {x: v for x, v in r.items() if x != "update"}
            continue
        out.append(r)
    return out  # 整份日志一起算时，开头那半轮不用提示


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
