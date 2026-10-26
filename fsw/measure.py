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
TAIL = re.compile(r"FSW,TAIL,(P\d+),(-?\d+),(-?\d+)")  # 清零之后超过 1 秒才走到的"记最后一次"：设备只说一声晚了，不算数


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
    follow: set = field(default_factory=set)  # 跟着起点记的点（起点和第一个结果之间）：只认码表取的那次起点之后的，取起点时不拿它们当"后面的一步"
    any: dict = field(default_factory=dict)  # (从, 到) → 这一段的终点取这几个点里每轮最晚的那个（人感觉到的结束和协议上的结束谁先谁后说不准）

    def __post_init__(self):
        if self.need is None:
            pts = {self.start}
            for l in self.lines:
                for a, b in l["segs"]:
                    pts.update((a, b))
            for a, b in self.segments.values():
                pts.update((a, b))
            for c in self.any.values():
                pts.update(c)
            self.need = sorted(pts, key=lambda p: int(p[1:]))

    @classmethod
    def from_scenario(cls, s):
        segs = {k: [g["from"], g["to"]] for k, g in s["segments"].items()}
        lines = [{"name": l["name"], "segs": [segs[k] for k in l["segments"]], **({"first": True} if l.get("first") else {})}
                 for l in s["lines"]]
        names = dict(s.get("point_names") or {})
        names.setdefault(s["start"]["point"], s["start"]["name"])
        for p in (s.get("per_round") or {}).get("phases") or []:
            names.setdefault(p["point"], p["name"])
        for g in s["segments"].values():
            names.setdefault(g["to"], g["name"] + "（终点）")
        spec = cls(start=s["start"]["point"], segments=segs, lines=lines, problems=s.get("problems") or [], names=names,
                   any={(g["from"], g["to"]): list(g["to_any"]) for g in s["segments"].values() if g.get("to_any")},
                   last=set(s.get("last_points") or []) & {l["segs"][-1][1] for l in lines if l["segs"]})  # 只认线的终点（起点等别的点"记最后一次"是插点做法，不影响收尾）
        # 有"第一下感觉到的结果"那条线时，最后一条线（到全部做完，比如回答放完）看内容长短、报告不判快慢（同 judge）：
        # 只在这条线上用的点缺了，不算没收全（真测第 38 次：回答完整放了，固件那一轮没打出"声音放完"，整轮作废让人重说）
        first = next((i for i, l in enumerate(s["lines"]) if l.get("first")), None)
        if first is not None and first != len(lines) - 1:
            other = {p for i, l in enumerate(lines) if i != len(lines) - 1 for g in l["segs"] for p in g}
            # 不在哪条线上、只给"同时在走的段"用的点（比如末段输出、放空，10-26 起不进线）也一样：缺了不算没收全
            spec.soft = ({p for g in lines[-1]["segs"] for p in g} | {p for g in segs.values() for p in g}) - other - {spec.start}
        spec.marks = set(s.get("last_points") or [])
        spec.follow = set(s.get("follow") or []) - {spec.start}
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


def _path(segs):
    """一条线从起点走到终点（最后一段的终点）经过的那几段。线里还有从同一个点分出去的段（比如从首段出声分出
    "扬声器连续播放回复""判定播放队列放空""回到聆听" 三段，各量各的）：它们同时在走，不能加起来，只算通到终点的那一串
    （10-25 真测：三段都加进"到回到聆听"，多算了 0.19 秒）。从终点往回接不到起点（线没接好）就照旧按全部段算。"""
    if not segs:
        return segs
    start, x, path = segs[0][0], segs[-1][1], []
    while x != start:
        prev = next((g for g in reversed(segs) if g[1] == x and g not in path), None)
        if prev is None:
            return segs
        path.insert(0, prev)
        x = prev[0]
    return path


def compute(cur, spec):
    t = {p: us / 1_000_000 for p, (us, _) in cur.items()}
    vals = {p: v for p, (_, v) in cur.items()}
    anyend = getattr(spec, "any", None) or {}

    def at(a, b):  # 终点的时刻：有几个候选的取每轮最晚的那个
        c = [t[x] for x in anyend.get((a, b), [b]) if x in t]
        return max(c) if c else None

    def span(a, b):
        e = at(a, b)
        return (e - t[a]) if a in t and e is not None else None
    segs = {k: (round(v, 6) if (v := span(a, b)) is not None else None) for k, (a, b) in spec.segments.items()}
    ends = {}
    for l in spec.lines:
        g = _path(l["segs"])
        parts = [span(a, b) for a, b in g]
        ends[l["name"]] = round(sum(parts), 6) if parts and None not in parts else None
        if ends[l["name"]] is None and g and g[0][0] in t and at(*g[-1]) is not None and all(g[i][1] == g[i + 1][0] for i in range(len(g) - 1)):
            # 中间缺一个点（比如打断回答后那一轮"收到开始说话"不是这一轮的）：一条线首尾相接，用时就是首尾两点之差，照样算
            ends[l["name"]] = round(at(*g[-1]) - t[g[0][0]], 6)
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
LAST_QUIET = 30.0    # 有"取最后一次"的点时，下一轮一直没开始，最后一次之后再安静这么久才收尾（实测：回答两句之间服务器停了好几秒；10-19 停过 10.25 秒，原来的 10 秒把放完记早了）


