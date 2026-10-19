"""合理范围清单：每个被测项目一份，存 ~/firmware-stopwatch-data/项目名/合理范围清单.md（减重版，2026-10-17）。

两张表，人能直接读、直接改：
- 分段：编号、分段、起止计时点、合理范围、慢阈值、备注；
- 测量线：测量线、合理范围、慢阈值。
体验标准（用户等不等得了）不在清单里：只从标准库按设备类别挑，出处是公开资料，人不手填（2026-10-19 用户定）。

第一次测满 10 轮，码表按实测自动填（范围 = 最快×0.9～最慢×1.1，慢阈值 = 最慢×1.4）；
之后只给新出现的、或空着的段补上，已经有数的不动。报告每次都按这份表判。
文件写坏了报"第几行哪里不对"，不悄悄忽略。

段和线怎么对上（2026-10-19）：重新找步骤后编号会重排（上次的 P7-P8 这次可能是另一步），
所以文件末尾记着各计时点在代码里的位置，按位置对；对不上的当新段、新线，这次不判，测满 10 轮重新填。
老清单没记位置：编号和名字都一样才算同一段，线按名字对。
"""

import json
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

FILENAME = "合理范围清单.md"
MIN_ROUNDS = 10  # 测满这么多轮才拿来自动填清单：跑流程的几轮不算数
NONE = "—"
SEG_HEAD = ["编号", "分段", "起止计时点", "合理范围（秒）", "慢阈值（秒）", "备注"]
LINE_HEAD = ["测量线", "合理范围（秒）", "慢阈值（秒）"]
OLD_LINE_HEAD = LINE_HEAD + ["体验标准：超过多少秒算差", "出处"]  # 老清单多这两列：读的时候不管，下次写回去就没了


class ListFormatError(ValueError):
    """清单文件写坏了：errors 是 [(行号, 哪里不对)]。"""

    def __init__(self, errors, path=None):
        self.errors = errors
        super().__init__(f"「{path or FILENAME}」格式不对：" + "；".join(f"第 {n} 行：{m}" if n else m for n, m in errors))


class ListRuleError(ValueError):
    """改的内容不对（范围写反、慢阈值比上限低）。"""


# ---------- 数字 ----------

def _num_txt(x):
    if x is None:
        return NONE
    return str(x) if isinstance(x, int) else repr(float(x))


def _range_txt(r):
    return NONE if r is None else f"{_num_txt(r[0])}～{'不限' if r[1] is None else _num_txt(r[1])}"


def _num(s):
    if not re.fullmatch(r"-?\d+(\.\d+)?", s.strip()):
        raise ValueError
    return float(s) if "." in s else int(s)


def _rng(s):
    if s in (NONE, ""):
        return None
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*[～~]\s*(-?[\d.]+|不限)\s*", s)
    if not m:
        raise ValueError
    return [_num(m.group(1)), None if m.group(2) == "不限" else _num(m.group(2))]


def check(rng, slow, what):
    """范围、慢阈值说得通吗；不通就说清楚哪里不对（编辑时和读文件时都用）。"""
    if rng is not None:
        if rng[0] is None or rng[0] < 0 or (rng[1] is not None and rng[0] > rng[1]):
            return f"{what}的合理范围写反了或是负数"
        if slow is not None and rng[1] is not None and slow < rng[1]:
            return f"{what}的慢阈值 {_num_txt(slow)} 秒比合理范围上限 {_num_txt(rng[1])} 秒还低"
    elif slow is not None:
        return f"{what}有慢阈值却没有合理范围"
    return None


# ---------- 读写 ----------

def _cell(s):
    return str(s if s not in (None, "") else NONE).replace("\n", " ").replace("|", "\\|")


def _table(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"] + \
        ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]


META = "<!-- 码表用，不用改："  # 文件末尾这一行记计时点位置，人不用看


def dumps(lst):
    L = [f"# 合理范围清单：{lst.get('project') or '（未命名项目）'}", "",
         "> 码表按下面两张表判快慢（正常 / 偏慢 / 超过慢阈值），可以直接改表里的数；不要改表头。",
         "> 合理范围写「最少～最多」（单位秒，上限没有写「不限」），空着写「—」。",
         "> 第一次测满 10 轮时码表按实测自动填写：范围 = 最快×0.9～最慢×1.1，慢阈值 = 最慢×1.4。", "",
         "## 分段", ""]
    L += _table(SEG_HEAD, [[k, g["name"], f"{g['from']}→{g['to']}", _range_txt(g.get("range")), _num_txt(g.get("slow")), g.get("note")]
                           for k, g in lst["segments"].items()]) + ["", "## 测量线", ""]
    L += _table(LINE_HEAD, [[l["name"], _range_txt(l.get("range")), _num_txt(l.get("slow"))] for l in lst["lines"]]) + [""]
    if lst.get("at"):
        meta = json.dumps({"at": lst["at"], "line_at": lst.get("line_at") or {}}, ensure_ascii=False).replace("-->", "--\\u003e")
        L += [META + meta + " -->", ""]
    return "\n".join(L)


def _split(line):
    body = line.strip()
    if len(body) < 2 or not (body.startswith("|") and body.endswith("|")):
        return None
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body[1:-1])]


