"""识别后的结构检查：所有大模型共用，只看源码结构，不看是哪个模型、哪个固件。

大模型给的位置先由 identify.check_location 核对"这一行在不在"；这里再看这一行插下去以后落在什么结构里：
- 硬失败：插在 if / else if / else / while / for / do / switch 这一行"之后"，点会落进分支或循环体，
  只在条件成立（或每循环一次）时才记到。识别结果没有字段能说明"有意放进分支"，一律按位置不对处理。
- 提示（判断不一定准，只提醒、不拦）：
  · 一轮记每一次（once=every）的点在循环里，或所在函数在别处的循环里被反复调用（比如逐帧处理）；
  · 点在 #if / #ifdef 编译开关里：对照 sdkconfig、sdkconfig.defaults*、Kconfig 默认值标出开关取值；
  · 同一段的两头写在同一个函数里，一头当场执行，另一头在 lambda 里（交给 Schedule 之类排队、登记成回调，之后另外执行）。

只做静态的文本结构分析（去掉注释和字符串后数括号），不编译、不跑代码，也不知道运行时走哪条路。
说明文字用中文，不出现 P1 这类内部编号（决策 168）。
"""

import re
from pathlib import Path

SRC_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ino"}
SKIP_DIRS = {".git", "build", "managed_components", "node_modules", ".fsw", "fsw-backup"}
KEYWORDS = {"if", "else", "while", "for", "do", "switch", "case", "return", "sizeof", "catch", "try", "namespace",
            "struct", "class", "union", "enum", "extern", "typedef"}
_LAMBDA = re.compile(r"(?:^|[(,={]|\breturn)\s*\[[^\[\]]*\]\s*(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?"
                     r"\s*(?:mutable|constexpr|noexcept|\s)*(?:->\s*[^{}]*)?$")
_LOOP = re.compile(r"^(?:while|for)\b|^do$|^\w*for_?each\w*\s*\(", re.I)
_CTRL_AFTER = re.compile(r"^(?:\}\s*)?(else\s+if|if|while|for|switch|else|do)\b")
_CONFIG = re.compile(r"\bCONFIG_\w+")


# ---------- 去掉注释和字符串，留下能数括号的代码 ----------
def mask(text):
    """注释、字符串、字符常量换成空格（换行保留，行号不变）；预处理行（含续行）也换成空格。"""
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [ch if ch == "\n" else " " for ch in text[i:j]]
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out[i:j] = [ch if ch == "\n" else " " for ch in text[i:j]]
            i = j
        else:
            i += 1
    lines = "".join(out).split("\n")
    cont = False
    for k, ln in enumerate(lines):
        if cont or ln.lstrip().startswith("#"):
            cont = ln.rstrip().endswith("\\")
            lines[k] = " " * len(ln)
    return "\n".join(lines)


