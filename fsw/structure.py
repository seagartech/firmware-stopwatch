"""识别后的结构检查：所有大模型共用，只看源码结构，不看是哪个模型、哪个固件。

大模型给的位置先由 identify.check_location 核对"这一行在不在"；这里再看这一行插下去以后落在什么结构里：
- 硬失败：参照行不是一句真正的语句——只有 { 或 } 的行、else / } else … 行、条件或循环头
  （if / while / for / switch / case）之前、预处理行（#if / #else / #endif），或者落在一句没写完的话中间；
  插在 if / else if / else / while / for / do / switch 这一行"之后"，点会落进分支或循环体，只在条件成立
  （或每循环一次）时才记到——事件链写明了是哪一支的 if / else 例外，按有意放进这一支第一句之前处理，只提醒。
- 提示（判断不一定准，只提醒、不拦）：
  · 一轮记每一次（once=every）的点在循环里，或所在函数在别处的循环里被反复调用（比如逐帧处理）；
  · 写成一轮只经过一次（once=once）的点直接在循环体里（不在里面某一支里）；
  · 旧写法插在判断或循环开头之前：点在整个判断前面、不在任何一支里；
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
    cont, heads = False, {}
    for k, ln in enumerate(lines):
        if cont or ln.lstrip().startswith("#"):
            if not cont:
                heads[k] = k
            cont = ln.rstrip().endswith("\\")
            lines[k] = " " * len(ln)
    _pp_branches(text.split("\n"), lines, heads)
    return "\n".join(lines)


_PP = re.compile(r"\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$")


def _pp_branches(raw, lines, heads):
    """编译开关（#if / #elif / #else）几支里的大括号对不上时，数括号会乱：
    - #if 0 那一支本来就不编译，整支当空白；
    - 同一组有好几支（#else / #elif），其中有一支多开（或多关）了括号（比如两支各写一个 `if (…) {`、共用后面一个 }）：
      几支只会编进去一支，只留第一支（#if 那一支），其余几支当空白——相当于按其中一种配置看，括号就对上了。
    每一支里括号都自己配好的照原样都留着（检查照样能看到每一支里的代码）。只改 lines（去注释后的代码），原文和行号不变。"""
    stack = []
    for k in sorted(heads):
        s = raw[k].strip()
        j = k
        while s.endswith("\\") and j + 1 < len(raw):
            j += 1
            s = s[:-1] + " " + raw[j].strip()
        m = _PP.match(s)
        if not m:
            continue
        d, rest = m.group(1), re.sub(r"/\*.*?\*/|//.*$", "", m.group(2)).strip()
        if d in ("if", "ifdef", "ifndef"):
            stack.append({"zero": d == "if" and rest in ("0", "(0)"), "parts": [], "open": j + 1})
        elif d in ("elif", "else") and stack:
            stack[-1]["parts"].append((stack[-1]["open"], k))
            stack[-1]["open"] = j + 1
        elif d == "endif" and stack:
            f = stack.pop()
            parts = f["parts"] + [(f["open"], k)]
            delta = [sum(lines[i].count("{") - lines[i].count("}") for i in range(a, b)) for a, b in parts]
            blank = parts[:1] if f["zero"] else parts[1:] if len(parts) > 1 and any(delta) else []
            for a, b in blank:
                for i in range(a, b):
                    lines[i] = " " * len(lines[i])


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
        self.balanced = True  # 大括号能一一配上（多出来的 } 或没关上的 {：数括号说不准，结构检查不拦、记"没核对"）
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
                else:
                    self.balanced = False
                last = i + 1
            elif ch == ";" and paren == 0:
                last = i + 1
        if stack:
            self.balanced = False

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


_ELSE = re.compile(r"^(?:\}\s*)?else\b")
_UNFINISHED = re.compile(r"(?:,|\(|&&|\|\||[^=!<>]=)$")  # 这一句还没写完，下一行接着写


def _next_code(src, line):
    """第 line 行后面第一行有代码的（行号, 去掉注释的代码），没有就 (None, "")。"""
    for k in range(line, len(src.clines)):
        if src.clines[k].strip():
            return k + 1, src.clines[k].strip()
    return None, ""


def _prev_code(src, line):
    for k in range(line - 2, -1, -1):
        if src.clines[k].strip():
            return src.clines[k].strip()
    return ""


def check_anchor(src, line, where):
    """参照句是不是一句真正的语句：插点以"插在这一句之前、和它同一层"为主。返回说明（硬失败）或 None。
    插在 if / else 这一行之后的不在这里判，交给 check_branch（事件链写明分支时按有意放入处理）。"""
    raw = src.lines[line - 1].strip()
    c = src.clines[line - 1].strip()
    code = _cond_text(raw)
    if raw.startswith("#"):
        return (f"参照的第 {line} 行「{code}」是编译开关（预处理）行，不是一句语句，点落在开关哪一边说不清。"
                "请把参照句改成开关里面或外面那一句真正的语句")
    if not c:
        return None  # 空行、注释：插在哪儿都一样，不拦
    if c == ";":
        return (f"参照的第 {line} 行只有一个分号（空语句，常是一个循环的循环体），插在它前面会变成那个循环的循环体、改掉原来的逻辑。"
                "请换成循环外面那一句真正的语句")
    if re.fullmatch(r"[{}]+;?", c):
        return (f"参照的第 {line} 行「{code}」只有大括号，插在它{'之后会出了这个代码块（比如出了某一支或循环）' if where == 'after' else '之前落在块的末尾'}，"
                "想放在哪一层说不清。要放在块里，参照句写块里最后一句、where 写 after；要放在块外，参照句写块后面那一句、where 写 before")
    if _ELSE.match(c) and where != "after":
        return (f"插在「{code}」这一行之前，会落进上一支的末尾，不是这一支，分支容易读反。"
                "要放进这一支，参照句写这一支大括号里的第一句、where 写 before")
    if re.match(r"(?:case\b|default\s*:)", c) and where != "after":
        return (f"插在「{code}」这一行之前，会落进上一个 case 的末尾，不是这个 case。"
                "要放进这个 case，参照句写它下面的第一句、where 写 before")
    if where == "after" and _UNFINISHED.search(c) and not _CTRL_AFTER.match(c):
        return f"第 {line} 行「{code}」这一句还没写完（下一行接着写），插在它后面会把一句话拆开。请换成这一整句的第一行、where 写 before"
    if where != "after" and _UNFINISHED.search(_prev_code(src, line)):
        return f"第 {line} 行「{code}」是上一行那句话的接续，插在它前面会把一句话拆开。请换成这一整句的第一行"
    return None


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
    if once not in ("every", "once"):
        return None
    at = src.pos(line, where)
    loops, inner, _ = src.context(at)
    if once == "once":  # 一轮只经过一次：直接写在循环体里（不在里面某一支里）才提醒
        if loops and src.chain(at)[:1] == loops[:1]:
            b = src.blocks[loops[0]]
            return (f"这一步写成一轮只经过一次，但它直接在第 {src.line_of(b['open'])} 行「{_cond_text(b['head'])}」这个循环体里，"
                    "每循环一次都会走到；如果只有某一支才是这一次，请放进那一支")
        return None
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



def _calls(src, line, name):
    """第 line 行有没有调用函数 name（只看函数名最后一段，比如 A::Send → Send）。"""
    short = re.split(r"::|\.", str(name or ""))[-1].strip()
    return bool(short) and re.search(rf"(?<![\w]){re.escape(short)}\s*\(", src.clines[line - 1]) is not None


def _same_thing(proj, a, b):
    """两点是不是同一件事的两处：同一步、同一行，或一处那一行调用另一处所在的函数（调用和被调用，直接或隔一层）。"""
    if a.get("chain_step") is not None and a.get("chain_step") == b.get("chain_step"):
        return True
    if a.get("file") == b.get("file") and str(a.get("line")) == str(b.get("line")):
        return True
    sa, sb = proj.file(a["file"]), proj.file(b["file"])
    if sa is None or sb is None:
        return False
    la, lb = int(a["line"]), int(b["line"])
    fa, fb = func_at(sa, la)[0], func_at(sb, lb)[0]
    if (fb and _calls(sa, la, fb)) or (fa and _calls(sb, lb, fa)):
        return True
    return bool((fb and _calls_via(proj, sa, la, fb)) or (fa and _calls_via(proj, sb, lb, fa)))


_NOT_CALLS = {"if", "while", "for", "switch", "return", "sizeof", "catch", "defined"}


def _short(name):
    return re.split(r"::|\.", str(name or ""))[-1].strip()


def _calls_via(proj, src, line, name):
    """第 line 行调用的某个函数，它的函数体里又直接调用了 name（隔一层的调用和被调用，比如通用输出函数里再调具体板子的写函数）。"""
    target = _short(name)
    called = [n for n in re.findall(r"(?<![\w])([A-Za-z_]\w*)\s*\(", src.clines[line - 1]) if n not in _NOT_CALLS and n != target]
    if not target or not called:
        return False
    cache = proj.__dict__.setdefault("_via", {})
    for mid in dict.fromkeys(called):
        key = (mid, target)
        if key not in cache:
            cache[key] = False
            pat_mid, pat_t = re.compile(rf"(?<![\w]){re.escape(mid)}\s*\("), re.compile(rf"(?<![\w]){re.escape(target)}\s*\(")
            for rel in proj.sources():
                try:
                    raw = (proj.root / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if not (pat_mid.search(raw) and pat_t.search(raw)):
                    continue
                f = proj.file(rel)
                if f is None:
                    continue
                for bl in f.blocks:
                    if bl["kind"] == "function" and _short(bl["name"]) == mid and pat_t.search(f.code[bl["open"]:bl["close"]]):
                        cache[key] = True
                        break
                if cache[key]:
                    break
        if cache[key]:
            return True
    return False


# ---------- 码表自己找：全部放完那一支、每段输出都经过的那一句 ----------
def real_stmt(src, line):
    """这一行能不能当插点参照：一句真正的语句（不是判断或循环开头、else/case、只有分号或大括号、编译开关、上一句的接续）。"""
    if src is None or not 1 <= line <= len(src.clines):
        return False
    c = src.clines[line - 1].strip()
    if not c or c == ";" or re.fullmatch(r"[{}]+;?", c) or src.lines[line - 1].strip().startswith("#"):
        return False
    if re.match(r"(?:\}\s*)?(?:else\b|if\b|while\b|for\b|switch\b|do\b|case\b|default\s*:)", c):
        return False
    return not _UNFINISHED.search(_prev_code(src, line))


def stmt_start(src, line):
    """第 line 行所在那一整句的第一行（上一行没写完就一直往上找）。"""
    k = line
    while k > 1 and _UNFINISHED.search(_prev_code(src, k)):
        k -= 1
        while k > 1 and not src.clines[k - 1].strip():
            k -= 1
    return k


def _blocks(src, line):
    return src.chain(src.pos(line, "before"))


def _first_in_block(src, bi):
    b = src.blocks[bi]
    start, end = src.line_of(b["open"]), src.line_of(b["close"])
    for k in range(start + (1 if src.clines[start - 1].rstrip().endswith("{") else 0), end + 1):
        c = src.clines[k - 1].strip()
        if c and not re.fullmatch(r"\{", c):
            return k if k < end or c != "}" else None
    return None


def _in_func(src, line):
    return func_at(src, line)[0] is not None or bool(func_at(src, line)[1])


def _sites(proj, pattern, word, skip=None):
    """项目里匹配 pattern 的行（在函数里、不是函数定义那一行、不是判断或循环开头）：[(文件, 行)]。"""
    rx = re.compile(pattern)
    out = []
    for rel in proj.sources():
        try:
            raw = (proj.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if word not in raw:
            continue
        src = proj.file(rel)
        if src is None:
            continue
        for k, c in enumerate(src.clines, 1):
            if not rx.search(c) or (skip and skip.search(c)) or _CTRL_AFTER.match(c.strip()):
                continue
            if any(src.blocks[i]["kind"] == "function" for i in _opened_on(src, k)) or not _in_func(src, k):
                continue
            out.append((rel, k))
    return out


def _pick(proj, sites, prefer):
    """几处里挑一处：只有一处就用它；不然用和 prefer（文件, 函数名）同一个函数里的那一处；还挑不出就 None。"""
    if len(sites) == 1:
        return sites[0]
    if prefer:
        same = [x for x in sites if x[0] == prefer[0] and func_at(proj.file(x[0]), x[1])[0] == prefer[1]]
        if len(same) == 1:
            return same[0]
    return None


_ASSIGN = re.compile(r"^(?:[\w:<>]+\s+)*([A-Za-z_]\w*)\s*=(?!=)")


_EXIT = re.compile(r"(?:return|break|continue|goto)\b")


def _not_done_branch(src, bi):
    """这一支是不是"还没做完、还有数据"那条路：条件是取反或出错一类，或者大括号里只有 return/break/continue 这类提前退出。"""
    b = src.blocks[bi]
    m = re.match(r"(?:\}\s*)?(?:else\s+)?if\s*\((.*)\)\s*$", b["head"].strip())
    if m and _FAIL_COND.search(m.group(1)):
        return True
    start, end = src.line_of(b["open"]), src.line_of(b["close"])
    body = [src.clines[k - 1].strip() for k in range(start, end + 1)]
    body = [re.sub(r"^.*\{\s*$", "", x) if k == 0 else x for k, x in enumerate(body)]
    code = [x.strip("{} ") for x in body if x.strip("{} ")]
    return bool(code) and all(_EXIT.match(x) for x in code)


def _after_block(src, header_line):
    """第 header_line 行开头的整个判断（含 else 各支）后面的第一句：(行号) 或 None（后面就是函数结尾）。"""
    end = _block_end(src, header_line)
    if not end:
        return None
    k, c = _next_code(src, end)
    return k if k and c != "}" else None


def locate_done(proj, rel, line, prefer=None, depth=0):
    """从流程里标了全部放完的那一行出发，顺着通知往回找判断出放完的那一支里的那一句：
    转发事件位的处理 → 置这个事件位的地方；回调 lambda 里 → 调用这个回调的地方；
    把判断结果存进变量 → 紧接着检查这个变量的那一支；在一个只被一处调用的函数里（或这里只是返回）→ 调用它的地方。
    "还没放完、还有数据"那一支（条件取反、出错，或者只有 return 这类提前退出）不进、不选，跳到这个判断后面接着找；
    return/break/continue 这类不当插点。最后停在放完那一支里的一句真正的语句。找不准就返回 None（不改模型的答案）。"""
    src = proj.file(rel)
    if depth > 8 or src is None or not line or not 1 <= line <= len(src.clines):
        return None
    c = src.clines[line - 1].strip()

    def callers():
        inner = next((i for i in _blocks(src, line) if src.blocks[i]["kind"] in ("function", "lambda")), None)
        if inner is None or src.blocks[inner]["kind"] != "function":
            return None
        short = _short(src.blocks[inner]["name"])
        got = _pick(proj, [x for x in _sites(proj, rf"(?<![\w]){re.escape(short)}\s*\(", short) if x != (rel, line)], prefer)
        return locate_done(proj, got[0], got[1], prefer, depth + 1) if got else None

    def skip_branch(header_line):  # 不进这一支：到整个判断后面去；后面没有了就是这个函数做完了 → 调用它的地方
        k = _after_block(src, header_line)
        return locate_done(proj, rel, k, prefer, depth + 1) if k else callers()

    if re.match(r"(?:\}\s*)?(?:else\s+)?if\b", c) and c.endswith("{"):
        bi = next((i for i in _opened_on(src, line) if src.blocks[i]["kind"] == "branch"), None)
        if bi is None:
            return None
        if _not_done_branch(src, bi):
            return None if re.match(r"\}", c) else skip_branch(line)
        k = _first_in_block(src, bi)
        return locate_done(proj, rel, k, prefer, depth + 1) if k else None
    chain = _blocks(src, line)
    branch = chain[0] if chain and src.blocks[chain[0]]["kind"] == "branch" else None
    if branch is not None and _not_done_branch(src, branch):  # 在"还没放完"那一支里：跳到这个判断后面
        hl = src.line_of(src.blocks[branch]["open"])
        return None if re.match(r"\}", src.clines[hl - 1].strip()) else skip_branch(hl)
    if _EXIT.match(c):  # 只是返回、跳出：不当插点，到调用它的地方去
        return callers()
    for bi in chain:  # 事件位：if (位 & 某个常量) 那一支 → 置这个常量的那一句
        b = src.blocks[bi]
        if b["kind"] != "branch":
            continue
        for name in re.findall(r"&\s*([A-Z][A-Z0-9_]{3,})\b|\b([A-Z][A-Z0-9_]{3,})\s*&(?!&)", b["head"]):
            name = name[0] or name[1]
            sites = [x for x in _sites(proj, rf"\w\s*\([^;]*\b{name}\b", name) if x != (rel, line)]
            got = _pick(proj, sites, None)
            if got:
                return locate_done(proj, got[0], got[1], prefer, depth + 1)
    inner = next((i for i in chain if src.blocks[i]["kind"] in ("function", "lambda")), None)
    if inner is not None and src.blocks[inner]["kind"] == "lambda":  # 回调 lambda → 调用这个回调的地方
        m = re.search(r"([A-Za-z_]\w*)\s*=\s*\[[^\]]*\]\s*\([^)]*\)[^=]*$", src.blocks[inner]["head"])
        if not m:
            return None
        got = _pick(proj, _sites(proj, rf"(?:\.|->)\s*{m.group(1)}\s*\(", m.group(1), re.compile(rf"\b{m.group(1)}\s*=")), prefer)
        return locate_done(proj, got[0], got[1], prefer, depth + 1) if got else None
    a = _ASSIGN.match(c)
    if a and chain:  # 判断结果存进变量 → 同一块里紧接着检查它的那一支
        end = src.line_of(src.blocks[chain[0]]["close"])
        for k in range(line + 1, end):
            ck = src.clines[k - 1].strip()
            if re.match(r"if\b", ck) and re.search(rf"\b{a.group(1)}\b", ck) and ck.endswith("{"):
                return locate_done(proj, rel, k, prefer, depth + 1)
    if branch is not None:
        return (rel, line) if real_stmt(src, line) else None
    return callers()


def _class_of(head):
    m = re.search(r"(\w+)::~?\w+\s*\(", head or "")
    return m.group(1) if m else None


def _bases(proj, cls, seen=None):
    seen = seen if seen is not None else set()
    if not cls or cls in seen:
        return seen
    seen.add(cls)
    for rel, k in _sites_any(proj, rf"\b(?:class|struct)\s+{cls}\b[^;{{]*:", cls):
        src = proj.file(rel)
        for b in re.findall(r"(?:public|protected|private)?\s*(\w+)\s*(?:,|\{|$)", src.clines[k - 1].split(":", 1)[1]):
            _bases(proj, b, seen)
    return seen


def one_of_impls(proj, src, line):
    """这一行在某个类的一个方法里，而项目里还有别的类写了同名方法（同一个接口的几种实现，运行时只走其中一种）：
    返回 (类名, 方法名, [别的类])；不是就返回 None。"""
    head = ""
    for i in src.chain(src.pos(line, "before")):
        if src.blocks[i]["kind"] == "function":
            head, name = src.blocks[i]["head"], src.blocks[i]["name"]
            break
    cls = _class_of(head)
    if not cls:
        return None
    others = set()
    for rel, k in _sites_any(proj, rf"\b\w+::{re.escape(name)}\s*\(", name):
        o = proj.file(rel)
        for b in o.blocks:
            if b["kind"] == "function" and b["name"] == name and o.line_of(b["open"]) in (k, k + 1, k + 2):
                c2 = _class_of(b["head"])
                if c2 and c2 != cls:
                    others.add(c2)
    return (cls, name, sorted(others)) if others else None


def _sites_any(proj, pattern, word):
    rx = re.compile(pattern)
    out = []
    for rel in proj.sources():
        try:
            raw = (proj.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if word not in raw or not rx.search(raw):
            continue
        src = proj.file(rel)
        out += [(rel, k) for k, c in enumerate(src.clines, 1) if rx.search(c)]
    return out


def locate_output(proj, rel, line):
    """逐段反复执行的输出：从流程里标了输出的那一行往外找，直到落在逐段循环里的那一句（每段输出都经过的那一句）。
    外层调用只有一处才往外走（类的方法：同一个类或它的父类里不带对象直接调用的优先）。找不准返回 None。"""
    cur = (rel, line)
    for _ in range(5):
        src = proj.file(cur[0])
        if src is None or not real_stmt(src, cur[1]):
            return None
        loops, inner, _o = src.context(src.pos(cur[1], "before"))
        if loops:
            return cur
        if inner is None or src.blocks[inner]["kind"] != "function":
            return None
        b = src.blocks[inner]
        short, cls = _short(b["name"]), _class_of(b["head"])
        sites = [x for x in _sites(proj, rf"(?<![\w]){re.escape(short)}\s*\(", short) if x != cur]
        plain = [x for x in sites if not re.search(rf"(?:\.|->|::)\s*{re.escape(short)}\s*\(", proj.file(x[0]).clines[x[1] - 1])]
        if cls:
            fam = _bases(proj, cls)
            mine = [x for x in plain if _class_of(_func_head(proj, x)) in fam]
            objs = [x for x in sites if x not in plain]
            sites = mine or objs
        if len(sites) != 1:
            return None
        cur = sites[0]
    return None


def _func_head(proj, at):
    src = proj.file(at[0])
    for i in src.chain(src.pos(at[1], "before")):
        if src.blocks[i]["kind"] == "function":
            return src.blocks[i]["head"]
    return ""


def check_order(proj, steps, segments):
    """一段从某处的"最后一次"（last）连到同一件事另一处的"第一次"（first）：比如同一次发送的外层一句和它调用的那一句各放了 first、last，
    外层末 → 内层首这一段是负的。返回 [(段的终点 id, 说明)]。"""
    by = {s["id"]: s for s in steps}
    out = []
    for g in segments or []:
        if not isinstance(g, dict):
            continue
        a, b = by.get(g.get("from")), by.get(g.get("to"))
        if not a or not b or str(a.get("once")).lower() != "last" or str(b.get("once")).lower() != "first":
            continue
        if not all(x.get("file") and x.get("line") and x.get("loc_ok", True) for x in (a, b)) or not _same_thing(proj, a, b):
            continue
        seg = f"「{g.get('name')}」这一段" if g.get("name") else "这一段"
        out.append((b["id"], f"{seg}从「{a.get('name')}」（一轮里最后一次）连到「{b.get('name')}」（一轮里第一次），两处是同一件事（同一句，或调用和被调用）："
                             "第一次早于最后一次，这一段是负的。同一次发送只在一处放 first、last"))
    return out


def _uncertain(text):
    return any(w in str(text or "") for w in ("不确定", "说不准", "不清楚"))


def check_uncertain(steps, segments, by_n):
    """先后说不准的两步被串成了前后相连的一段：两头都标了最后结果，或者后一步写明和上一步先后不确定、前一头正是那上一步。
    这样的段可能是负的，应各自从前面同一个点量起。返回 [(段的终点 id, 说明)]。"""
    by = {s["id"]: s for s in steps}
    out = []
    for g in segments or []:
        if not isinstance(g, dict):
            continue
        a, b = by.get(g.get("from")), by.get(g.get("to"))
        if not a or not b:
            continue
        na, nb = a.get("chain_step"), b.get("chain_step")
        ca, cb = by_n.get(na), by_n.get(nb)
        if not ca or not cb or na == nb:
            continue
        # 两步都标了最后结果（比如结束消息和放空，说不准谁先）。放空和它引起的收尾这种有因果先后的，只有一头标最后结果，不算
        both_last = ("最后结果" in str(ca.get("role") or "") and "最后结果" in str(cb.get("role") or "")
                     and str(a.get("once") or "").lower() != "first")  # 从第一次出发的不算（第一段一定在前）
        unsure = _uncertain(cb.get("after")) and isinstance(na, int) and isinstance(nb, int) and na == nb - 1
        if not both_last and not unsure:
            continue
        why = "两步都标了最后结果" if both_last else f"流程里写着第 {nb} 步和上一步谁先谁后不确定"
        seg = f"「{g.get('name')}」这一段" if g.get("name") else "这一段"
        out.append((b["id"], f"{seg}从「{a.get('name')}」到「{b.get('name')}」：{why}，先后说不准，这一段可能是负的。"
                             "这两个点应各自从前面同一个点量起，不要串成前后相连的段"))
    return out


# ---------- 两步识别：事件链每一步写的函数、计时点在不在链里那一步 ----------
def _line_at(src, line):
    """这一行第一个真正的字（跳过开头的空白和 }，免得算进上一支）的位置。"""
    k = line - 1
    c = src.clines[k]
    j = len(c) - len(c.lstrip(" \t}"))
    return src.starts[k] + min(j, len(c))


def _opened_on(src, line):
    """在这一行打开的块（比如 `} else if (x) {`、函数头）。"""
    a, b = src.starts[line - 1], src.starts[line - 1] + len(src.lines[line - 1])
    return [i for i, blk in enumerate(src.blocks) if a <= blk["open"] < b]


def func_at(src, line):
    """包住这一行的函数：(函数名, 途经的 lambda 块)。按大括号层级往外找；找不到函数返回 (None, [])。"""
    lambdas = []
    for i in src.chain(_line_at(src, line)):
        b = src.blocks[i]
        if b["kind"] == "lambda":
            lambdas.append(i)
        elif b["kind"] == "function":
            return b["name"], lambdas
    for i in _opened_on(src, line):  # 这一行就是函数头
        if src.blocks[i]["kind"] == "function":
            return src.blocks[i]["name"], []
    return None, lambdas


def check_func(src, line, func):
    """事件链这一步写的函数对不对：返回 None（对得上）、("tip", 说明)（判断不了，放行）或 ("fail", 说明)。"""
    words = set(re.findall(r"[A-Za-z_~]\w*", str(func or "")))
    name, lambdas = func_at(src, line)
    if not name:
        return ("tip", f"第 {line} 行码表没找到包住它的函数（可能是宏或预处理把大括号拆开了），写的「{func}」没法核对，请自己核对")
    if name in words:
        return None
    if lambdas:  # 在 lambda 或回调里：写交给谁的、回调名，都可能说得通
        names = {src.blocks[i]["name"] for i in lambdas if src.blocks[i]["name"]}
        if names & words:
            return None
        return ("tip", f"第 {line} 行在 {name} 里的一个 lambda 里，写的是「{func}」，码表判断不了是不是同一个回调，请核对")
    there = [src.line_of(b["open"]) for b in src.blocks if b["kind"] == "function" and b["name"] in words]
    more = f"；「{func}」在这个文件第 {there[0]} 行开始，不包含这一行" if there else ""
    return ("fail", f"第 {line} 行在函数 {name} 里，不在写的「{func}」里{more}。请沿真实的调用关系重新追这一步")


def in_step_area(src, chain_line, pos):
    """计时点（位置 pos）是不是落在链里那一步那一行所在的那一支里（含这一行自己打开的块）。判断不了算在。
    链里那一步写在延后执行的回调里（lambda：交给队列、调度、回调登记的），登记这个回调的那一支也算同一支。"""
    inner = src.chain(_line_at(src, chain_line))
    areas = inner[:1] + _opened_on(src, chain_line)
    lam = next((i for i in inner if src.blocks[i]["kind"] == "lambda"), None)
    if lam is not None and src.blocks[lam]["parent"] is not None:
        areas.append(src.blocks[lam]["parent"])
    if not areas:
        return True
    return any(src.blocks[i]["open"] < pos <= src.blocks[i]["close"] for i in areas)


_NOT_CALL = {"if", "while", "for", "switch", "sizeof", "return", "defined"}


_QUERY = re.compile(r"(?i:is|has|get|can|should|check|contains|exists|find|count|size|length|empty|valid)(?:[A-Z0-9]|$)")
_QUERY_NAMES = {"strcmp", "strncmp", "strcasecmp", "memcmp", "strlen", "strstr", "empty", "size", "length", "find", "count"}


def _is_query(name):
    """名字像只查状态、不往外发数据的调用：IsXxx、HasXxx、GetXxx、is_xxx、xxx_IsXxx、strcmp 这一类。"""
    if name.lower() in _QUERY_NAMES:
        return True
    return any(_QUERY.match(part) for part in re.split(r"_+|::", name) if part)


def call_in_if(code):
    """这一行是不是 if，且条件里就有一次真正的动作调用（比如 if (!send(x))）：动作本身写在条件里，点只能放在这一行前面。
    只算带参数、名字不像只查状态的调用（发送、写出数据这类）；IsXxx()、HasXxx()、strcmp(...) 这种只查状态的不算。"""
    m = re.match(r"if\s*\((.*)", code.strip())
    if not m:
        return False
    for call in re.finditer(r"([A-Za-z_]\w*)\s*\(\s*([^)\s])?", m.group(1)):
        name, arg = call.group(1), call.group(2)
        if name not in _NOT_CALL and arg and not _is_query(name):
            return True
    return False


def _check_one(proj, s, branch="", strict=False, chain_line=None):
    if not s.get("loc_ok", True) or not s.get("file") or not s.get("line"):
        return
    src = proj.file(s["file"])
    line = int(s["line"])
    if src is None or not 1 <= line <= len(src.lines):
        return
    where = str(s.get("where") or "").strip().lower()
    once = str(s.get("once") or "").strip().lower()
    t = check_anchor(src, line, where)
    if t:
        s["chk"].append({"level": "fail", "kind": "anchor", "text": t})
    elif where == "before" and line == chain_line and call_in_if(src.clines[line - 1]):
        # 流程里这一步就是这一行，而且动作（发送、调用）本身写在 if 条件里：只能放在这一行前面，不算错
        s["chk"].append({"level": "tip", "kind": "anchor",
                         "text": f"第 {line} 行「{_cond_text(src.lines[line - 1].strip())}」的动作写在 if 条件里，点只能放在这一行前面，记到的是调用之前那一刻"})
    elif where == "before" and s.get("lifted"):
        # 原来那一句只在按设置选的某一支里（比如某个协议版本），码表挪到了整个判断前面：每种设置都会经过
        s["chk"].append({"level": "tip", "kind": "branch", "text": s["lifted"]})
    elif where == "before" and re.match(r"(?:if|while|for|switch|do)\b", src.clines[line - 1].strip()):
        # 旧写法：插在判断或循环开头之前。旧结果、演示数据只提醒；两步识别的新结果算失败（多半是想放进这一支、这个循环里）
        s["chk"].append({"level": "fail" if strict else "tip", "kind": "anchor",
                         "text": f"参照的第 {line} 行「{_cond_text(src.lines[line - 1].strip())}」是条件或循环的开头：点放在整个判断前面，"
                                 "不在任何一支里。要放进某一支，参照句应写那一支大括号里的第一句"})
    alt = None if s.get("lifted") else alt_branch(src, line)
    if alt:
        s["chk"].append({"level": "fail" if strict else "tip", "kind": "branch", "text": alt_text(src, line, alt)})
    t = None if t else check_branch(src, line, where)
    if t:
        kw = _CTRL_AFTER.match(src.clines[line - 1].strip()).group(1).split()[0]
        head = t.split("。识别结果没有说明")[0]
        n, first = _next_code(src, line)
        first = f"这一支第一句「{_cond_text(src.lines[n - 1].strip())}」之前" if n and first != "}" else "这一支里（这一支是空的）"
        # 事件链写明了是哪一支：等于插在这一支第一句之前，按有意放入处理，只提醒核对。循环、switch、没大括号、看着像失败分支的照旧算失败
        if branch and kw in ("if", "else") and "失败或出错" not in t and "改掉原来的逻辑" not in t:
            s["chk"].append({"level": "tip", "kind": "branch",
                             "text": f"{head}，等于插在{first}。事件链写明放在「{branch}」这一支，码表按有意放入处理，请核对这一支是不是每轮正常要走的"})
        else:
            s["chk"].append({"level": "fail", "kind": "branch",
                             "text": t + "。要放进这一支，参照句写这一支大括号里的第一句、where 写 before"})
    t = None if s.get("every_why") else check_repeat(proj, s["file"], src, line, where, once)  # 码表有意设成每次都记的（全部放完）：不提醒
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


def _check_chain_step(proj, s, steps_by_n):
    """两步识别的新结果：点必须对应链里的一步，并且插在那一步那一行或它所在的那一支里。返回说明或 None。"""
    if s.get("line_of") == "side":
        return None  # 顺带发现的线：本来就不在事件链里，不要求
    n = s.get("chain_step")
    if n is None:
        return "没写对应理清的流程里第几步（chain_step）。两步识别里计时点只能从流程里选"
    c = steps_by_n.get(n)
    if c is None:
        return f"理清的流程里没有第 {n} 步，这个点不是从流程里选的"
    if not s.get("loc_ok", True) or not s.get("file") or not s.get("line"):
        return None
    if s["file"] != c.get("file"):
        return f"流程第 {n} 步在 {c.get('file')}，这个点却插在 {s['file']}，不是那一步的位置"
    src = proj.file(s["file"])
    line, cl = int(s["line"]), int(c.get("line") or 0)
    if src is None or not 1 <= line <= len(src.lines) or not 1 <= cl <= len(src.lines) or line == cl:
        return None
    if not in_step_area(src, cl, src.pos(line, str(s.get("where") or "").strip().lower())):
        return f"流程第 {n} 步在第 {cl} 行，这个点插在第 {line} 行，不在那一行所在的那一支里"
    return None


def _check_side_on_role(s, by_n, steps):
    """流程里标了角色（上传首包、首个结果、输出……）的那一步只有旁线上的点：那一步是主线，点不能都写成旁线。
    这一步已经有主线点的，旁线从这里分出去（比如收到结果后顺带换表情）是正常的。"""
    if s.get("line_of") != "side" or any(x.get("chain_step") == s.get("chain_step") and x.get("line_of") in ("start", "main") for x in steps):
        return None
    c = by_n.get(s.get("chain_step")) or {}
    role = c.get("role")
    role = "、".join(map(str, role)) if isinstance(role, list) else str(role or "").strip()
    if not role:
        return None
    return f"流程第 {s.get('chain_step')} 步标了{role}，是主线上的一步，这一步的点却都写成了旁线（line_of side）：line_of 要写 main，并按先后接进主线的段里"


def chain_no_line(c):
    try:
        return int(c.get("line")) if isinstance(c, dict) else None
    except (TypeError, ValueError):
        return None


def _same_line_steps(by_n, n):
    """和第 n 步写在同一文件同一行的所有步骤（含它自己）：同一行拆成几步写的，算一步。"""
    c = by_n.get(n) or {}
    key = (c.get("file"), str(c.get("line")))
    return {k for k, x in by_n.items() if (x.get("file"), str(x.get("line"))) == key} if c.get("file") else {n}


def check_output(steps, by_n):
    """流程里标了输出的那一步：首段、末段输出用它的第一次和最后一次执行，点要有 first 和 last 两个。
    同一文件同一行拆成几步写的算一步。返回 [(点 id, 说明)]。"""
    out, done = [], set()
    for n, c in by_n.items():
        if "输出" not in str(c.get("role") or "") or n in done:
            continue
        group = _same_line_steps(by_n, n)
        done |= group
        mine = [x for x in steps if x.get("chain_step") in group and x.get("line_of") != "side"]
        if not mine:
            continue
        have = {str(x.get("once") or "").strip().lower() for x in mine}
        miss = [w for w in ("first", "last") if w not in have]
        if miss:
            out.append((mine[0].get("id"), f"流程第 {n} 步标了输出，这一步的点缺 once 写 {'、'.join(miss)} 的：首段、末段输出要用它的第一次和最后一次执行，"
                                           "两个点都放在这一步（同一行），并接进段里"))
    return out


_FAIL_COND = re.compile(r"!\s*[\w(]|==\s*(?:NULL|nullptr|false)\b|!=\s*(?:ESP_OK|0)\b|<\s*0\b")


def _enclosing_header(src, line):
    """第 line 行所在的那个大括号块的开头那一行：(行号, 去掉注释的代码)；在最外层就是 (None, "")。"""
    depth = 0
    for k in range(line - 2, -1, -1):
        c = src.clines[k]
        depth += c.count("}") - c.count("{")
        if depth < 0:
            return k + 1, c.strip()
    return None, ""


def _failure_header(src, line):
    """第 line 行是不是在一个看起来是"失败、出错才走"的 if 那一支里（往外找最多三层）。返回那一行 (行号, 原文) 或 None。"""
    cur = line
    for _ in range(3):
        n, c = _enclosing_header(src, cur)
        if not n:
            return None
        m = re.match(r"(?:\}\s*)?(?:else\s+)?if\s*\((.*)\)\s*\{?$", c)
        if m and _FAIL_COND.search(m.group(1)) and not call_in_if(c.lstrip("} ").replace("else ", "", 1)):
            return n, src.lines[n - 1].strip()
        if m and _FAIL_COND.search(m.group(1)) and call_in_if(c.lstrip("} ").replace("else ", "", 1)):
            return n, src.lines[n - 1].strip()  # if (!send(x)) { … }：大括号里是发送失败那一支
        cur = n
    return None


def _block_end(src, line):
    """第 line 行开头的判断或循环，整个块结束那一行的行号（含 else 各支）；找不到就是 None。"""
    depth, opened = 0, False
    for k in range(line - 1, min(len(src.lines), line - 1 + 200)):
        c = src.clines[k]
        depth += c.count("{") - c.count("}")
        opened = opened or "{" in c
        if opened and depth <= 0:
            nxt = _next_code(src, k + 1)
            if nxt[0] and _ELSE.match(nxt[1]):
                continue
            return k + 1
    return None


def _branch_text(src, line, limit=12):
    """判断或循环开头那一行后面那一支：(第一句的行号, 第一句原文, [(行号, 原文)…最多 limit 行])。"""
    n, first = _next_code(src, line)
    if not n or first == "}":
        return None, "", []
    rows, depth = [], 0
    for k in range(line - 1, min(len(src.lines), line - 1 + 60)):
        c = src.clines[k]
        if k >= line:
            rows.append((k + 1, src.lines[k].rstrip()))
        depth += c.count("{") - c.count("}")
        if k >= line - 1 and depth <= 0 and k > line - 1:
            break
        if len(rows) >= limit:
            break
    return n, src.lines[n - 1].strip(), rows


def branch_hint(src, line, step=False):
    """判断或循环开头那一行：告诉模型这一支里有什么、能往哪儿挪。失败那一支、空循环体不当插点。
    step=True 是第一步（理清流程）用的说法：改的是这一步写的那一行，不是插点。"""
    it, put, drop = ("这一步", "这一步的 file、line、code 改成", "去掉这一步上的标记") if step else ("这个点", "line 改成", "去掉这个点")
    how = "（照抄那一行）" if step else "（where 写 before）"
    fail = _failure_header(src, line)
    if fail:
        return (f"它在第 {fail[0]} 行「{_cond_text(fail[1])}」这一支里，这一支是出错、失败才走的，正常每轮走不到："
                f"{drop}，或者放回流程里正常走的那一句")
    c = src.clines[line - 1].strip()
    n, first, rows = _branch_text(src, line)
    after = _block_end(src, line)
    nxt = _next_code(src, after) if after else (None, "")
    tail = f"判断（循环）后面的第一句是第 {nxt[0]} 行「{_cond_text(src.lines[nxt[0] - 1].strip())}」。" if nxt[0] and nxt[1] != "}" else ""
    if not n or first.strip() in (";", "{}") or (re.match(r"(?:while|for)\b", c) and c.rstrip().endswith(";")):
        return f"这个循环体是空的，不能放在里面。{tail}{put}循环外面那一句{how}，或者{drop}"
    m = re.match(r"(?:\}\s*)?(?:else\s+)?if\s*\((.*)\)\s*\{?$", c)
    if m and _FAIL_COND.search(m.group(1)):
        return f"这一支（第 {n} 行起）看起来是出错、失败才走的，正常走的是判断后面。{tail}{put}判断后面的那一句{how}，或者正常那一支里的第一句"
    body = "\n".join(f"    {k}: {t}" for k, t in rows)
    return (f"这一支大括号里的第一句是第 {n} 行「{first}」；这一支的内容：\n{body}\n"
            f"  {put}你要的那一支里的第一句（里面又是判断的，再往里一层）{how}")


def anchor_fix_text(src, s):
    """点插在判断或循环开头那一行：追问时说的话。"""
    line = int(s["line"])
    return (f"{s['id']}「{s.get('name')}」放在 {s['file']} 第 {line} 行「{src.lines[line - 1].strip()}」，"
            f"这是判断或循环的开头，点会落在整个判断外面。" + branch_hint(src, line))


def pick_fixes(root, steps, chain):
    """选点结果里能指出改法的硬失败（插在判断或循环开头那一行、只有分号的那一行、标了输出的那一步缺 first 或 last、
    点不在它对应的那一步那一行或那一支里），返回追问时要说的话；别的硬失败（段是负的……）不在追问里，原样留着、照旧算数。没有能追问的就返回 None。"""
    proj = Project(root)
    by_n = {}
    for i, c in enumerate(chain if isinstance(chain, list) else []):
        if isinstance(c, dict):
            try:
                by_n[int(str(c.get("n", i + 1)).strip())] = c
            except (TypeError, ValueError):
                pass
    items = []
    for s in steps:
        for c in s.get("chk") or []:
            if c["level"] != "fail":
                continue
            if c["kind"] == "anchor" and "是条件或循环的开头" in c["text"]:
                src = proj.file(s["file"])
                if src is not None:
                    items.append(anchor_fix_text(src, s))
            elif c["kind"] == "anchor" and "只有一个分号" in c["text"]:
                src = proj.file(s["file"])
                fail = _failure_header(src, int(s["line"])) if src is not None else None
                why = f"，而且在第 {fail[0]} 行「{_cond_text(fail[1])}」那一支里，是出错、失败才走的" if fail else ""
                items.append(f"{s['id']}「{s.get('name')}」放在 {s['file']} 第 {s['line']} 行，这一行只有一个分号（空循环体）{why}：不能放在这里，"
                             "去掉这个点，或者放回流程里那一步那一行")
            elif c["kind"] == "anchor" and "是上一行那句话的接续" in c["text"]:
                src = proj.file(s["file"])
                if src is not None:
                    k = stmt_start(src, int(s["line"]))
                    items.append(f"{s['id']}「{s.get('name')}」放在 {s['file']} 第 {s['line']} 行，这一行是上一句没写完接着写的，插在这里会把一句话拆开。"
                                 f"这一整句从第 {k} 行「{src.lines[k - 1].strip()}」开始：line 改成 {k}（where 写 before），或者放进别的一句真正的语句")
            elif c["kind"] in ("output", "line_of"):
                items.append(f"{s['id']}：{c['text']}")
            elif c["kind"] == "chain" and ("不是那一步的位置" in c["text"] or "不在那一行所在的那一支里" in c["text"]):
                t = _step_fix_text(proj, s, by_n.get(s.get("chain_step")) or {})
                if t:
                    items.append(t)
    return items or None


def _step_fix_text(proj, s, c):
    """点不在它对应的那一步那一行（文件不对，或跑到了别的函数、别的一支）：追问时告诉它那一步在哪，放回去或去掉。"""
    src = proj.file(c.get("file") or "")
    try:
        cl = int(c.get("line") or 0)
    except (TypeError, ValueError):
        return None
    if src is None or not 1 <= cl <= len(src.lines):
        return None
    code = src.lines[cl - 1].strip()
    head = (f"{s['id']}「{s.get('name')}」放在 {s.get('file')} 第 {s.get('line')} 行，可它对应的流程第 {s.get('chain_step')} 步在 "
            f"{c.get('file')} 第 {cl} 行「{code}」，点不在那一步那一行，也不在那一行所在的那一支里。")
    hl = src.clines[cl - 1].strip()
    if re.match(r"(?:\}\s*)?(?:else\s+)?(?:if|while|for|switch)\b", hl) and hl.endswith("{") and not call_in_if(hl):
        return head + "那一行是判断或循环的开头：" + branch_hint(src, cl)
    return head + f"line 改成 {cl}、file 改成 {c.get('file')}（where 照那一句的意思写 before 或 after）；这个点量的不是这一轮流程里的事，就去掉这个点"


END_ROLES = ("最后结果", "全部放完")


def _end_role(s, by_n):
    c = by_n.get(s.get("chain_step")) or {}
    return any(r in str(c.get("role") or "") for r in END_ROLES)


def _skip_for_end(x, p, by_n):
    """给最后结果、全部放完的点找起点时不能用的点：别的最后结果、全部放完；"最后一次执行"的点；旁线；
    流程里排在它后面的；它写明和上一步先后不确定时，紧挨着的上一步。"""
    if x is None or x.get("line_of") not in ("start", "main") or x is p:
        return True
    if _end_role(x, by_n) or str(x.get("once") or "").strip().lower() == "last":
        return True
    np, nx = p.get("chain_step"), x.get("chain_step")
    if not (isinstance(np, int) and isinstance(nx, int)) or x.get("line_of") == "start":
        return False
    if nx >= np:
        return True
    # 紧挨着的上一步：流程写明和它先后不确定时不能用（有因果先后的，比如收到结果再开锁，照常用）
    return nx == np - 1 and _uncertain((by_n.get(np) or {}).get("after"))


def _end_base(p, steps, by_n, segs=None, start_from=None):
    """标了最后结果、全部放完的点从哪儿量起：从 start_from（没有就用按先后排在它前面的那个主线点）出发，
    顺着已有的段往回找，跳过不能用的点（见 _skip_for_end）。顺着段找不到时，再按点的先后往前找。找不到就是 None。"""
    by = {x.get("id"): x for x in steps}
    i = next((k for k, x in enumerate(steps) if x is p), None)
    if start_from is None and i is not None:
        start_from = next((x for x in reversed(steps[:i]) if x.get("line_of") in ("start", "main")), None)
    cur, seen = start_from, set()
    while cur is not None and cur.get("id") not in seen:
        seen.add(cur.get("id"))
        if not _skip_for_end(cur, p, by_n):
            return cur
        inc = next((g.get("from") for g in segs or [] if g.get("to") == cur.get("id") and g.get("from") in by), None)
        cur = by.get(inc)
    for x in reversed(steps[:i] if i is not None else []):
        if not _skip_for_end(x, p, by_n):
            return x
    return None


def _kind(s):
    k = str(s.get("once") or "").strip().lower()
    return k if k in ("first", "last") else "once"


def build_segments(steps, segments):
    """要测的这条线的段由码表按点的先后连（模型写的段只拿来用名字、谁在忙、在不在设备外）：
    同一步（或同一行拆成的几步）的"最后一次"从那一句的"第一次"量起；"第一次"不从别处的"最后一次"量起（那样会是负的），往前找最近一个不是"最后一次"的点；
    其余从紧挨着的上一个点量起。先后说不准、最后结果和全部放完从哪儿量，再交给 repair_segments 理。返回 (段, [说明])。"""
    main = [x for x in steps if x.get("line_of") in ("start", "main")]
    model = [g for g in segments or [] if isinstance(g, dict)]
    ids = {x.get("id") for x in steps}
    dropped = {g.get("from") for g in model} - ids  # 模型连过、码表去掉了的点（比如起点之前就一直在跑的流水线中间那几步）
    out, notes = [], []
    for i, p in enumerate(main):
        if i == 0:
            continue
        k = _kind(p)
        a = None
        if k == "last":
            a = next((x for x in main[:i] if _kind(x) == "first" and (
                (x.get("chain_step") == p.get("chain_step") and p.get("chain_step") is not None)
                or (x.get("file") and (x.get("file"), x.get("line"), x.get("where")) == (p.get("file"), p.get("line"), p.get("where"))))), None)  # 同一句的第一次（同一步，或同一行拆成的前一步）
        if a is None and k in ("first", "last"):
            a = next((x for x in reversed(main[:i]) if _kind(x) != "last"), None)
        if a is None:
            a = main[i - 1]
        mine = [g for g in model if g.get("to") == p.get("id")]
        g = next((g for g in mine if g.get("from") == a.get("id")), mine[0] if mine else None)
        gone = g is not None and g.get("from") != a.get("id") and g.get("from") in dropped  # 模型那段的起头点被去掉了：名字只说了后半截
        seg = {"from": a.get("id"), "to": p.get("id"), "name": (not gone and (g or {}).get("name")) or f"{a.get('name')}到{p.get('name')}",
               "who": (g or {}).get("who") or "", "outside": bool((g or {}).get("outside"))}
        if g is None:
            seg["added"] = True
        out.append(seg)
    old = {(g.get("from"), g.get("to")) for g in model}
    new = {(g["from"], g["to"]) for g in out}
    if old != new:
        notes.append(f"要测的这条线的段由码表按点的先后连：{len(new - old)} 段和模型连的不一样")
    return out, notes


def repair_segments(root, steps, segments, chain):
    """两步识别的新结果：码表把主线的段理顺，模型只管选点。
    1. 先后说不准的两步被串成一段（后一步写明和上一步先后不确定、或两步都标了最后结果）：起点换成更前面的点，两头各自从那里量；
    2. 同一次发送的"最后一次"连到"第一次"（负段）：起点换成前一头的上一个点；
    3. 标了最后结果、全部放完的那一步没有段量到它：补一段；
    4. 同一步放了 first、last 两个点，last 那个一段都没连上：补上 first → last。
    最后结果、全部放完的点顺着已有的段往回找起点，跳过紧挨的上一步、"最后一次"的点和别的放完、结束点。
    前面已经没有点可换（比如起点出发的那一段）就原样留着，不删。返回 (新的段, [改了什么])。"""
    proj = Project(root)
    by_n = {}
    for i, c in enumerate(chain if isinstance(chain, list) else []):
        if isinstance(c, dict):
            try:
                by_n[int(str(c.get("n", i + 1)).strip())] = c
            except (TypeError, ValueError):
                pass
    by = {s.get("id"): s for s in steps}
    segs = [dict(g) for g in segments or [] if isinstance(g, dict)]
    notes, kept = [], set()
    for g in [g for g in segs if g.get("from") and g.get("from") == g.get("to")]:
        segs.remove(g)  # 自己连到自己：量不出东西
        notes.append(f"删掉「{g.get('name') or ''}」：两头是同一个点（{g.get('from')}），量不出东西")

    def name(i):
        return (by.get(i) or {}).get("name") or i

    for _ in range(3 * len(segs) + 3):
        k, why, neg = None, "", False
        for j, g in enumerate(segs):
            if id(g) in kept:
                continue
            if check_order(proj, steps, [g]):
                k, why, neg = j, "同一次发送从最后一次连到第一次，是负的", True
            elif check_uncertain(steps, [g], by_n):
                k, why, neg = j, "两步先后说不准", False
            if k is not None:
                break
        if k is None:
            break
        g = segs[k]
        a, b = by.get(g.get("from")), by.get(g.get("to"))
        if b is not None and _end_role(b, by_n):
            base = _end_base(b, steps, by_n, segs, start_from=a)
            new = base.get("id") if base else None
        else:  # 负段：改从前一头的上一个点量起；先后说不准：顺着往回找，直到落在前一头那一步之前的点
            new, na, hops = g.get("from"), (a or {}).get("chain_step"), 0
            while new and (hops == 0 or (not neg and (by.get(new) or {}).get("chain_step") == na)) and hops <= len(segs):
                new = next((x.get("from") for x in segs if x is not g and x.get("to") == new), None)
                hops += 1
        old = g.get("from")
        if not new or new == g.get("to") or new == old:
            kept.add(id(g))  # 前面没有点可换：原样留着（结构检查照旧会提示）
            notes.append(f"「{g.get('name') or ''}」{name(old)}→{name(g.get('to'))}：{why}，但前面没有更早的点可换，原样留着")
            continue
        if any(x is not g and x.get("from") == new and x.get("to") == g.get("to") for x in segs):
            segs.pop(k)
            notes.append(f"删掉「{g.get('name') or ''}」{name(old)}→{name(g.get('to'))}：{why}（从{name(new)}量起的那段已经有了）")
            continue
        g["from"] = new
        notes.append(f"「{g.get('name') or ''}」改成从「{name(new)}」量到「{name(g.get('to'))}」（原来从「{name(old)}」：{why}）")
    ends = {g.get("to") for g in segs}
    seen = set()
    for p in steps:
        n = p.get("chain_step")
        if p.get("line_of") not in ("start", "main") or n in seen or not _end_role(p, by_n):
            continue
        mine = [x for x in steps if x.get("chain_step") == n and x.get("line_of") in ("start", "main")]
        seen.add(n)
        if any(x.get("id") in ends for x in mine):
            continue
        target = mine[-1]
        base = _end_base(target, steps, by_n, segs)
        if base is None:
            continue
        segs.append({"from": base.get("id"), "to": target.get("id"), "name": f"到{target.get('name')}", "who": "", "outside": False, "added": True})
        ends.add(target.get("id"))
        notes.append(f"补上「{base.get('name')}」→「{target.get('name')}」：标了最后结果或全部放完的这一点原来没有段量到它")
    first_out = next((x for x in steps if x.get("line_of") in ("start", "main") and str(x.get("once") or "").lower() == "first"
                      and "输出" in str((by_n.get(x.get("chain_step")) or {}).get("role") or "")), None)
    if first_out is not None and isinstance(first_out.get("chain_step"), int):
        no = first_out["chain_step"]
        for p in steps:  # 最后结果、全部放完：一律从首段输出量起，不顺着模型连的链
            n = p.get("chain_step")
            if p.get("line_of") not in ("start", "main") or not _end_role(p, by_n) or not isinstance(n, int) or n <= no:
                continue
            inc = [g for g in segs if g.get("to") == p.get("id")]
            if inc and all(g.get("from") == first_out["id"] for g in inc):
                continue
            nm = inc[0].get("name") if inc else f"到{p.get('name')}"
            for g in inc:
                segs.remove(g)
            segs.append({**(inc[0] if inc else {"who": "", "outside": False, "added": True}), "from": first_out["id"], "to": p["id"], "name": nm})
            was = "、".join(f"「{name(g.get('from'))}」" for g in inc)
            notes.append(f"「{nm}」改成从「{name(first_out['id'])}」量到「{name(p['id'])}」" + (f"（原来从{was}）" if inc else "（原来没有段量到它）")
                         + "：最后结果、全部放完一律从首段输出量起")
    used = {g.get("from") for g in segs} | {g.get("to") for g in segs}
    for p in steps:
        if p.get("line_of") not in ("start", "main") or p.get("id") in used or str(p.get("once") or "").lower() != "last":
            continue
        first = next((x for x in steps if x is not p and x.get("chain_step") == p.get("chain_step") and x.get("chain_step") is not None
                      and str(x.get("once") or "").lower() == "first" and x.get("id") in used), None)
        if first:
            segs.append({"from": first.get("id"), "to": p.get("id"), "name": f"{first.get('name')}到{p.get('name')}", "who": "", "outside": False, "added": True})
            used.add(p.get("id"))
            notes.append(f"补上「{first.get('name')}」→「{p.get('name')}」：同一步的最后一次原来没有段连上")
    return segs, notes


def check_side_order(steps, side_segments, by_n):
    """旁线段的终点在流程里排在起点前面（两头都对应流程里的步骤，也都没写先后不确定）：先后多半反了，这一段可能是负的。
    只提醒，不改。返回 [(段的终点 id, 说明)]。"""
    by = {s.get("id"): s for s in steps}
    out = []
    for g in side_segments or []:
        if not isinstance(g, dict):
            continue
        a, b = by.get(g.get("from")), by.get(g.get("to"))
        if not a or not b:
            continue
        na, nb = a.get("chain_step"), b.get("chain_step")
        if not (isinstance(na, int) and isinstance(nb, int)) or nb >= na or na not in by_n or nb not in by_n:
            continue
        if any(_uncertain((by_n.get(n) or {}).get("after")) for n in range(nb + 1, na + 1)):
            continue
        seg = f"旁线段「{g.get('name')}」" if g.get("name") else "这一段旁线"
        out.append((b["id"], f"{seg}从「{a.get('name')}」（流程第 {na} 步）连到「{b.get('name')}」（流程第 {nb} 步，排在前面）："
                             "终点在流程里比起点早，先后多半反了，量出来可能是负的，请核对"))
    return out


def check_dangling(steps, segments, by_n):
    """流程里标了最后结果、全部放完的那一步，它的点至少要有一段量到它（作为某一段的终点；先后说不准的从前面同一个点量起）。
    一段都没连上的点量不出来，等于白放。返回 [(点 id, 说明)]，每一步只报一次。"""
    ends = {g.get("to") for g in segments or [] if isinstance(g, dict)}
    seen, out = set(), []
    for s in steps:
        n = s.get("chain_step")
        c = by_n.get(n)
        if not c or n in seen:
            continue
        role = str(c.get("role") or "")
        tag = next((r for r in END_ROLES if r in role), None)
        if not tag:
            continue
        mine = [x for x in steps if x.get("chain_step") == n]
        if any(x.get("id") in ends for x in mine):
            continue
        seen.add(n)
        out.append((s.get("id"), f"「{s.get('name')}」是流程里标了{tag}的第 {n} 步，但没有任何一段量到它：至少要有一段以它为终点"
                                 "（和别的点先后说不准的，从前面同一个点量起），不然这一点量不出来"))
    return out


def check(root, steps, segments=None, side_segments=None, chain=None, strict=False):
    """逐个计时点做结构检查，结果写进 step["chk"]（[{level: fail/tip, kind, text}]）和 step["struct_ok"]。
    有硬失败的点可信度降成"低"。可以重复调用（缓存、沿用的结果也再查一遍），每次重新算，不会叠加。
    strict：两步识别的新结果（点必须来自事件链、插在判断或循环开头之前算失败）；旧结果和演示数据不加这两条。"""
    proj = Project(root)
    branches = _chain_branches(chain)
    by_n = {}
    for i, c in enumerate(chain if isinstance(chain, list) else []):
        if isinstance(c, dict):
            try:
                by_n[int(str(c.get("n", i + 1)).strip())] = c
            except (TypeError, ValueError):
                pass
    for s in steps:
        s["chk"] = []
        try:
            if strict:
                t = _check_chain_step(proj, s, by_n)
                if t:
                    s["chk"].append({"level": "fail", "kind": "chain", "text": t})
                t = _check_side_on_role(s, by_n, steps)
                if t:
                    s["chk"].append({"level": "fail", "kind": "line_of", "text": t})
            c = by_n.get(s.get("chain_step")) if strict else None
            cl = chain_no_line(c)
            _check_one(proj, s, branches.get(s.get("chain_step"), ""), strict, cl if c and c.get("file") == s.get("file") else None)
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
    try:
        for sid, t in check_uncertain(steps, [g for g in list(segments or []) + list(side_segments or []) if isinstance(g, dict)], by_n):
            if sid in by and not any(c["text"] == t for c in by[sid]["chk"]):
                by[sid]["chk"].append({"level": "fail" if strict else "tip", "kind": "order", "text": t})
    except Exception as e:
        for s in steps[-1:]:
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表检查先后说不准的段时出错（{type(e).__name__}），请自己核对"})
    try:
        for sid, t in check_output(steps, by_n):
            if sid in by and not any(c["text"] == t for c in by[sid]["chk"]):
                by[sid]["chk"].append({"level": "fail" if strict else "tip", "kind": "output", "text": t})
    except Exception as e:
        for s in steps[-1:]:
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表检查输出那一步的首末点时出错（{type(e).__name__}），请自己核对"})
    try:
        for sid, t in check_dangling(steps, [g for g in list(segments or []) + list(side_segments or []) if isinstance(g, dict)], by_n):
            if sid in by and not any(c["text"] == t for c in by[sid]["chk"]):
                by[sid]["chk"].append({"level": "fail" if strict else "tip", "kind": "segment", "text": t})
    except Exception as e:
        for s in steps[-1:]:
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表检查最后结果有没有段量到时出错（{type(e).__name__}），请自己核对"})
    try:
        for sid, t in check_order(proj, steps, [g for g in list(segments or []) + list(side_segments or []) if isinstance(g, dict)]):
            if sid in by and not any(c["text"] == t for c in by[sid]["chk"]):
                # 两步识别的新结果算失败（段肯定是负的）；旧结果、演示数据只提醒
                by[sid]["chk"].append({"level": "fail" if strict else "tip", "kind": "order", "text": t})
    except Exception as e:
        for s in steps[-1:]:
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表检查段的先后时出错（{type(e).__name__}），请自己核对"})
    try:
        for sid, t in check_side_order(steps, side_segments, by_n):
            if sid in by and not any(c["text"] == t for c in by[sid]["chk"]):
                by[sid]["chk"].append({"level": "tip", "kind": "order", "text": t})
    except Exception as e:
        for s in steps[-1:]:
            s["chk"].append({"level": "tip", "kind": "error", "text": f"码表检查旁线段的先后时出错（{type(e).__name__}），请自己核对"})
    for s in steps:
        s["struct_ok"] = not any(c["level"] == "fail" for c in s["chk"])
        if not s["struct_ok"]:
            s["sure"] = "低"
    return steps


# ---------- 按设置选一支的判断（协议版本、连接方式这类）：点只在其中一支里 ----------
_CONST = re.compile(r"^(?:k[A-Z]\w*|[A-Z][A-Z0-9_]*|true|false|nullptr|NULL|this|sizeof)$")
_CALLEE = re.compile(r"([A-Za-z_]\w*)\s*(?:<[^<>()]*>)?\s*\(")
_ALT_NOT_CALL = KEYWORDS | {"return", "static_cast", "reinterpret_cast", "const_cast", "dynamic_cast", "sizeof", "decltype"}


def _callee(src, line):
    """这一句调用的函数名（a->b(…) 取 b；auto x = f(…) 取 f）；不是调用返回 None。"""
    c = src.clines[line - 1]
    for m in _CALLEE.finditer(c):
        if m.group(1) not in _ALT_NOT_CALL:
            return m.group(1)
    return None


def _calls_in(text):
    return {m.group(1) for m in _CALLEE.finditer(text) if m.group(1) not in _ALT_NOT_CALL}


def _cond_of(head):
    m = re.match(r"(?:else\s+)?(?:if|switch)\s*\((.*)\)\s*$", head.strip())
    return m.group(1) if m else None


def _setting_names(src, cond, fn):
    """判断条件里用到的量：全是"设置"（成员、全局，这个函数里不赋值、也不是参数或局部变量）时返回它们的名字，
    条件里有函数调用（比如查当前状态、比较消息类型）、用到局部变量或参数、在这个函数里改过的，返回 None（是运行时每次不同的东西）。"""
    if cond is None:
        return None
    if re.search(r"[A-Za-z_]\w*\s*\(", cond):
        return None
    body = src.code[src.blocks[fn]["open"]:src.blocks[fn]["close"]] if fn is not None else ""
    head = src.blocks[fn]["head"] if fn is not None else ""
    names = set()
    for m in re.finditer(r"(?<![\w.>:])([A-Za-z_]\w*)", re.sub(r"(?:->|\.)\s*[A-Za-z_]\w*", "", cond)):
        n = m.group(1)
        if _CONST.match(n) or n.isdigit():
            continue
        if re.search(rf"\b{re.escape(n)}\b", head):
            return None  # 参数
        if re.search(rf"(?<![=!<>]){re.escape(n)}\s*(?:[+\-*/|&^]?=(?!=)|\+\+|--)|(?:\+\+|--)\s*{re.escape(n)}\b", body):
            return None  # 这个函数里改过
        if re.search(rf"\b(?:auto|bool|char|int|long|short|float|double|size_t|u?int\d+_t|unsigned|signed|const)\b[\w\s:<>*&]*\b{re.escape(n)}\s*[=;\[({{]", body):
            return None  # 局部变量
        names.add(n)
    return names or None


def alt_text(src, line, alt, moved=False):
    """给人看的说明：这一句只在某一种设置下才走。"""
    what = "、".join(alt["names"])
    base = (f"第 {line} 行只在「{_branch_cond(src, line, alt)}」这一种设置下才走"
            f"（第 {alt['head']} 行按 {what} 分成 {alt['n']} 支，每一支都调用 {alt['callee']}，设备只走其中一支）")
    if moved:
        return base + f"；码表把点挪到第 {alt['head']} 行这个判断前面，不管哪种设置都会记到"
    return base + f"；设备不是这种设置时一次也记不到，点要放在第 {alt['head']} 行这个判断前面"


def _branch_cond(src, line, alt):
    at = src.pos(line, "before")
    for i in src.chain(at):
        b = src.blocks[i]
        if b["kind"] == "branch":
            c = _cond_of(b["head"]) if b["name"] != "case" else None
            if c:
                return c
            m = next((k for k in range(line, 0, -1) if re.match(r"\s*case\b", src.clines[k - 1])), None)
            return src.lines[m - 1].strip().rstrip(":") if m else alt["cond"]
    return alt["cond"]


def alt_branch(src, line):
    """这一行在"按设置选一支"的判断里的某一支（不是最后那个默认的 else / default），而别的每一支也调用同一个函数
    （比如按协议版本各组一份数据、都交给同一个回调或发送函数；设备只走其中一支，插在这一支里换个设置就一次也记不到）：
    返回 {"head": 整个判断开头那一行, "callee", "cond", "names"}；不是就 None。
    最后那个默认的 else / default 不算（什么都没设时走它）；条件里有函数调用、局部变量、参数、在这个函数里改过的量不算
    （那是运行时的情况，比如消息类型、当前状态、检测结果，各支本来就是不同的事）。"""
    if src is None or not 1 <= line <= len(src.lines):
        return None
    callee = _callee(src, line)
    if not callee:
        return None
    at = src.pos(line, "before")
    chain = src.chain(at)
    fn = next((i for i in chain if src.blocks[i]["kind"] in ("function", "lambda")), None)
    for i in chain:
        b = src.blocks[i]
        if b["kind"] in ("function", "lambda", "loop"):
            return None
        if b["kind"] != "branch":
            continue
        if b["name"] in ("switch", "case", "default"):
            sw = i if b["name"] == "switch" else b["parent"]
            if sw is None or src.blocks[sw]["name"] != "switch":
                return None
            return _alt_switch(src, sw, line, callee, fn)
        return _alt_if(src, i, callee, fn)
    return None


def _alt_if(src, i, callee, fn):
    par = src.blocks[i]["parent"]
    sib = [k for k, b in enumerate(src.blocks) if b["parent"] == par and b["kind"] == "branch"]
    k = sib.index(i)
    lo = k
    while lo > 0 and src.blocks[sib[lo]]["head"].startswith("else") and \
            re.fullmatch(r"\s*else\b.*", src.code[src.blocks[sib[lo - 1]]["close"] + 1:src.blocks[sib[lo]]["open"]], re.S):
        lo -= 1
    hi = k
    while hi + 1 < len(sib) and src.blocks[sib[hi + 1]]["head"].startswith("else") and \
            re.fullmatch(r"\s*else\b.*", src.code[src.blocks[sib[hi]]["close"] + 1:src.blocks[sib[hi + 1]]["open"]], re.S):
        hi += 1
    members = sib[lo:hi + 1]
    if len(members) < 2 or not src.blocks[members[0]]["head"].startswith("if"):
        return None
    if src.blocks[i]["head"].strip() == "else":
        return None  # 默认那一支
    names = set()
    for m in members:
        h = src.blocks[m]["head"].strip()
        if h == "else":
            continue
        got = _setting_names(src, _cond_of(h), fn)
        if not got:
            return None
        names |= got
    for m in members:
        if m != i and callee not in _calls_in(src.code[src.blocks[m]["open"] + 1:src.blocks[m]["close"]]):
            return None
    head = src.line_of(src.blocks[members[0]]["open"])
    while head > 1 and not re.match(r"\s*if\b", src.clines[head - 1]):
        head -= 1
    return {"head": head, "callee": callee, "cond": " / ".join(_cond_of(src.blocks[m]["head"]) or "其他" for m in members),
            "names": sorted(names), "n": len(members)}


def _alt_switch(src, sw, line, callee, fn):
    b = src.blocks[sw]
    names = _setting_names(src, _cond_of(b["head"]), fn)
    if not names:
        return None
    first, last = src.line_of(b["open"]), src.line_of(b["close"])
    labels = [k for k in range(first, last + 1) if re.match(r"\s*(?:case\b.*?:|default\s*:)", src.clines[k - 1])
              and (lambda c: c and (c[0] == sw or src.blocks[c[0]]["parent"] == sw))(src.chain(src.pos(k, "before")))]
    mine = max((k for k in labels if k <= line), default=None)
    if mine is None or len(labels) < 2 or re.match(r"\s*default\s*:", src.clines[mine - 1]):
        return None
    ends = labels[1:] + [last]
    for a, e in zip(labels, ends):
        if a == mine:
            continue
        text = "\n".join(src.clines[a - 1:e - 1])
        if callee not in _calls_in(text):
            return None
    head = first
    while head > 1 and not re.match(r"\s*switch\b", src.clines[head - 1]):
        head -= 1
    return {"head": head, "callee": callee, "cond": _cond_of(b["head"]), "names": sorted(names), "n": len(labels)}