class Collector:
    """一行行喂进来，一轮收尾时交出这一轮：下一轮开始（RESET）时，或收全之后调用 flush 时。"""

    def __init__(self, spec, att=0, n=0):
        self.spec, self.att, self.n = spec, att, n
        self.last = ""
        self.late = []  # 晚过了 1 秒窗口才到的尾巴点（设备打的 TAIL、清零 1 秒以后才到的）：不算数，报告里点名（停下再接着测也留着）
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
        self.post = {}  # 这一轮已经收尾（等安静收的尾）、下一轮还没开始时到的点：看是不是结束标记没读到、已经是下一轮（见 _post_round）

    def skip_round(self):
        """正在进行的这一轮不算（比如试一次时只喊了唤醒词的那一轮）：到下一个结束标记前收到的都不管，也不报开头没收到。
        但这一轮的结束标记可能在试一次时就过去了（10-19 真测：唤醒后说的第一句整句被跳过、格子不变绿）：跳着的时候来了起点、
        起点之后要的点又到了一大半，就是下一轮已经开始了，从这个起点算起（和"结束标记没读到"同一个判断）。"""
        self.started, self.first, self.cur, self.occ = False, False, {}, {}
        self.skipping, self.post = True, {}

    def _park(self, us):
        """设备回到等待（打出结束标记）时，最后一声可能还没放完：这一轮只差最后一两个点时先不判没收全，
        结束标记之后 LATE_US 内到的这几个点算回这一轮（实测：最后一声放完比结束标记晚约 0.13 秒）。"""
        ends = {l["segs"][-1][1] for l in self.spec.lines if l["segs"]}
        miss = self._miss_after()
        # 只等线的终点（最后一声），或"记最后一次"的点（设备可能在清零之后才补打，见 fw_stopwatch.h 的 FSW_TAIL_MASK）：别的点缺了照常判没收全
        if not miss or len(miss) > LATE_MAX or not set(miss) <= ends | self.spec.marks:
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
        if self.prev.get("anchor"):  # 一直没等到下一轮的起点（最后一轮、或下一轮没起点）：到这里为止的尾巴都算它的
            return self._anchor_commit(self._next_start())  # 下一轮已经报了起点（没收全就停了）：只算起点之前的
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

    def _prev_late(self, name, us, val, tail=False):
        """这一行是不是上一轮的尾巴：是就记回上一轮，收齐了交出上一轮。返回 (算上一轮的吗, 交出的结果)。"""
        pv = self.prev
        if us > pv["until"]:
            return False, self._prev_close()
        if pv.get("carry") and self.spec.start in self.cur:  # 试一次带过来的那一轮：下一句已经开始，之后放的声音都是下一句的
            return False, self._prev_close()
        if pv.get("ready"):  # 已经收齐、在等"取最后一次"的终点的尾巴：结束标记之后才报出来的最后一声算回上一轮
            # 线的终点、或"记最后一次"的点（比如每句放完都报一次的"全部放完"，不一定是线的终点）
            if (name in self.spec.last or tail or (name in self.spec.marks and name != self.spec.start and name not in self.spec.follow)) \
                    and self._before_end(name) not in self.cur:
                pv["occ"].setdefault(name, []).append((us, val))
                return True, self._prev_update()
            return False, []
        if name not in pv["miss"]:
            if (name in self.spec.last or tail or (name in self.spec.marks and name != self.spec.start and name not in self.spec.follow)) \
                    and not self._begun_with(self._before_end(name)):
                # 上一轮还差别的点、在等：清零后才到的"记最后一次"的点（最后一段声音）也是上一轮的，记上（8784：原来丢了，全程按结束消息算短了）
                pv["occ"].setdefault(name, []).append((us, val))
                return True, []
            return False, []
        if self._begun_with(self._before_end(name)):  # 这一轮终点前一个点已经到了：这个终点是这一轮的，上一轮等不到了
            return False, [self._prev_do(self._bad_or_stale)]
        pv["cur"].setdefault(name, (us, val))
        pv["occ"].setdefault(name, []).append((us, val))
        pv["miss"].discard(name)
        return True, ([self._prev_do(self._ok_or_stale)] if not pv["miss"] else [])

    def _begun_with(self, b):
        """这一轮（清零之后）真的开始了、而且点 b 是开始之后才到的：b 在这一轮里、这一轮有起点、b 不早于起点。
        清零后、下一轮起点之前被上一轮剩下的声音重新触发的"第一次"点（比如回复还没放完就清零，第一段出声又报了一次）
        不算这一轮开始的证据（8784：拿它当证据，上一轮真正的放完被判给了下一轮、又因为早于起点被扔掉，全程少算 0.1～0.5 秒）。"""
        if b is None or b not in self.cur:
            return False
        st = self.cur.get(self.spec.start)
        if st is not None:
            return self.cur[b][0] >= st[0]
        # 起点还没打出来（起点是补打的，比设备上后面几步晚出现在日志里）：这一轮要的点已经到了一大半，才算真的开始了
        got = [x for x in self.spec.need if x != self.spec.start and x in self.cur]
        return len(got) * 2 >= len(self.spec.need) - 1

    def _flag_late(self, name, us, how):
        if len(self.late) < 200:
            self.late.append({"p": name, "name": self.spec.names.get(name, name), "us": us, "how": how})

    def feed(self, line):
        out = self._feed(line)
        emit, self._emit = getattr(self, "_emit", []), []
        return emit + out

    def _feed(self, line):
        p = parse_line(line)
        if not p and (tm := TAIL.search(line or "")):
            if self.prev and self.prev.get("anchor"):  # 上一轮还开着：晚到的这一次照样是它的（到下一轮起点为止都算），不点名
                self._tent(tm.group(1), int(tm.group(2)), 0)
            elif tm.group(1) not in self.spec.follow:
                # 跟着起点记的点（比如一直在发的上传）清零后又走：是下一轮的事，不点名（10-26 8784：数据检查里多了一条无关的"晚到"）
                self._flag_late(tm.group(1), int(tm.group(2)), "tail")
            return []
        if not p:
            m = LATE.search(line or "")
            # 当"取最后一次"的终点用（比如设备先回到等待、后放空声音的最后一声）。起点和别的要的点补打出来：
            # 这一轮后面的点大半都到了才算（10-19 真测：回答被打断、设备回到聆听时才补打出"你说完"，整句当成没记到起点）；
            # 没走完的起点（后面没有点）补打出来照旧不算
            pv = self.prev
            if m and pv and pv.get("anchor"):  # 上一轮还开着（等下一轮的起点）：补打的这次先记给上一轮，等起点定了按时间分
                self._tent(m.group(1), int(m.group(2)), int(m.group(3)))
            # 上一轮清零之后 LATE_US 内补打的"记最后一次"（设备上可能晚到的点，见 fw_stopwatch.h 的 FSW_TAIL_MASK）：
            # 是上一轮这个点更晚的那一次，交给上一轮（见 _prev_late：已收齐的更新最后那次，差这个终点的补上）；
            # 上一轮不要就不要，不记进这一轮（它比这一轮的起点早）。晚过 LATE_US 的照旧不算（试一次会点名）
            tail = bool(m and pv and not pv.get("anchor") and m.group(1) in self.spec.marks and m.group(1) not in self.spec.last
                        and pv["until"] - LATE_US <= int(m.group(2)) <= pv["until"])
            if tail:
                late, got = self._prev_late(m.group(1), int(m.group(2)), int(m.group(3)), tail=True)
                return got
            if m and m.group(1) not in self.spec.last:
                ln, lus = m.group(1), int(m.group(2))
                # 这一轮已经有它、但只有起点或前一步之前的那次（比如唤醒提示音的最后一段）：补打的这次才是这一轮的
                fresh = ln not in self.cur or (ln != self.spec.start and self.spec.start in self.cur and not self._after(ln))
                ok = (ln in self.spec.need and fresh and self.started and not self.done
                      and (self.spec.start in self.cur or (ln == self.spec.start and self._later_half(lus, self.occ))))
                if not ok:
                    return []
                m = None
                p = (ln, lus, int(LATE.search(line).group(3)))
            elif not m:
                return []
            # 补打的那次晚过了它的下一步（比如听写结果回来后又发出的一小段录音）：不是这一步该算的那次，不要
            # （真测第 41 次第 1 轮：拿它顶替正常打出的那次，先后反了 0.014 秒）
            if m:
                nxt = [self.cur[b][0] for a, b in self.spec.segments.values() if a == m.group(1) and b in self.cur]
                if nxt and int(m.group(2)) > min(nxt):
                    return []
                p = (m.group(1), int(m.group(2)), int(m.group(3)))
        self.last = line.strip()
        name, us, val = p
        out = []
        anchor = bool(self.prev and self.prev.get("anchor"))
        if anchor and name != "RESET":
            self._tent(name, us, val)  # 起点定下来之前到的点：先记给上一轮（尾巴：最后一声、真正放完、回到聆听），也照常记进这一轮
        elif self.prev and name != "RESET":
            late, got = self._prev_late(name, us, val)
            out += got
            if late:
                return out
        if name != "RESET" and not anchor and self._leftover(name, us):  # 上一轮的尾巴（上一轮已判过）：不记进这一轮
            return out
        if (name != "RESET" and name in self.spec.marks and name != self.spec.start and name not in self.spec.follow
                and self.reset_us is not None and us > self.reset_us + LATE_US and self.spec.start not in self.cur
                and self.last_cur and name in self.last_cur and not self.done and not anchor):
            # 记最后一次的点，清零 1 秒以后、这一轮起点之前才到：是上一轮更晚的一下（比如最后一声放完晚了），已经不算回上一轮了
            self._flag_late(name, us, "after_window")
        if name == "RESET":
            if self.prev:  # 等尾巴时又一轮结束了：上一轮收尾（差点的算没收全；等起点的，到这里为止的尾巴都算它的）
                out += self._prev_close()
            if self.ready and not self.done:
                # 收齐了：这一轮先交出去（页面上格子马上变绿，不用等）。一轮的边界按起点算，不按清零（10-26 真测）：
                # 清零（回到等待）之后还会到这一轮的点——最后几段声音、真正放完、回到聆听——一直算这一轮的，
                # 直到下一轮的起点定下来（起点之后第一个结果到了，起点取它之前的最后一次）；晚到的只更新这一轮的数（见 _anchor_commit）
                r = self._close()
                out.append(r)
                self.prev = {"cur": self.cur, "occ": self.occ, "reset_us": self.reset_us, "miss": set(), "until": float("inf"),
                             "at": time.monotonic(), "ready": True, "sent": r, "anchor": True, "tent": []}
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
            self.post = {}
            self.started, self.cur, self.done, self.first = True, {}, False, False
            self.skipping = False
            self.occ = {}
            self.reset_us = us
            self.ready, self.ready_at = False, None
            return out
        if self.started and self.done:  # 这一轮已经收尾（等安静收的尾）：后面的先记着，看是不是下一轮的结束标记没读到
            self.post.setdefault(name, []).append((us, val))
            if name == self.spec.start and self._later_half(us, self.post):
                self._begin({k: [o for o in v if o[0] >= us - STALE_US] for k, v in self.post.items()})
            return out
        if not self.started:  # 半轮：后面的不管
            if getattr(self, "skipping", False):  # 跳着唤醒那一轮：看下一轮是不是已经开始了（见 skip_round）
                self.post.setdefault(name, []).append((us, val))
                s = max((o[0] for o in self.post.get(self.spec.start, [])), default=None)
                if s is not None and self._later_half(s, self.post):
                    self.skipping = False
                    self._begin({k: [o for o in v if o[0] >= s - STALE_US] for k, v in self.post.items()})
            return out
        if name == self.spec.start and self.ready and self._missed_reset(us):
            return out + self._split(us, val)
        s = self._new_anchor(name, us)
        if s is not None:  # 这一轮收全后又一次结果、前面有新的起点：结束标记没读到，按起点分开（起点之前的尾巴还是这一轮的）
            out += self._split_at(s)
        self.cur.setdefault(name, (us, val))
        self.occ.setdefault(name, []).append((us, val))  # 同一个点一轮里记了几次（"每次都记"的点）：收尾时挑起点之后的第一次
        if anchor and self._confirms(name, us):  # 这一轮的起点定下来了：起点之前到的尾巴交给上一轮
            out += self._anchor_commit(max(o[0] for o in self.occ[self.spec.start] if o[0] <= us))
        if not self.ready and self._complete():
            self.ready, self.ready_at = True, time.monotonic()  # 收全了：进度马上显示，收尾等下一轮开始或 flush
        elif self.ready and name in self.spec.last:
            self.ready_at = time.monotonic()  # 取最后一次的点又报了一次：这一轮还没完，从这次起重新等
        return out

    def _tent(self, name, us, val):
        self.prev["at"] = time.monotonic()
        if name != self.spec.start and name not in self.spec.follow and (name, us, val) not in self.prev["tent"][-8:]:
            self.prev["tent"].append((name, us, val))
            if self._next_start() is None:  # 这一轮还没报起点：一定在下一轮起点之前，马上算回上一轮（格子、数马上更新），上一轮接着开着
                self._emit = getattr(self, "_emit", []) + self._anchor_apply(self.prev, [self.prev["tent"].pop()])

    def tail_quiet(self, sec=LATE_US / 1e6 + 0.5):
        """上一轮还开着、在收清零之后的尾巴：已经 sec 秒没有新的点了（测够了轮数时用：最后一轮不再等下一轮的起点）。"""
        return bool(self.prev and self.prev.get("anchor") and (time.monotonic() - self.prev["at"] >= sec or self._next_start() is not None))

    def _next_start(self):
        """上一轮还开着时，这一轮已经报过的第一次起点（之后到的点多半是这一轮的了）。"""
        occ = self.occ.get(self.spec.start) if self.prev and self.prev.get("anchor") else None
        return min(o[0] for o in occ) if occ else None

    def _confirm_pts(self):
        """定下一轮起点的点：第一下感觉到的结果那条线上（没有就第一条线上）不是起点、不跟着起点记、不是"记最后一次"的点。"""
        line = next((l["segs"] for l in self.spec.lines if l.get("first") and l["segs"]), None) or \
            next((l["segs"] for l in self.spec.lines if l["segs"]), [])
        pts = {b for _, b in line} - self.spec.follow - self.spec.marks - self.spec.last - {self.spec.start}
        return pts or set(self.spec.need) - self.spec.follow - self.spec.marks - self.spec.last - {self.spec.start}

    def _confirms(self, name, us):
        return (name in self._confirm_pts() and self.spec.start in self.occ
                and any(o[0] <= us for o in self.occ[self.spec.start]))

    def _anchor_commit(self, anchor_us):
        """上一轮的尾巴：清零之后、这一轮起点（anchor_us；None = 到数据结束）之前到的点，算回上一轮。
        "记最后一次"的点（最后一段声音、全部放完）取最后那次；上一轮没有的点（比如回到聆听）补上；上一轮已有的只记一次的点不动
        （清零后又报的"第一段声音"是上一轮剩下的声音，不是新的一轮）。返回更新（同一轮，不算新的一轮）。"""
        pv, self.prev = self.prev, None
        if not pv:
            return []
        return self._anchor_apply(pv, [t for t in pv.get("tent") or [] if anchor_us is None or t[1] < anchor_us])

    def _anchor_apply(self, pv, tent):
        """把这几个尾巴点算回上一轮 pv，有变化就交一个更新（pv 仍开着时也可以调）。"""
        t0 = pv["cur"][self.spec.start][0] if self.spec.start in pv["cur"] else None
        known = set(self.spec.need) | {p for g in self.spec.segments.values() for p in g}
        add = []
        for name, us, val in tent:
            if (t0 is not None and us <= t0) or name not in known:
                continue
            if name in self.spec.last or name in self.spec.marks:
                pv["occ"].setdefault(name, []).append((us, val))
                add.append(name)
            elif name not in pv["cur"] or (t0 is not None and pv["cur"][name][0] < t0):
                pv["cur"][name] = (us, val)
                pv["occ"].setdefault(name, []).append((us, val))
                add.append(name)
        if not add or not (pv.get("sent") or {}).get("ok"):
            return []
        keep, self.prev = self.prev, pv
        try:
            return self._prev_update(extra=set(add))
        finally:
            self.prev = keep

    def _new_anchor(self, name, us):
        """收全了的一轮里，定起点的点（第一下的结果）又来了一次，而且这一轮自己的那次之后有新的起点：返回新的起点（它之前的最后一次）。"""
        if not self.ready or self.done or name not in self.cur or name not in self._confirm_pts():
            return None
        mine = self.cur[name][0]
        s = [o[0] for o in self.occ.get(self.spec.start, []) if mine < o[0] <= us]
        if not s:
            return None
        # 起点和这个结果之间的那几步（跟着起点的、上传……）在新起点之后也都又走了一遍，才是新的一轮；
        # 只是起点、结果各多报一次（中间没有别的步）不算（同一轮重复的只认第一次）
        line = next((l["segs"] for l in self.spec.lines if l.get("first") and l["segs"]), None) or \
            next((l["segs"] for l in self.spec.lines if l["segs"]), [])
        mid = {b for _, b in line} - {self.spec.start, name} - self.spec.marks - self.spec.last  # "记最后一次"的常在结果之后才补打，不等它
        a = max(s)
        if not mid or not all(any(a < o[0] <= us for o in self.occ.get(x, [])) for x in mid):
            return None
        return a

    def _split_at(self, cut):
        """按起点 cut 分成两轮：cut 之前的（含上一轮清零前后的尾巴）交出这一轮，cut 起（含起点）当作下一轮。"""
        new = {k: [o for o in v if o[0] >= cut] for k, v in self.occ.items()}
        self.occ = {k: [o for o in v if o[0] < cut] for k, v in self.occ.items() if any(o[0] < cut for o in v)}
        self.cur = {k: (v if v[0] < cut else min(self.occ[k])) for k, v in self.cur.items() if k in self.occ}
        out = [self._close()]
        self._begin(new)
        return out

    def _missed_reset(self, us):
        """这一轮已经收全，又来一个起点，而且起点之后又有一大半的点：设备已经开始了下一轮，结束标记没收到
        （比如试一次收齐后到正式实测开始读之间调试口没在读，那一下丢了；10-19 真测：试一次之后说的第一句被并进第 1 轮、白说了）。"""
        if any(o[0] >= us - STALE_US for o in self.occ.get(self.spec.start, [])):
            return False  # 起点在这一轮里报了好几次（比如杂音），不是新的一轮（见 _latest_start）
        return self._later_half(us, self.occ)

    def _later_half(self, us, occ):
        """起点之后（时间戳在 us 之后）要的点到了一大半：是一次真的、走完了的事，不是没走完的起点或杂音。"""
        later = [x for x in self.spec.need if x != self.spec.start and any(o[0] > us for o in occ.get(x, []))]
        return len(later) * 2 >= len(self.spec.need) - 1

    def _split(self, us, val):
        """按这个新起点把收到的分成两轮：之前的交出这一轮，之后的（含起点）当作下一轮，等它自己收全、收尾。"""
        cut = us - STALE_US
        new = {k: [o for o in v if o[0] >= cut] for k, v in self.occ.items()}
        self.occ = {k: [o for o in v if o[0] < cut] for k, v in self.occ.items() if any(o[0] < cut for o in v)}
        self.cur = {k: (v if v[0] < cut else min(self.occ[k])) for k, v in self.cur.items() if k in self.occ}
        out = [self._close()]
        new.setdefault(self.spec.start, []).append((us, val))
        self._begin(new)
        return out

    def _begin(self, occ):
        """结束标记没读到的下一轮：从这些点开始（起点已在里面），等它自己收全、收尾。"""
        self.last_cur = dict(self.cur)
        self.started, self.done, self.first = True, False, False
        self.occ = {k: sorted(v) for k, v in occ.items() if v}
        self.cur = {k: v[0] for k, v in self.occ.items()}
        self.post = {}
        self.reset_us = None
        self.ready, self.ready_at = False, None
        if self._complete():
            self.ready, self.ready_at = True, time.monotonic()

    def _after(self, x, _seen=None):
        """这一轮里计时点 x 记在起点之后的那几次，按时间先后。
        "记最后一次"的点和线的终点：只认它这一段起头那一步之后的（10-25 真测：唤醒提示音的"最后一段出声"比这一轮的起点早
        0.29 秒，原来"记最后一次"的点放宽到起点前半秒，被当成这一轮回复的最后一段，整段播放算成 -5.72 秒）。
        起头那一步这一轮没有（中途打断、只记了起点之前那次）就往前找，直到起点。
        一次一记的中间点不卡先后（服务器两条消息先后会反，比如第一包声音比"开始说话"早几毫秒到，是要查的问题，不是插错）。"""
        t0 = self.cur[self.spec.start][0]
        ends = self._line_ends()
        # 老的场景（没标"记最后一次"）照旧：起点之前半秒内的也算；一次一记的中间点必须在起点之后
        # （打断回答后服务器补发的"开始说话"早于你说完 0.44 秒，算进来就成了负数段，10-19 真测）
        tol = 0 if x in self.spec.marks or x in self.spec.follow else STALE_US if not self.spec.marks or x in ends else 0
        got = sorted(o for o in self.occ.get(x, [self.cur[x]] if x in self.cur else []) if o[0] >= t0 - tol)
        if not got or x == self.spec.start or not (x in self.spec.marks or x in ends):
            return got
        seen = (_seen or set()) | {x}
        tb = None
        for a in self._froms(x):
            t = self._from_time(a, seen, x in ends)
            if t is None:
                return []
            tb = t if tb is None else max(tb, t)
        return [o for o in got if tb is None or o[0] >= tb]

    def _froms(self, x):
        return [g[0] for g in self.spec.segments.values() if g[1] == x and g[0] != x]

    def _from_time(self, a, seen, strict=False):
        """x 那一段起头的那一步 a 这一轮记到的时刻（起点之后的第一次）。a 是"记最后一次"的点时（它和下一步谁先谁后不一定，
        10-21 真测：一半的轮先放完、50 毫秒后才收到合成结束），不拿它卡，往前找只记一次的那一步；这一轮没有 a 也往前找。
        往前一直找到起点。strict（线的终点，比如喇叭最后一声中途多打一次）：前一步这一轮还一次都没到就返回 None，当这个终点还没收到；
        前一步只有起点之前那次（比如唤醒词说完那一下就发出的第一包），往前找。"""
        while a and a != self.spec.start and a not in seen:
            seen = seen | {a}
            if a not in self.spec.marks:
                if a not in self.cur:
                    if strict and a in self.spec.need:  # 可选的前一步（只在某种情况才走）这一轮没走：往前找
                        return None
                elif got := self._after(a, seen):
                    return got[0][0]
            prev = self._froms(a)
            a = prev[0] if prev else self.spec.start
        return self.cur[self.spec.start][0]

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

    def have(self):
        """正在收的这一轮已经到了哪几个点（页面步骤条按这个涂绿：有的点设备要到一轮结束才打出来，不能按个数从左往右涂）。
        取最后一次的点（比如声音放完）一轮里会报好几次（每句放完报一次），报过不等于做完了：这一轮收尾前不涂绿。"""
        return [x for x in self._have() if x not in self.spec.last]

    def _have(self):
        if self.done:
            return []
        if self.spec.start in self.cur:
            # 起点要等它后面的一步到了才涂绿：设备回到聆听时常常先多报一次起点（10-21 真测：每轮结束 0.2 秒后报一次"检测到说话结束"），
            # 人还没开口第一步就绿了
            got = [x for x in self.spec.need if x != self.spec.start and self._after(x)]
            return ([self.spec.start] if got else []) + got
        # 起点还没打出来（设备一轮结束才打）：按先后看，上一步没到的不算到（打断回答后服务器补发的"开始说话"，你还没说话就先涂绿了）；
        # 上一步到过、但比它的上一步还早的（就是那个补发的）跳过去，往前接着比
        shown = {}

        def ok(x):
            if x in shown:
                return shown[x]
            shown[x] = False
            if x not in self.cur:
                return False
            t = None
            for a, b in self.spec.segments.values():
                if b != x:
                    continue
                while a is not None and a != self.spec.start and a not in self.spec.marks:
                    if a not in self.cur:
                        return False
                    if ok(a):
                        t = self.cur[a][0]
                        break
                    a = next((g[0] for g in self.spec.segments.values() if g[1] == a), None)
            shown[x] = t is None or any(o[0] >= t for o in self.occ.get(x, []))
            return shown[x]
        return [x for x in self.spec.need if ok(x)]

    def close_carried(self, ended=False):
        """试一次带过来的那一轮已经收全：马上交出（格子马上变绿），不等下一句开始才知道它结束了
        （10-19 用户："说完今天星期几格子不变色，说完恐龙一下子绿两个"）。这一轮的结束标记在换调试口时过去了，读不到，
        所以当它已经结束：后面的点算下一轮；它回答还没放完、晚到的"最后一声"照样算回它（只更新数，见 _prev_update），
        直到下一个结束标记。
        只差"不判快慢"那条线的最后一声（比如回答放完）也马上交出，那条线先空着：试一次收齐就停，换调试口时这一声常常丢了，
        等它的话下一句会被并进这一轮（10-21 真测："一加一等于几"说完第一个格子不变绿，第 1 轮放完记成 9.7 秒）。
        它晚到了照样算回这一轮。"""
        miss = set(self._miss_after()) if not self.ready and not self.done and self.spec.start in self.cur else set()
        # ended：这一轮在试一次里已经结束（读到过结束标记），缺的只是"不判快慢"那条线上的点，后面也不会再来了：照样交出，那几段空着
        if miss and (miss <= self.spec.last & self.spec.soft or (ended and miss <= self.spec.soft)):
            self.done = True
            r = self._ok_or_stale()
        elif not (self.ready and not self.done and (self.spec.last or ended)):  # ended：这一轮在试一次里已经结束（读到过结束标记）
            return []
        else:
            r = self._close()
        self.prev = {"cur": self.cur, "occ": self.occ, "reset_us": self.reset_us, "miss": set(), "until": float("inf"),
                     "at": time.monotonic(), "ready": True, "sent": r, "carry": True}
        self.last_cur, self.post = dict(self.cur), {}
        self.started, self.cur, self.done, self.first = True, {}, False, False
        self.occ, self.reset_us = {}, None
        self.ready, self.ready_at = False, None
        return [r]

    def flush(self, settle=0.0):
        """收全了、已经等了 settle 秒（设备可能不再开始下一轮，比如最后一轮）：收尾，交出这一轮。"""
        out = []
        if self.prev and not self.prev.get("carry") and not self.prev.get("anchor") and time.monotonic() - self.prev["at"] > LATE_US / 1e6 + 0.5:  # 等尾巴等过了时（设备之后没再输出）
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
                # 记最后一次的点（marks，比如末段输出、全部放完）：回复分几句播时一轮里会报好几次（每句放完一次），取这一轮最后那次，量的是整段回复
                self.cur[x] = after[-1] if x in self.spec.last or x in self.spec.marks else after[0]
            else:
                stale.append(x)
        self._drop_before_start()
        gap = self._gaps(stale)
        stale = [x for x in stale if x not in gap]
        if not stale:
            return self._ok(gap)
        self.att += 1
        return {"ok": False, "att": self.att, "stale": True,
                "missing": [self.spec.names.get(x, x) + "（记到的是这一轮开始之前的）" for x in stale]}

    def _drop_before_start(self):
        """不是一轮必须收齐的点（可选的、顺带那条线的）：只认起点之后的那次；只有起点之前的（上一轮剩下的，比如上一轮的回到聆听）
        不算这一轮的，用到它的段这一轮空着（10-26 真测：上一轮的"回到聆听"记进下一轮，这条线成了 -1.86 秒）。"""
        if self.spec.start not in self.cur:
            return
        for x in [x for x in self.cur if x != self.spec.start and x not in self.spec.need]:
            after = self._after(x)
            if after:
                self.cur[x] = after[-1] if x in self.spec.last or x in self.spec.marks else after[0]
            else:
                self.cur.pop(x)

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
        """起点一轮里报了好几次（设备每听到一阵杂音、或者唤醒词说完、说到一半停一下，都判一次"说完了"）：
        取第一下感觉到的结果（比如收到识别文字）之前的最后一次，不取第一次。不然人真正说完之前的那次会被当成起点
        （真测第 31 次：说完到开口记成 41 秒；10-25 真测：唤醒词说完那一下当了起点，早了 4 秒多）。
        没有"第一下感觉到的结果"那条线时，取紧挨着起点下一步之前的那一次。"""
        st = self.spec.start
        occ = sorted(self.occ.get(st, []))
        if len(occ) < 2:
            return
        felt = [l["segs"][-1][1] for l in self.spec.lines if l.get("first") and l["segs"]]
        nxt = set(felt) or {g[0][1] for g in (l["segs"] for l in self.spec.lines) if g and g[0][0] == st}  # 每条线起点之后的第一步
        if self.spec.follow:
            # 有跟着起点记的点（每报一次起点就重新记一次）：取第一个结果（不跟着起点的第一步，比如收到识别结果）之前的最后一次，
            # 跟着起点的那几步（静音回调、上传首包）量的就是这次起点之后的那一下（10-26 真测：取晚了、早了都会让这几段空着）
            line = next((l["segs"] for l in self.spec.lines if l.get("first") and l["segs"]), None) or \
                next((l["segs"] for l in self.spec.lines if l["segs"] and l["segs"][0][0] == st), [])
            pts = {b for _, b in line} - self.spec.follow - self.spec.marks - {st}
            nxt = pts or nxt
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

    def _prev_update(self, extra=()):
        """已经交出去的上一轮，最后一声晚到了：按它重算这一轮的数，交一个"更新"（同一轮，不算新的一轮、不改轮数）。"""
        sent = self.prev["sent"]
        if not sent.get("ok"):
            return []
        mine = (self.cur, self.occ, self.reset_us)
        self.cur, self.occ, self.reset_us = self.prev["cur"], self.prev["occ"], self.prev["reset_us"]
        try:
            for x in self.spec.last | self.spec.marks:  # 记最后一次的点（含清零后补打的尾巴）都取这一轮最后那次
                after = self._after(x)
                if after:
                    self.cur[x] = after[-1]
            self._drop_before_start()
            new = {**sent, **self._numbers(), "update": True}
            gap = [g for g in sent.get("gap") or [] if not any(self.spec.names.get(x, x) == g and x in self.cur for x in self.spec.last)]
            if gap != (sent.get("gap") or []):  # 空着的最后一声晚到了：那条线这一轮不再空着
                new.pop("gap", None)
                new.update({"gap": gap} if gap else {})
        finally:
            self.cur, self.occ, self.reset_us = mine
        self.prev["sent"] = new
        return [new]

    def _bad(self):
        self.att += 1
        miss = [self.spec.names.get(x, x) for x in self.spec.need if x not in self.cur]
        return {"ok": False, "att": self.att, "missing": miss}