class Source:
    """一个源文件：原文、去注释后的代码、所有 {…} 块（开括号位置、闭括号位置、开括号前的头部、种类）。"""

    def __init__(self, text):
        self.text = text
        self.lines = text.split("\n")
        self.code = mask(text)
        self.clines = self.code.split("\n")
        self.starts = [0]
        for ln in self.lines:
            self.starts.append(self.starts[-1] + len(ln) + 1)
        self.blocks = []
        stack, depths = [], []
        last, paren = 0, 0  # last：上一个 ; { } 之后的位置，块头从这里开始；括号里的 ;（for 的三段）不算
        for i, ch in enumerate(self.code):
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "{":
                head = " ".join(self.code[last:i].split())
                b = {"open": i, "close": len(self.code), "head": head, "parent": stack[-1] if stack else None}
                b["kind"], b["name"] = self._kind(b)
                self.blocks.append(b)
                stack.append(len(self.blocks) - 1)
                depths.append(paren)
                last, paren = i + 1, 0
            elif ch == "}":
                if stack:
                    self.blocks[stack.pop()]["close"] = i
                    paren = depths.pop()
                last = i + 1
            elif ch == ";" and paren == 0:
                last = i + 1

    def _in_function(self, b):
        p = b["parent"]
        while p is not None:
            if self.blocks[p]["kind"] in ("function", "lambda"):
                return True
            p = self.blocks[p]["parent"]
        return False

    def _kind(self, b):
        h = b["head"]
        word = re.match(r"\w+", h)
        word = word.group() if word else ""
        if _LAMBDA.search(h):
            m = re.search(r"(\w+)\s*\(\s*(?:[^()]*,\s*)?\[[^\[\]]*\][^\[\]]*$", h)
            return "lambda", m.group(1) if m else ""
        if _LOOP.search(h):
            return "loop", word
        if word in ("if", "else", "switch", "case", "default"):
            return "branch", word
        if word in KEYWORDS or "(" not in h or self._in_function(b):
            return "other", word
        m = re.match(r"[^(]*?([A-Za-z_~]\w*)\s*\(", h)
        if m and m.group(1) not in KEYWORDS:
            return "function", m.group(1)
        return "other", word

    def pos(self, line, where):
        """计时点插下去的位置（字符偏移）：之前 = 这一行第一个字；之后 = 这一行行尾。"""
        k = line - 1
        if where == "after":
            return self.starts[k] + len(self.lines[k])
        return self.starts[k] + (len(self.lines[k]) - len(self.lines[k].lstrip()))

    def line_of(self, offset):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def chain(self, offset):
        """包住这个位置的块，从里到外。"""
        out = [i for i, b in enumerate(self.blocks) if b["open"] < offset <= b["close"]]
        return sorted(out, key=lambda i: -self.blocks[i]["open"])

    def context(self, offset):
        """从里到外走到第一个函数或 lambda 为止：(经过的循环块, 最里层的函数/lambda 块, 最外层的函数块)。"""
        loops, inner, outer = [], None, None
        for i in self.chain(offset):
            k = self.blocks[i]["kind"]
            if inner is None:
                if k == "loop":
                    loops.append(i)
                elif k in ("function", "lambda"):
                    inner = i
            if k == "function":
                outer = i
        return loops, inner, outer


