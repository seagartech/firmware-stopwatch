"""合理范围清单：每个被测项目一份，存 ~/firmware-stopwatch-data/项目名/合理范围清单.md（减重版，2026-10-17）。

两张表，人能直接读、直接改：
- 分段：编号、分段、起止计时点、合理范围、慢阈值、备注；
- 测量线：测量线、合理范围、慢阈值、体验标准（超过多少秒算差，可以不填）、出处。

第一次测满 10 轮，码表按实测自动填（范围 = 最快×0.9～最慢×1.1，慢阈值 = 最慢×1.4）；
之后只给新出现的、或空着的段补上，已经有数的不动。报告每次都按这份表判。
文件写坏了报"第几行哪里不对"，不悄悄忽略。
"""

import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

FILENAME = "合理范围清单.md"
MIN_ROUNDS = 10  # 测满这么多轮才拿来自动填清单：跑流程的几轮不算数
NONE = "—"
SEG_HEAD = ["编号", "分段", "起止计时点", "合理范围（秒）", "慢阈值（秒）", "备注"]
LINE_HEAD = ["测量线", "合理范围（秒）", "慢阈值（秒）", "体验标准：超过多少秒算差", "出处"]


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


def dumps(lst):
    L = [f"# 合理范围清单：{lst.get('project') or '（未命名项目）'}", "",
         "> 码表按下面两张表判快慢（正常 / 偏慢 / 超过慢阈值），可以直接改表里的数；不要改表头。",
         "> 合理范围写「最少～最多」（单位秒，上限没有写「不限」），空着写「—」。",
         "> 第一次测满 10 轮时码表按实测自动填写：范围 = 最快×0.9～最慢×1.1，慢阈值 = 最慢×1.4。", "",
         "## 分段", ""]
    L += _table(SEG_HEAD, [[k, g["name"], f"{g['from']}→{g['to']}", _range_txt(g.get("range")), _num_txt(g.get("slow")), g.get("note")]
                           for k, g in lst["segments"].items()]) + ["", "## 测量线", ""]
    L += _table(LINE_HEAD, [[l["name"], _range_txt(l.get("range")), _num_txt(l.get("slow")), _num_txt(l.get("fair")),
                             l.get("source") if l.get("fair") is not None else NONE] for l in lst["lines"]]) + [""]
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
        if s.startswith("# 合理范围清单："):
            lst["project"] = s.split("：", 1)[1].strip() or None
        elif s.startswith("## "):
            sec = {"分段": "seg", "测量线": "line"}.get(s[3:].strip())
            if sec is None:
                errs.append((n, f"不认识的标题「{s[3:].strip()}」，只认「分段」「测量线」"))
        elif sec and s.startswith("|"):
            cells, want = _split(raw), SEG_HEAD if sec == "seg" else LINE_HEAD
            if cells is None:
                errs.append((n, "表格的一行要以 | 开头、以 | 结尾"))
            elif sec not in heads:
                heads[sec] = n
                if cells != want:
                    errs.append((n, f"表头改了：应为「{' | '.join(want)}」"))
            elif all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            elif len(cells) != len(want):
                errs.append((n, f"这一行有 {len(cells)} 列，应为 {len(want)} 列（{'、'.join(want)}）"))
            else:
                _row(lst, sec, n, cells, errs)
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
        name, r_txt, s_txt, fair, src = c
        what = f"测量线「{name}」"
        r, slow = rng(r_txt, what), num(s_txt, f"{what}的慢阈值")
        bad = check(r, slow, what)
        if bad:
            errs.append((n, bad))
        lst["lines"].append({"name": name, "range": r, "slow": slow, "fair": num(fair, f"{what}的体验标准"),
                             "source": "" if src == NONE else src})


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


def fill(lst, scenario, rounds, project=None):
    """测满 10 轮：没有清单就建一份；有清单就只给新出现的、空着的段和线补上，已经有数的不动。
    返回 (清单, 这次补了哪些名字)；不到 10 轮返回 (原样, [])。"""
    if len(rounds) < MIN_ROUNDS:
        return lst, []
    lst = lst or {"project": project, "segments": {}, "lines": []}
    done = []
    by_pts = {(g["from"], g["to"]): k for k, g in lst["segments"].items()}
    for k, g in scenario["segments"].items():
        lk = k if k in lst["segments"] else by_pts.get((g.get("from"), g.get("to")))
        vals = [r["segs"][k] for r in rounds if r["segs"].get(k) is not None]
        if lk is None:
            lk = k
            lst["segments"][k] = {"name": g["name"], "from": g.get("from"), "to": g.get("to"), "range": None, "slow": None, "note": ""}
        cur = lst["segments"][lk]
        if cur.get("range") is None and vals:
            cur["range"], cur["slow"] = backfill(vals)
            cur["note"] = f"实测 {len(vals)} 轮自动填"
            done.append(cur["name"])
    have = {l["name"]: l for l in lst["lines"]}
    from fsw.scenario import experience_of
    for i, l in enumerate(scenario["lines"]):
        cur = have.get(l["name"])
        if cur is None:
            es = experience_of(scenario, l["name"]) or {}
            cur = {"name": l["name"], "range": None, "slow": None, "fair": es.get("fair"), "source": es.get("source") or ""}
            lst["lines"].append(cur)
            have[l["name"]] = cur
        vals = [r["ends"][i] for r in rounds if i < len(r["ends"]) and r["ends"][i] is not None]
        if cur.get("range") is None and vals:
            cur["range"], cur["slow"] = backfill(vals)
            done.append(l["name"])
    return lst, done


def apply(scenario, lst):
    """判定用：把清单里的数套到这次的场景上（按编号对，对不上按起止计时点对）。清单里没有的段不判。"""
    import copy
    s = copy.deepcopy(scenario)
    by_pts = {(g["from"], g["to"]): k for k, g in lst["segments"].items()}
    for k, g in s["segments"].items():
        lg = lst["segments"].get(k) or lst["segments"].get(by_pts.get((g.get("from"), g.get("to"))), {})
        g.update(range=lg.get("range"), slow=lg.get("slow"), basis="measured")
    have = {l["name"]: l for l in lst["lines"]}
    for l in s["lines"]:
        got = have.get(l["name"]) or {}
        l.update(range=got.get("range"), slow=got.get("slow"))
    s["experiences"] = [{"line": l["name"], "fair": have[l["name"]]["fair"], "source": have[l["name"]].get("source") or "用户设定"}
                        for l in s["lines"] if have.get(l["name"], {}).get("fair") is not None]
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
    g.update(range=rng, slow=slow, note=(note or "").strip())
    return lst


def edit_line(lst, name, rng, slow, fair, source=""):
    l = next((x for x in lst["lines"] if x["name"] == name), None)
    if l is None:
        raise ListRuleError(f"清单里没有测量线「{name}」")
    bad = check(rng, slow, f"「{name}」")
    if bad:
        raise ListRuleError(bad)
    if fair is not None and fair <= 0:
        raise ListRuleError("体验标准要写大于 0 的秒数，不设就空着")
    l.update(range=rng, slow=slow, fair=fair, source=((source or "").strip() or "用户设定") if fair is not None else "")
    return lst
