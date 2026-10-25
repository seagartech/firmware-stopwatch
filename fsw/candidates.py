"""码表自己在源码里找候选行：每个关键角色（输入结束检测、模式、协议、上传发送、收到某种消息、输出、全部放完）列出几句
核对过的真语句，大模型只从里面挑，不再自己写行号。找法只看通用写法（判断条件里的词、按消息类型分支、循环里的输出调用、
放空那一支），不认任何具体固件、文件或函数名。按设备类别（体验标准库里的类别名）换一组词；认不出类别用通用词。"""

import re
from pathlib import PurePosixPath

from fsw import structure as st

# 各类别的词：输入结束（设备察觉人做完输入的检测）、上传的数据、输出、全部放完
WORDS = {
    "语音对话": {"start": r"silen|speech_?end|vad_?end|voice_?end|end_?of_?speech",
                 "data": r"audio|voice|opus|pcm|packet|payload|frame",
                 "out": r"output|play|i2s_?write|speaker",
                 "done": r"drain|played|play_?end|playback_?(?:done|finish|end|complete)"},
    "门禁": {"start": r"card|uid|rfid|nfc|finger|face|credential|detect",
             "data": r"card|uid|credential|auth|verify|request|payload",
             "out": r"relay|unlock|door|lock|beep|buzzer",
             "done": r"complete|finish|done|closed"},
}
for _k in ("智能门锁", "刷卡终端"):
    WORDS[_k] = WORDS["门禁"]
GENERIC = {"start": r"release|detect|finish|complete|ready",
           "data": r"data|packet|payload|report",
           "out": r"output|write|play|show|draw",
           "done": r"drain|empty|complete|finish|done"}
SEND = r"send|publish|transmit|upload|post"
NET = r"protocol|transport|client|conn|link|mqtt|websocket|http|udp|tcp|ble|serial|channel"
ROLES = ("输入结束", "模式", "协议", "上传", "消息", "输出", "全部放完")
DESC = {"输入结束": "设备察觉人做完输入（检测翻转的那一支里）", "模式": "运行时算出模式的那一句", "协议": "按条件选协议或通道的那一串判断的开头",
        "上传": "把数据真正发出去的那一句", "消息": "收到某一种消息后，那一支里真正生效的语句",
        "输出": "逐段循环里真正把每段输出送出去的那一句", "全部放完": "判断出全部放完（放空、做完）的那一支里的那一句"}
LIMIT = {"输入结束": 4, "模式": 4, "协议": 3, "上传": 8, "消息": 30, "输出": 3, "全部放完": 3}
_LOG = re.compile(r"\b(?:ESP_LOG\w*|printf|printk|LOG\w*|log_\w+|Serial\.print\w*|std::cout)\s*\(|\bassert\s*\(", re.I)
_TESTDIR = re.compile(r"(?:^|/)(?:tests?|examples?|docs?|scripts?|tools?)(?:/|$)|_test\.\w+$|test_\w+\.\w+$", re.I)


def _words(category):
    return WORDS.get(str(category or "").strip(), GENERIC)