class Project:
    """源码根目录：按需读文件（只读），顺带收集 sdkconfig 和 Kconfig 的取值。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._files = {}
        self._all = None
        self._cfg = None
        self._kconfig = None

    def file(self, rel):
        if rel not in self._files:
            p = (self.root / rel).resolve()
            try:
                ok = self.root in p.parents and p.is_file()
                self._files[rel] = Source(p.read_text(encoding="utf-8", errors="replace")) if ok else None
            except OSError:
                self._files[rel] = None
        return self._files[rel]

    def walk(self, pred):
        stack = [self.root]
        while stack:
            d = stack.pop()
            try:
                items = sorted(d.iterdir())
            except OSError:
                continue
            for e in items:
                if e.is_dir():
                    if e.name not in SKIP_DIRS and not e.is_symlink():
                        stack.append(e)
                elif pred(e):
                    yield e

    def sources(self):
        if self._all is None:
            self._all = [p.relative_to(self.root).as_posix() for p in self.walk(lambda e: e.suffix in SRC_EXT)]
        return self._all

    def config(self):
        """[(文件名, {CONFIG_X: 值})]：sdkconfig 在前（编译时真正用的），再是 sdkconfig.defaults*。"""
        if self._cfg is None:
            files = ([self.root / "sdkconfig"] if (self.root / "sdkconfig").is_file() else []) + \
                    sorted(p for p in self.root.glob("sdkconfig.defaults*") if p.is_file())
            self._cfg = []
            for p in files:
                vals = {}
                try:
                    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        m = re.match(r"\s*(CONFIG_\w+)=(.*)$", ln)
                        if m:
                            vals[m.group(1)] = m.group(2).strip()
                        m = re.match(r"\s*#\s*(CONFIG_\w+) is not set", ln)
                        if m:
                            vals[m.group(1)] = "n"
                except OSError:
                    continue
                self._cfg.append((p.name, vals))
        return self._cfg

    def kconfig_default(self, name):
        """Kconfig 里这个选项写的默认值（原样，比如 "y"、"n"、"y if FOO"），找不到返回 None。"""
        if self._kconfig is None:
            self._kconfig = {}
            for p in self.walk(lambda e: e.name.startswith("Kconfig")):
                try:
                    cur = None
                    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        s = ln.strip()
                        m = re.match(r"(?:menu)?config\s+(\w+)$", s)
                        if m:
                            cur = "CONFIG_" + m.group(1)
                            continue
                        if re.match(r"(?:endmenu|menu|choice|endchoice|if|endif|source|comment)\b", s):
                            cur = None
                        m = re.match(r"(?:default|def_bool|def_tristate)\s+(.+)$", s)
                        if cur and m and cur not in self._kconfig:
                            self._kconfig[cur] = m.group(1).strip()
                except OSError:
                    continue
        return self._kconfig.get(name)


# ---------- 预处理条件：这一行在哪些 #if 里 ----------
def directives_at(src, line):
    """包住第 line 行的预处理条件，从外到里：[{"line", "text", "branch": "if"/"elif"/"else", "cond", "guard"}]。"""
    stack, k, lines = [], 0, src.lines
    while k < min(line - 1, len(lines)):
        raw = lines[k]
        s = raw.strip()
        start = k
        while s.endswith("\\") and k + 1 < len(lines):
            k += 1
            s = s[:-1] + " " + lines[k].strip()
        k += 1
        m = re.match(r"#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$", s)
        if not m:
            continue
        d, rest = m.group(1), re.sub(r"/\*.*?\*/|//.*$", "", m.group(2)).strip()
        if d in ("if", "ifdef", "ifndef"):
            nxt = next((x.strip() for x in lines[k:k + 3] if x.strip()), "")
            guard = d == "ifndef" and re.match(rf"#\s*define\s+{re.escape(rest)}\b", nxt) is not None
            cond = rest if d == "if" else f"defined({rest})" if d == "ifdef" else f"!defined({rest})"
            stack.append({"line": start + 1, "text": s, "branch": "if", "cond": cond, "first": cond, "guard": guard})
        elif d == "elif" and stack:
            stack[-1].update(branch="elif", cond=rest, text=s, line=start + 1)
        elif d == "else" and stack:
            stack[-1].update(branch="else", line=start + 1, text=s)
        elif d == "endif" and stack:
            stack.pop()
    return [f for f in stack if not f["guard"]]


def _value(proj, name):
    """(说明, y/n/None)：先看 sdkconfig，再看 sdkconfig.defaults*，最后看 Kconfig 默认值。"""
    cfg = proj.config()
    hits = [(f, v[name]) for f, v in cfg if name in v]
    if hits and hits[0][0] == "sdkconfig":
        v = hits[0][1]
        return f"sdkconfig 里是 {v}", v if v in ("y", "n") else None
    if hits:
        vals = {v for _, v in hits}
        txt = "、".join(f"{f} 里是 {v}" for f, v in hits)
        return txt + ("" if len(vals) == 1 else "（不同芯片的默认配置不一样，以实际编译用的为准）"), \
            (next(iter(vals)) if len(vals) == 1 and next(iter(vals)) in ("y", "n") else None)
    d = proj.kconfig_default(name)
    if d is not None:
        plain = d if d in ("y", "n") else None
        return f"sdkconfig 里没写，Kconfig 默认 {d}" + ("（另受 depends on 等条件影响）" if plain else ""), plain
    return "sdkconfig、sdkconfig.defaults*、Kconfig 里都没找到", None


def _branch_on(frame, val):
    """单个开关的简单条件：这一支在开关为 val 时会不会编进去；判断不了返回 None。"""
    if val is None:
        return None
    m = re.fullmatch(r"(!?)\s*(?:defined\s*\(?\s*)?(CONFIG_\w+)\s*\)?", frame["first"].strip())
    if not m or frame["branch"] == "elif":
        return None
    on = val == "y"
    if m.group(1):
        on = not on
    return on if frame["branch"] == "if" else not on


# ---------- 各条检查 ----------
def _cond_text(s):
    return s if len(s) <= 60 else s[:58] + "…"


def check_branch(src, line, where):
    """插在控制语句那一行之后：落进分支或循环体。返回说明或 None。"""
    if where != "after":
        return None
    c = src.clines[line - 1].strip()
    m = _CTRL_AFTER.match(c)
    if not m:
        return None
    kw = m.group(1).split()[0]
    opens = c.endswith("{")
    if not opens:
        if c.endswith(";") or c.endswith("}"):
            return None
        if kw not in ("else", "do") and c.count("(") != c.count(")"):
            return None  # 条件还没写完，跨好几行：这里不判
        nxt = next((x.strip() for x in src.clines[line:line + 5] if x.strip()), "")
        opens = True if nxt.startswith("{") else "bare"
    code = _cond_text(src.lines[line - 1].strip())
    if kw in ("while", "for", "do"):
        what = "循环体里：每循环一次记一次，不进循环就一次也记不到"
    elif kw == "switch":
        what = " switch 里、第一个 case 之前：这里的代码执行不到"
    elif kw == "else":
        what = "else 分支里：只有前面的条件都不成立时才记到"
    else:
        what = "这个判断的分支里：只有条件成立时才记到"
        if re.search(r"!\s*[\w(]|==\s*(?:NULL|nullptr|false)\b|!=\s*(?:ESP_OK|0)\b|<\s*0\b", m.string[m.end():]):
            what += "（条件看起来是在判断失败或出错，正常运行时多半走不到）"
    bare = "；这一行后面没有大括号，插进去会变成这个判断唯一管的那句，还会改掉原来的逻辑" if opens == "bare" else ""
    return f"插在「{code}」这一行之后，会落进{what}{bare}。识别结果没有说明是有意放进这里，码表按位置不对处理，请核对"


def _callers_in_loop(proj, name, skip):
    """本工程里在循环体内调用 name(…) 的地方：[(文件, 行号)]（最多 3 处）。skip=(文件, 函数块) 是定义本身。"""
    out, pat = [], re.compile(rf"(?<!\w){re.escape(name)}\s*\(")
    for rel in proj.sources():
        src = proj.file(rel)
        if src is None or name not in src.code:
            continue
        for m in pat.finditer(src.code):
            at = m.start()
            loops, inner, _ = src.context(at)
            if (rel, inner) == skip or inner is None:
                continue
            if loops:
                out.append((rel, src.line_of(at)))
                if len(out) >= 3:
                    return out
    return out


def _params(head):
    """函数头里的参数名。"""
    m = re.search(r"\((.*)\)", head)
    if not m:
        return set()
    out = set()
    for p in m.group(1).split(","):
        p = re.sub(r"=.*$", "", p).strip()
        w = re.findall(r"[A-Za-z_]\w*", p)
        if w and w[-1] not in ("void", "const"):
            out.add(w[-1])
    return out


def _param_guard(src, inner, at):
    """函数开头到这个点之间，有没有"看传进来的参数不对就 return"的筛选。"""
    fn = src.blocks[inner]
    params = _params(fn["head"])
    if not params:
        return False
    word = re.compile(r"\b(?:" + "|".join(map(re.escape, params)) + r")\b")
    for i, b in enumerate(src.blocks):
        if b["kind"] == "branch" and b["name"] == "if" and fn["open"] < b["open"] and b["close"] < at \
                and _inside(src, i, inner) and re.search(r"\breturn\b", src.code[b["open"]:b["close"]]) and word.search(b["head"]):
            return True
    for m in re.finditer(r"\bif\s*\(([^;{}]*)\)\s*return\b", src.code[fn["open"]:at]):
        if word.search(m.group(1)):
            return True
    return False


def check_repeat(proj, rel, src, line, where, once):
    """一轮记每一次的点：在循环里，或所在函数在循环里被反复调用。返回说明或 None。"""
    if once != "every":
        return None
    at = src.pos(line, where)
    loops, inner, _ = src.context(at)
    if loops:
        b = src.blocks[loops[0]]
        head = _cond_text(b["head"])
        return (f"这一步设成一轮里每次都记，但它在第 {src.line_of(b['open'])} 行「{head}」这个循环里，一轮可能记很多次；"
                f"如果这一步一轮只发生一次，考虑改成只记第一次或最后一次")
    if inner is None or src.blocks[inner]["kind"] != "function":
        return None
    name = src.blocks[inner]["name"]
    if len(name) < 3:
        return None
    if _param_guard(src, inner, at):
        return None  # 点前面按传进来的数据筛过（比如"不是要找的那一帧就 return"）：不是每次调用都走到，不提示
    hits = _callers_in_loop(proj, name, (rel, inner))
    if not hits:
        return None
    where_txt = "、".join(f"{Path(f).name} 第 {n} 行" for f, n in hits)
    return (f"这一步设成一轮里每次都记，它所在的函数 {name} 在循环里被调用（{where_txt}），"
            f"会被反复执行（比如逐帧、逐包处理），一轮可能记很多次；如果这一步一轮只发生一次，考虑改成只记第一次或最后一次")


def check_ifdef(proj, src, line):
    """点在 #if / #ifdef 里：标出开关和取值。返回说明或 None。"""
    frames = directives_at(src, line)
    if not frames:
        return None
    parts = []
    for f in frames:
        names = sorted(set(_CONFIG.findall(f["first"] + " " + f["cond"])))
        where = f"第 {f['line']} 行「{_cond_text(f['text'])}」" + ("（开关条件不成立时才编进去的那一支）" if f["branch"] == "else" else "")
        if not names:
            others = sorted(set(re.findall(r"[A-Za-z_]\w*", f["cond"])) - {"defined"})
            parts.append(f"{where}：{'、'.join(others) or '这个条件'} 不在 sdkconfig 里，码表判断不了会不会编进去")
            continue
        vals = []
        for n in names:
            txt, v = _value(proj, n)
            vals.append(f"{n}：{txt}")
            if len(names) == 1:
                on = _branch_on(f, v)
                if on is not None:
                    vals[-1] += "，按这个取值这一步会编进固件" if on else "，按这个取值这一步不会编进固件，记不到"
        parts.append(f"{where}，{'；'.join(vals)}")
    return "这个计时点在编译开关里：" + "；".join(parts) + "。这一步编不编进固件取决于开关，请对照实际编译用的配置核对"