def loads(text, path=None):
    errs, lst, sec, heads = [], {"project": None, "segments": {}, "lines": []}, None, {}
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if s.startswith(META) and s.endswith("-->"):
            try:
                meta = json.loads(s[len(META):-3])
                lst["at"], lst["line_at"] = meta.get("at") or {}, meta.get("line_at") or {}
            except (ValueError, AttributeError):
                pass  # 这一行坏了：当老清单（没记位置）处理，不报错
        elif s.startswith("# 合理范围清单："):
            lst["project"] = s.split("：", 1)[1].strip() or None
        elif s.startswith("## "):
            sec = {"分段": "seg", "测量线": "line"}.get(s[3:].strip())
            if sec is None:
                errs.append((n, f"不认识的标题「{s[3:].strip()}」，只认「分段」「测量线」"))
        elif sec and s.startswith("|"):
            cells = _split(raw)
            want = heads.get(sec) or (SEG_HEAD if sec == "seg" else LINE_HEAD)
            if cells is None:
                errs.append((n, "表格的一行要以 | 开头、以 | 结尾"))
            elif sec not in heads:
                heads[sec] = OLD_LINE_HEAD if sec == "line" and cells == OLD_LINE_HEAD else want
                if cells != heads[sec]:
                    errs.append((n, f"表头改了：应为「{' | '.join(want)}」"))
            elif all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            elif len(cells) != len(want):
                errs.append((n, f"这一行有 {len(cells)} 列，应为 {len(want)} 列（{'、'.join(want)}）"))
            else:
                _row(lst, sec, n, cells[:3] if sec == "line" else cells, errs)  # 老清单的体验标准、出处两列不读
    for sec, zh in (("seg", "分段"), ("line", "测量线")):
        if sec not in heads:
            errs.append((0, f"缺「## {zh}」这张表"))
    if errs:
        raise ListFormatError(errs, path)
    return lst


def _row(lst, sec, n, c, errs):
    def num(v, what):
        if v == NONE:
            return None
        try:
            return _num(v)
        except ValueError:
            errs.append((n, f"{what}要写数字（单位秒），空着写「—」，现在是「{v}」"))

    def rng(v, what):
        try:
            return _rng(v)
        except ValueError:
            errs.append((n, f"{what}的合理范围要写成「最少～最多」（比如 0.3～1.0），空着写「—」，现在是「{v}」"))

    if sec == "seg":
        key, name, pts, r_txt, s_txt, note = c
        what = f"分段「{name or key}」"
        a, sep, b = pts.partition("→")
        if not key or key in lst["segments"]:
            errs.append((n, f"{what}的编号空着或重复了"))
        if not sep:
            errs.append((n, f"{what}的起止计时点要写成「P2→P4」"))
        r, slow = rng(r_txt, what), num(s_txt, f"{what}的慢阈值")
        bad = check(r, slow, what)
        if bad:
            errs.append((n, bad))
        lst["segments"][key] = {"name": name, "from": a.strip(), "to": b.strip(), "range": r, "slow": slow,
                                "note": "" if note == NONE else note}
    else:
        name, r_txt, s_txt = c
        what = f"测量线「{name}」"
        r, slow = rng(r_txt, what), num(s_txt, f"{what}的慢阈值")
        bad = check(r, slow, what)
        if bad:
            errs.append((n, bad))
        lst["lines"].append({"name": name, "range": r, "slow": slow})


def path_of(project_dir):
    return Path(project_dir) / FILENAME


def read(project_dir):
    """没有返回 None；写坏了抛 ListFormatError。"""
    p = path_of(project_dir)
    return loads(p.read_text(encoding="utf-8"), path=str(p)) if p.exists() else None


def save(project_dir, lst):
    p = path_of(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(lst), encoding="utf-8")
    return p


# ---------- 按实测自动填 ----------

def _sig(x, mode):
    """保留 2 位有效数字；下限往下取、上限往上取，保证实测都落在范围里。"""
    if x == 0:
        return 0.0
    d = Decimal(repr(float(x)))
    return float(d.quantize(Decimal(1).scaleb(d.adjusted() - 1), rounding=mode))


def backfill(vals):
    """范围 = 最快×0.9～最慢×1.1，慢阈值 = 最慢×1.4；计时点几乎同时、个别轮算出负数时下限按 0。"""
    lo, hi = min(vals), max(vals)
    return [max(0.0, _sig(lo * 0.9, ROUND_FLOOR)), _sig(hi * 1.1, ROUND_CEILING)], _sig(hi * 1.4, ROUND_CEILING)


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