class _Finder:
    def __init__(self, root, category):
        self.proj = st.Project(root)
        self.w = _words(category)
        self.raw = {}
        for rel in self.proj.sources():
            if _TESTDIR.search(rel):
                continue
            try:
                self.raw[rel] = (self.proj.root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        self.variant = self._variants()
        self.found, self.seen = [], set()

    def _variants(self):
        """同一个目录下并排很多个子目录、每个都放源码（各型号板子、各款外设这一类）：它们下面的文件只是某一款才用，排在后面。"""
        kids = {}
        for rel in self.raw:
            p = PurePosixPath(rel).parts
            for i in range(1, len(p) - 1):
                kids.setdefault("/".join(p[:i]), set()).add(p[i])
        many = {d for d, s in kids.items() if len(s) >= 8}
        return {rel for rel in self.raw if any(rel.startswith(d + "/") and rel.count("/") > d.count("/") + 1 for d in many)}

    def files(self, pattern):
        rx = re.compile(pattern, re.I)
        for rel in sorted(self.raw, key=lambda r: (r in self.variant, r)):
            if rx.search(self.raw[rel]):
                src = self.proj.file(rel)
                if src is not None:
                    yield rel, src

    def add(self, role, rel, src, line, where="", rank=0, area=None):
        """area：哪几行算"和这个候选在同一支"（输入结束、消息、全部放完是那一支；其余只算这一句）。"""
        if (role, rel, line) in self.seen:
            return
        self.seen.add((role, rel, line))
        self.found.append({"role": role, "file": rel, "line": line, "code": src.lines[line - 1].strip(), "where": where,
                           "area": list(area or (line, line)), "_rank": (rel in self.variant, rank)})


def _ok(src, line, allow_return=False):
    """能当计时点的真语句：不是判断或循环开头、分号、大括号、else、上一句的接续、编译开关；不在出错、失败才走的那一支里；
    不是只打日志；不是 return/break 这类提前退出（allow_return：return 后面带着真正的动作调用也行）。"""
    if not st.real_stmt(src, line) or _in_failure(src, line):
        return False
    c = src.clines[line - 1].strip()
    if _LOG.search(src.lines[line - 1]):
        return False
    if any(src.blocks[i]["kind"] != "lambda" for i in st._opened_on(src, line)):
        return False  # 这一行打开的是循环、判断一类（比如写成宏的遍历）
    if st._EXIT.match(c):
        return allow_return and bool(re.match(r"return\s+[^;]*\w\s*\(", c))
    return True


def _in_failure(src, line):
    """在"失败、出错、没有才走"的那一支里（往外看三层判断）：条件只是取反、判空，或者条件里就是一次发送（if (!send(x)) 里面是发送失败）。
    带 && 的组合条件（比如 新状态 && !之前的状态）不算。"""
    n = 0
    for i in src.chain(src.pos(line, "before")):
        b = src.blocks[i]
        if b["kind"] in ("function", "lambda"):
            return False
        if b["kind"] != "branch":
            continue
        n += 1
        h = b["head"].strip()
        if re.match(r"(?:\}\s*)?(?:else\s+)?if\b", h):
            c = _cond(h)
            if st.call_in_if("if (" + c + ")") and st._FAIL_COND.search(c):
                return True
            if st._FAIL_COND.search(c) and not re.search(r"&&|\|\|", c):
                return True
        if n >= 3:
            return False
    return False


def _area(src, bi):
    b = src.blocks[bi]
    return src.line_of(b["open"]), src.line_of(b["close"])


def _cond(head):
    m = re.match(r"(?:\}\s*)?(?:else\s+)?(?:if|while|switch)\s*\((.*)\)\s*$", head.strip())
    return m.group(1).strip() if m else head.strip()


def _short(t, n=60):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _raw_head(src, bi):
    """块开头那一行的原文（带字符串，比较的消息类型写在字符串里）。"""
    return src.lines[src.line_of(src.blocks[bi]["open"]) - 1].strip()


def _prev_sibling(src, bi):
    b = src.blocks[bi]
    best = None
    for j, x in enumerate(src.blocks):
        if x["parent"] == b["parent"] and x["close"] < b["open"] and (best is None or x["close"] > src.blocks[best]["close"]):
            best = j
    return best


def _path(src, line, upto=None, depth=3):
    """这一行要满足的条件（从外到里，最多 depth 层，停在 upto 块）：给模型看"在哪一支里"。else 那一支写成"…不成立"。"""
    out = []
    for i in src.chain(src.pos(line, "before")):
        if i == upto:
            break
        b = src.blocks[i]
        if b["kind"] == "function":
            break
        if b["kind"] == "branch":
            h = b["head"].strip()
            if re.match(r"(?:\}\s*)?else\s*$", h):
                p = _prev_sibling(src, i)
                out.append(f"「{_short(_cond(src.blocks[p]['head']), 50)}」不成立" if p is not None else "否则")
            elif re.match(r"(?:\}\s*)?(?:else\s+)?if\b", h):
                raw = _raw_head(src, i).rstrip("{ ")
                out.append(f"「{_short(_cond(raw) if re.search(r'\bif\s*\(', raw) else _cond(h), 50)}」")
        elif b["kind"] == "lambda":
            out.append(f"交给 {b['name'] or '回调'} 稍后执行")
        if len(out) >= depth:
            break
    return "，".join(reversed(out))


def _stmts(src, bi, keep=None):
    """块里（含里面的判断和稍后执行的回调）真语句的行号。keep(行号) 为假的跳过。"""
    b = src.blocks[bi]
    start, end = src.line_of(b["open"]), src.line_of(b["close"])
    out = []
    for k in range(start, end + 1):
        if k == start and not src.clines[k - 1].rstrip().endswith("{"):
            continue
        if not _ok(src, k):
            continue
        if bi not in src.chain(src.pos(k, "before")):
            continue
        if keep and not keep(k):
            continue
        out.append(k)
    return out


def _start(f):
    rx = re.compile(f.w["start"], re.I)
    for rel, src in f.files(f.w["start"]):
        for i, b in enumerate(src.blocks):
            if b["kind"] != "branch" or not re.search(r"\bif\b", b["head"]) or not rx.search(b["head"]):
                continue
            c = _cond(b["head"])
            if st._FAIL_COND.search(c) and "&&" not in c:
                continue
            inner = lambda k, i=i: src.chain(src.pos(k, "before"))[0] == i  # 只要这一支自己的语句
            for k in _stmts(src, i, inner)[:3]:
                f.add("输入结束", rel, src, k, f"在「{_short(c)}」那一支里", 0 if "&&" in c else 1, _area(src, i))


def _mode(f):
    for rel, src in f.files(r"mode"):
        for k, c in enumerate(src.clines, 1):
            s = c.strip()
            if "?" not in s or not re.search(r"mode", s, re.I):
                continue
            arms = re.search(r"\?\s*([\w:]+)\s*:\s*([\w:]+)\s*;", s)
            if not arms or not all(re.search(r"mode", a, re.I) for a in arms.groups()):
                continue
            if not st.real_stmt(src, k) or not st._in_func(src, k):
                continue
            how = "按条件返回模式" if s.startswith("return") else "按条件给模式赋值"
            f.add("模式", rel, src, k, f"在函数 {st.func_at(src, k)[0]} 里{how}", 0 if s.startswith("return") else 1)


def _protocol(f):
    rx_net = re.compile(NET, re.I)
    for rel, src in f.files(r"make_unique|make_shared|\bnew\s"):
        for k, c in enumerate(src.clines, 1):
            s = c.strip()
            if not re.match(r"if\s*\(", s) or not s.endswith("{") or not st._in_func(src, k):
                continue
            end = st._block_end(src, k)
            if not end:
                continue
            made = {}
            for j in range(k, end + 1):
                m = re.match(r"([\w.\->]+)\s*=\s*(?:std::make_(?:unique|shared)<(\w+)>|new\s+(\w+))", src.clines[j - 1].strip())
                if m:
                    made.setdefault(m.group(1), set()).add(m.group(2) or m.group(3))
            for v, kinds in made.items():
                if len(kinds) >= 2:
                    net = bool(rx_net.search(v) or any(rx_net.search(x) for x in kinds))
                    f.add("协议", rel, src, k, f"按条件给 {v} 选不同的实现（{'、'.join(sorted(kinds))}）", 0 if net else 1)
                    break


def _data_in(f, text):
    """这一句说的是要传的数据：只看变量、函数名，不看全大写的常量（比如某个引脚名里带 AUDIO）。"""
    rx = re.compile(f.w["data"], re.I)
    return any(rx.search(w) for w in re.findall(r"[A-Za-z_]\w*", text) if not re.fullmatch(r"[A-Z0-9_]+", w))


def _upload(f):
    rx_send = re.compile(rf"(?<![\w])(\w*(?i:{SEND})\w*)\s*\(")
    for rel, src in f.files(SEND):
        for k, c in enumerate(src.clines, 1):
            s = c.strip()
            m = rx_send.search(s)
            if not m or st._is_query(m.group(1)) or re.match(r"(?:on_|On[A-Z])", m.group(1)) or not st._in_func(src, k):
                continue
            fn = st.func_at(src, k)[0] or ""
            sender = bool(re.search(SEND, fn, re.I) and _data_in(f, fn))  # 在"发送某种数据"的函数里
            if not _data_in(f, s) and not sender:
                continue
            if any(src.blocks[i]["kind"] == "function" for i in st._opened_on(src, k)):
                continue
            if re.match(r"(?:\}\s*)?(?:else\s+)?if\s*\(", s):
                if not st.call_in_if(s.lstrip("} ").replace("else ", "", 1)):
                    continue
                how = "（发送写在 if 条件里，点放在这一行前面）"
            elif not _ok(src, k, allow_return=True):
                continue
            else:
                how = ""
            loops, _i, _o = src.context(src.pos(k, "before"))
            f.add("上传", rel, src, k, f"在函数 {fn} 里调用 {m.group(1)} 发出数据{how}", 0 if loops else (1 if sender else 2))


_DISPATCH = re.compile(r"""strn?cmp\s*\(\s*([^,]+?)\s*,\s*"([^"]{1,32})"\s*(?:,[^)]*)?\)\s*==\s*0|([\w.\->\[\]]+)\s*==\s*"([^"]{1,32})"|"([^"]{1,32})"\s*==\s*([\w.\->]+)|case\s+"?(\w+)"?\s*:""")


def _messages(f):
    """按消息类型分支（同一处拿同一个字段和三种以上的值比较）：每一支里真正生效的语句（稍后执行的回调里那几句也列上）。"""
    for rel, src in f.files(r"strn?cmp\s*\(|==\s*\""):
        groups, mine = {}, {}
        for i, b in enumerate(src.blocks):
            if b["kind"] != "branch":
                continue
            m = _DISPATCH.search(_raw_head(src, i))
            if not m or not re.search(r"\bif\b", _raw_head(src, i)):
                continue
            field = (m.group(1) or m.group(3) or m.group(6) or "").strip()
            lit = m.group(2) or m.group(4) or m.group(5)
            groups.setdefault((b["parent"], field), []).append((i, lit))
        disp = {i: (field, lit) for (_p, field), items in groups.items() if len(items) >= 3 for i, lit in items}
        size = {i: len(items) for (_p, field), items in groups.items() if len(items) >= 3 for i, _l in items}
        for i, (field, lit) in disp.items():
            def mine_only(k, i=i):  # 只要这一支自己的（不含里面再按消息分的支、不含循环里一遍遍做的）
                ch = src.chain(src.pos(k, "before"))
                inner = ch[:ch.index(i)] if i in ch else ch
                return next((j for j in ch if j in disp), None) == i and not any(src.blocks[j]["kind"] == "loop" for j in inner)
            top = [j for j in src.chain(src.blocks[i]["open"]) if j in disp]
            param = re.fullmatch(r"\w+", field) and re.search(rf"\b{field}\b", _func_head(src, i))
            rank = (bool(param), -size[top[-1]] if top else -size[i])
            outer = [disp[j][1] for j in src.chain(src.blocks[i]["open"]) if j in disp and j != i]
            name = "/".join(outer[::-1] + [lit])
            rows = _stmts(src, i, mine_only)
            # 交给回调稍后执行的：回调里有语句就只要回调里的（真正生效的时刻），不要"交出去"那一句
            def hands_off(k):  # 这一句（可能写成几行）里打开了一个回调，回调里也有候选语句
                for j, b in enumerate(src.blocks):
                    o = src.line_of(b["open"])
                    if b["kind"] == "lambda" and k <= o <= k + 3 and st.stmt_start(src, o) == k:
                        if any(b["open"] < src.pos(x, "before") <= b["close"] for x in rows if x != k):
                            return True
                return False
            rows = [k for k in rows if not hands_off(k)]
            calls = [k for k in rows if re.search(r"[\w>.]\s*\(", src.clines[k - 1]) and not re.match(r"(?:auto|const|[\w:<>*&]+)\s+\w+\s*=\s*\w*(?:Get|get|Find|find|Parse|parse)", src.clines[k - 1].strip())]
            for k in calls[:3]:
                where = _path(src, k, upto=i)
                f.add("消息", rel, src, k, f"收到 {_short(field, 30)} 为「{name}」的消息" + (f"，{where}" if where else ""), rank, _area(src, i))


def _func_head(src, bi):
    return next((src.blocks[j]["head"] for j in src.chain(src.blocks[bi]["open"]) if src.blocks[j]["kind"] == "function"), "")


def _output(f):
    rx = re.compile(rf"(?<![\w])\w*(?i:{f.w['out']})\w*\s*\(")
    for rel, src in f.files(f.w["out"]):
        for k, c in enumerate(src.clines, 1):
            if not rx.search(c) or not _ok(src, k):
                continue
            m = rx.search(c)
            if re.search(f.w["done"], m.group(0), re.I):
                continue
            loops, inner, _o = src.context(src.pos(k, "before"))
            if not loops or inner is None:
                continue
            args = c[m.end():]
            fn = st.func_at(src, k)[0] or ""
            rank = 0 if _data_in(f, args) else (1 if _data_in(f, fn) else 2)
            f.add("输出", rel, src, k, f"在函数 {fn} 的循环里，每段输出都经过", rank)


def _done(f):
    rx = re.compile(f.w["done"], re.I)
    outs = sorted([c for c in f.found if c["role"] == "输出"], key=lambda c: c["_rank"])
    prefer = (outs[0]["file"], st.func_at(f.proj.file(outs[0]["file"]), outs[0]["line"])[0]) if outs else None
    for rel, src in f.files(f.w["done"]):
        for i, b in enumerate(src.blocks):
            if b["kind"] != "branch" or not re.search(r"\bif\b", b["head"]) or not rx.search(b["head"]):
                continue
            if st._not_done_branch(src, i) or not st._in_func(src, src.line_of(b["open"])):
                continue
            c = _cond(b["head"])
            for k in _stmts(src, i, lambda k, i=i: src.chain(src.pos(k, "before"))[0] == i)[:2]:
                same = prefer and rel == prefer[0] and st.func_at(src, k)[0] == prefer[1]
                f.add("全部放完", rel, src, k, f"在「{_short(c)}」那一支里", 0 if same else 1, _area(src, i))



def _shared_call(f, mine):
    """发送写在同一个接口的几种实现里（几种连接方式各写一份），而候选里另有一句就是调用这个接口方法的：
    那一句不管走哪种实现都会执行，只留它，各实现里的去掉（只在一种连接方式下才走到）。"""
    impl = {}
    for c in mine:
        src = f.proj.file(c["file"])
        hit = st.one_of_impls(f.proj, src, c["line"]) if src is not None else None
        if hit:
            impl[id(c)] = hit[1]
    if not impl:
        return mine
    names = set(impl.values())
    callers = [c for c in mine if id(c) not in impl and any(re.search(rf"(?:->|\.)\s*{re.escape(n)}\s*\(", c.get("code") or "") for n in names)]
    if not callers:
        return mine
    called = {n for n in names if any(re.search(rf"(?:->|\.)\s*{re.escape(n)}\s*\(", c.get("code") or "") for c in callers)}
    return [c for c in mine if impl.get(id(c)) not in called]

def find(root, category=""):
    """返回候选列表：[{id, role, file, line, code, where}]，id 是 K1、K2……（按角色排好，每个角色最多几句）。"""
    f = _Finder(root, category)
    for fn in (_start, _mode, _protocol, _upload, _messages, _output, _done):
        try:
            fn(f)
        except Exception:  # 某一类找不到不影响别的
            pass
    out = []
    for role in ROLES:
        mine = sorted((c for c in f.found if c["role"] == role), key=lambda c: c["_rank"])
        if mine and not mine[0]["_rank"][0]:  # 有通用的就不要只某一款才用的
            mine = [c for c in mine if not c["_rank"][0]]
        if role == "上传" and mine and mine[0]["_rank"][1] <= 1:  # 有在循环里、或在"发送某种数据"的函数里的，就不要别的
            mine = [c for c in mine if c["_rank"][1] <= 1]
        if role in ("输出", "全部放完") and mine and mine[0]["_rank"][1] == 0:  # 有最像的（数据在参数里、和输出同一个函数）就只要这些
            mine = [c for c in mine if c["_rank"][1] == 0]
        if role == "消息" and mine and not mine[0]["_rank"][1][0]:  # 有收进来的消息分支，就不要按函数参数分支的
            mine = [c for c in mine if not c["_rank"][1][0]]
        if role == "上传":
            mine = _shared_call(f, mine)
        mine = mine[:LIMIT[role]]
        if role == "消息":
            mine.sort(key=lambda c: (c["file"], c["line"]))
        out += mine
    for i, c in enumerate(out, 1):
        c.pop("_rank", None)
        c["id"] = f"K{i}"
    return out


def prompt_section(cands):
    """给第一步的提示词：按角色列出候选。"""
    if not cands:
        return ""
    lines = ["", "## 码表在源码里找到的候选（每一句都核对过是真正执行的语句）"]
    for role in ROLES:
        mine = [c for c in cands if c["role"] == role]
        if not mine:
            continue
        lines.append(f"{DESC[role]}：")
        lines += [f"- {c['id']} {c['file']}:{c['line']}「{_short(c['code'], 70)}」{('（' + c['where'] + '）') if c['where'] else ''}" for c in mine]
    return "\n".join(lines) + "\n"