def _ctx(proj, step):
    if not step or not step.get("loc_ok", True) or not step.get("file") or not step.get("line"):
        return None
    src = proj.file(step["file"])
    if src is None or not 1 <= int(step["line"]) <= len(src.lines):
        return None
    _, inner, outer = src.context(src.pos(int(step["line"]), str(step.get("where") or "").strip().lower()))
    return src, inner, outer


def _lambda_at(src, i):
    b = src.blocks[i]
    via = f"交给 {b['name']} 的 " if b["name"] else ""
    return f"第 {src.line_of(b['open'])} 行{via}lambda "


def _inside(src, i, j):
    """块 i 是否在块 j 里面。"""
    p = src.blocks[i]["parent"]
    while p is not None:
        if p == j:
            return True
        p = src.blocks[p]["parent"]
    return False


def check_segments(proj, steps, segments):
    """同一段两头写在同一个函数里，但执行时机不同（一头当场执行，一头在 lambda 里排队、回调时才执行）。
    返回 [(段的终点 id, 说明)]。只看 lambda；函数指针登记的回调静态看不出来，不判。"""
    by = {s["id"]: s for s in steps}
    out = []
    for g in segments or []:
        if not isinstance(g, dict):
            continue
        a, b = by.get(g.get("from")), by.get(g.get("to"))
        ca, cb = _ctx(proj, a), _ctx(proj, b)
        if not ca or not cb or a["file"] != b["file"]:
            continue
        src, ia, oa = ca
        _, ib, ob = cb
        if oa is None or oa != ob or ia == ib:
            continue

        def lam(i):
            return i is not None and src.blocks[i]["kind"] == "lambda"
        if not lam(ia) and not lam(ib):
            continue
        seg = f"「{g.get('name')}」这一段" if g.get("name") else "这一段"
        head = f"{seg}从「{a['name']}」到「{b['name']}」：两头写在同一个函数 {src.blocks[oa]['name']} 里，"
        if lam(ib) and (ia == oa or _inside(src, ib, ia)):
            body = (f"但「{b['name']}」写在{_lambda_at(src, ib)}里，要排队调度或被回调时才另外执行，"
                    f"「{a['name']}」在外层代码里当场执行")
        elif lam(ia) and (ib == ob or _inside(src, ia, ib)):
            body = (f"但「{a['name']}」写在{_lambda_at(src, ia)}里，要排队调度或被回调时才另外执行，"
                    f"「{b['name']}」在外层代码里当场执行")
        else:
            da = f"在{_lambda_at(src, ia)}里" if lam(ia) else "当场执行"
            db = f"在{_lambda_at(src, ib)}里" if lam(ib) else "当场执行"
            body = f"但「{a['name']}」{da}，「{b['name']}」{db}，两处分别在被调度或回调时执行"
        out.append((b["id"], head + body + "，执行时机不同，两点的先后和间隔不一定是这一段真正的用时，请核对"))
    return out