ROUNDS_FILE = "rounds.json"
ROUND_RULE = 3  # 分轮规则的版本：2 = 按起点分轮、清零后的尾巴算回上一轮、全程量到人感觉到的结束（10-26）；3 = 清零后被剩下的声音重新触发的"第一次"点不算新一轮开始（8784）。存的是老规则的就按原始日志重算


def save_rounds(run_dir, rounds):
    """测完存下这次收全的每一轮（各段、各条线的用时），放在原始日志旁边。
    以后"和上次比"、首页"以前测过的"都读它，和测的时候是同一组数；不再重读原始日志重算
    （重算时认不出试一次带过来的那一轮，10 轮算成 9 轮，10-19）。"""
    import json
    from pathlib import Path
    keep = [{"n": r.get("n"), "segs": r.get("segs") or {}, "ends": r.get("ends") or []} for r in rounds if r.get("ok")]
    (Path(run_dir) / ROUNDS_FILE).write_text(json.dumps({"rounds": keep, "rule": ROUND_RULE}, ensure_ascii=False), encoding="utf-8")


def run_rounds(run_dir, spec):
    """一次测量收全的每一轮：测完时存下的（save_rounds）；老数据没存，才按原始日志重算。"""
    import json
    from pathlib import Path
    p = Path(run_dir) / ROUNDS_FILE
    saved = None
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            saved = [{**r, "ok": True} for r in d["rounds"]]
            if d.get("rule") == ROUND_RULE:
                return saved
        except (ValueError, KeyError, TypeError, AttributeError):
            saved = None  # 文件坏了：退回重算
    lines = (Path(run_dir) / "raw.log").read_text(encoding="utf-8", errors="replace").splitlines()
    got = [r for r in collect(lines, spec) if r["ok"]]
    # 老规则存的：按现在的规则重算（10-26：老规则把清零后的尾巴算进下一轮，出了负数）；重算少了轮（认不出试一次带过来的那一轮）
    # 而老的那份和现在的线对得上，才还用老的
    if saved and len(got) < len(saved) and all(len(r.get("ends") or []) == len(spec.lines) for r in saved):
        return saved
    return got


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