def _match(lst, scenario):
    """清单里的段、线 → 这次的段、线：({这次编号: 清单编号}, {这次线名: 清单里那条线})。"""
    at = at_now(scenario)
    segs = pair_segments(lst["segments"], lst.get("at") or {}, scenario["segments"], at)
    names = [l["name"] for l in lst["lines"]]
    old_sigs = [(lst.get("line_at") or {}).get(n) for n in names]
    new_sigs = [line_sig(scenario, l, at) for l in scenario["lines"]]
    lines = pair_lines(names, old_sigs, [l["name"] for l in scenario["lines"]], new_sigs)
    by_name = {l["name"]: l for l in lst["lines"]}
    return {v: k for k, v in segs.items()}, {v: by_name[k] for k, v in lines.items()}


def fill(lst, scenario, rounds, project=None):
    """测满 10 轮：没有清单就建一份；有清单就只给新出现的、空着的段和线补上，已经有数的不动。
    返回 (清单, 这次补了哪些名字)；不到 10 轮返回 (原样, [])。"""
    if len(rounds) < MIN_ROUNDS:
        return lst, []
    lst = lst or {"project": project, "segments": {}, "lines": []}
    seg_of, line_of = _match(lst, scenario)
    done, segs, lines = [], {}, []
    # 清单按这次的段、线重写：对上的带着原来的数（编号、名字换成这次的），对不上的是新的；
    # 清单里这次已经没有的段、线（步骤变了）去掉，免得以后按编号又套到别的步骤上
    for k, g in scenario["segments"].items():
        old = lst["segments"].get(seg_of.get(k)) or {}
        cur = segs[k] = {"name": g["name"], "from": g.get("from"), "to": g.get("to"), "range": old.get("range"),
                         "slow": old.get("slow"), "note": old.get("note") or ""}
        vals = [r["segs"][k] for r in rounds if r["segs"].get(k) is not None]
        if cur["range"] is None and vals:
            cur["range"], cur["slow"] = backfill(vals)
            cur["note"] = f"实测 {len(vals)} 轮自动填"
            done.append(cur["name"])
    for i, l in enumerate(scenario["lines"]):
        old = line_of.get(l["name"]) or {}
        cur = {"name": l["name"], "range": old.get("range"), "slow": old.get("slow")}
        lines.append(cur)
        vals = [r["ends"][i] for r in rounds if i < len(r["ends"]) and r["ends"][i] is not None]
        if cur.get("range") is None and vals:
            cur["range"], cur["slow"] = backfill(vals)
            done.append(l["name"])
    at = at_now(scenario)
    lst = {**lst, "segments": segs, "lines": lines}
    if at:
        lst["at"] = at
        lst["line_at"] = {l["name"]: s for l in scenario["lines"] if (s := line_sig(scenario, l, at))}
    return lst, done


def apply(scenario, lst):
    """判定用：把清单里的数套到这次的场景上（按计时点在代码里的位置对，见文件开头）。清单里对不上的段、线不判。"""
    import copy
    s = copy.deepcopy(scenario)
    seg_of, line_of = _match(lst, s)
    for k, g in s["segments"].items():
        lg = lst["segments"].get(seg_of.get(k)) or {}
        g.update(range=lg.get("range"), slow=lg.get("slow"), basis="measured")
    for l in s["lines"]:
        got = line_of.get(l["name"]) or {}
        l.update(range=got.get("range"), slow=got.get("slow"))
    s["experiences"] = []  # 体验标准不在清单里：报告最后按设备类别从标准库挂（standards.with_default_experience）
    s.pop("experience", None)
    s["standard"] = None
    s["range_list"] = {"file": FILENAME, "text": f"{lst.get('project') + ' 的' if lst.get('project') else ''}合理范围清单（{FILENAME}）"}
    return s


# ---------- 改 ----------

def edit_segment(lst, key, rng, slow, note=""):
    if key not in lst["segments"]:
        raise ListRuleError(f"清单里没有分段「{key}」")
    g = lst["segments"][key]
    bad = check(rng, slow, f"「{g['name']}」")
    if bad:
        raise ListRuleError(bad)
    note = (note or "").strip()
    if (rng, slow) != (g.get("range"), g.get("slow")) and note == g.get("note") and note.endswith("自动填"):
        note = "手动改过"  # 数改了、备注没动：别再写"实测自动填"
    g.update(range=rng, slow=slow, note=note)
    return lst


def edit_line(lst, name, rng, slow):
    l = next((x for x in lst["lines"] if x["name"] == name), None)
    if l is None:
        raise ListRuleError(f"清单里没有测量线「{name}」")
    bad = check(rng, slow, f"「{name}」")
    if bad:
        raise ListRuleError(bad)
    l.update(range=rng, slow=slow)
    return lst