def _check_one(proj, s, branch=""):
    if not s.get("loc_ok", True) or not s.get("file") or not s.get("line"):
        return
    src = proj.file(s["file"])
    line = int(s["line"])
    if src is None or not 1 <= line <= len(src.lines):
        return
    where = str(s.get("where") or "").strip().lower()
    once = str(s.get("once") or "").strip().lower()
    t = check_branch(src, line, where)
    if t:
        kw = _CTRL_AFTER.match(src.clines[line - 1].strip()).group(1).split()[0]
        head = t.split("。识别结果没有说明")[0]
        # 事件链写明了是哪一支：按有意放进这一支处理，只提醒核对。循环、switch、没大括号、看着像失败分支的照旧算失败
        if branch and kw in ("if", "else") and "失败或出错" not in t and "改掉原来的逻辑" not in t:
            s["chk"].append({"level": "tip", "kind": "branch",
                             "text": f"{head}。事件链写明放在「{branch}」这一支，码表按有意放入处理，请核对这一支是不是每轮正常要走的"})
        else:
            s["chk"].append({"level": "fail", "kind": "branch", "text": t})
    t = check_repeat(proj, s["file"], src, line, where, once)
    if t:
        s["chk"].append({"level": "tip", "kind": "repeat", "text": t})
    t = check_ifdef(proj, src, line)
    if t:
        s["chk"].append({"level": "tip", "kind": "ifdef", "text": t})


def _chain_branches(chain):
    """事件链里每一步写的分支：{第几步: 分支说明}。旧结果没有事件链就是空的。"""
    out = {}
    for i, c in enumerate(chain if isinstance(chain, list) else []):
        if not isinstance(c, dict):
            continue
        try:
            n = int(str(c.get("n", i + 1)).strip())
        except (TypeError, ValueError):
            continue
        b = str(c.get("branch") or "").strip()
        if b and b not in ("空", "无", "没有", "-"):
            out[n] = b
    return out


def check(root, steps, segments=None, side_segments=None, chain=None):
    """逐个计时点做结构检查，结果写进 step["chk"]（[{level: fail/tip, kind, text}]）和 step["struct_ok"]。
    有硬失败的点可信度降成"低"。可以重复调用（缓存、沿用的结果也再查一遍），每次重新算，不会叠加。"""
    proj = Project(root)
    branches = _chain_branches(chain)
    for s in steps:
        s["chk"] = []
        try:
            _check_one(proj, s, branches.get(s.get("chain_step"), ""))
        except Exception as e:  # 检查自己出错不能挡住识别：说清楚这一步没查完
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表结构检查这一步没做完（{type(e).__name__}），请自己核对"})
    by = {s.get("id"): s for s in steps}
    try:
        found = check_segments(proj, steps, [g for g in list(segments or []) + list(side_segments or []) if isinstance(g, dict)])
    except Exception as e:
        found = []
        for s in steps[-1:]:
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表检查各段两头执行时机时出错（{type(e).__name__}），请自己核对"})
    for sid, t in found:
        if not any(c["text"] == t for c in by[sid]["chk"]):
            by[sid]["chk"].append({"level": "tip", "kind": "timing", "text": t})
    for s in steps:
        s["struct_ok"] = not any(c["level"] == "fail" for c in s["chk"])
        if not s["struct_ok"]:
            s["sure"] = "低"
    return steps
