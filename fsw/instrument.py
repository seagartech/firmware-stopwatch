"""插计时点：AI 只读代码，给出"每个计时点插在哪一行后面、插哪几行"，码表自己插进复制的那份源码。

- AI 给的每一行只能是：中文注释、#include 计时点工具、计时点语句（FSW_ 开头）。夹带别的代码就整份不用。
- 码表插完逐个文件核对：原来的每一行都在、顺序不变，只多不少（只加不删）。
- 插之前把要改的文件原样存一份；"还原"就是放回去、删掉计时点工具，再和原来的源码逐字节比对。
- 编译出错时，按报错的"文件:行号"对回是哪一处计时点，可以只撤掉这一处重编。
- 附加值：问题规则要看的计时点（比如"第一包声音到达时设备的状态"）让 AI 顺带记上；附加值只能读已有的变量或
  不带参数的函数返回值（转成整数），不能赋值、自增、调用带参数的函数。记上了写进插入记录，判问题时才用。
计时点工具（fw_stopwatch.h）是码表自己的固定模板，C 和 C++ 文件都能用，有总开关。
"""

import difflib
import html
import json
import re
import shutil
import time
from pathlib import Path

from fsw.identify import _json_block
from fsw.llm import LLMError
from fsw.workspace.inspect import SKIP_DIRS
from fsw.workspace.workspace import GENERATED

HEADER = r"""// fw_stopwatch.h —— 固件码表的计时点工具（只记时间，不改原来的逻辑）
//
// 总开关：把下面的 FW_STOPWATCH_ENABLE 改成 0（或编译时加 -DFW_STOPWATCH_ENABLE=0），
// 所有计时点都变成空语句，固件的行为和原版一样。要让源码一个字节都不差地回到原版，用码表的"还原"。
//
// 串口每个计时点打印一行：FSW,P<编号>,<开机后微秒>,<附加值>
// 每轮开始打印：FSW,RESET,<开机后微秒>,0
// 开销：取时间用 esp_timer_get_time()，只用原子读写，不分配内存、不加锁。
#pragma once
#include <stdint.h>

#ifndef FW_STOPWATCH_ENABLE
#define FW_STOPWATCH_ENABLE 1
#endif

#if FW_STOPWATCH_ENABLE
#include <stdio.h>
#include "esp_timer.h"

#ifdef __cplusplus
extern "C" {
#endif

#define FSW_MAX_ID 32

// 几个文件共用同一份记录：weak 让链接时合成一份（C 和 C++ 文件都行）
__attribute__((weak)) unsigned char fsw_done[FSW_MAX_ID];   // "一轮只记第一次"：这一轮记过没有
__attribute__((weak)) int64_t fsw_last_us[FSW_MAX_ID];      // "记最后一次"：最后一次的时间
__attribute__((weak)) int32_t fsw_last_n[FSW_MAX_ID];       // "记最后一次"：这一轮发生了几次
__attribute__((weak)) unsigned char fsw_armed;              // 这一轮是否已经"武装"

static inline int fsw_ok(int id) { return id >= 0 && id < FSW_MAX_ID; }

static inline void fsw_print(int id, int64_t us, int32_t v) {
    printf("FSW,P%d,%lld,%ld\n", id, (long long)us, (long)v);
}

// 每次都记
static inline void fsw_mark(int id, int32_t v) { fsw_print(id, esp_timer_get_time(), v); }

// 一轮只记第一次
static inline void fsw_mark_once(int id, int32_t v) {
    if (fsw_ok(id) && !__atomic_exchange_n(&fsw_done[id], (unsigned char)1, __ATOMIC_RELAXED)) {
        fsw_print(id, esp_timer_get_time(), v);
    }
}

// 武装之后才记（同一处代码别的时候也会走到，比如提示音也走播放）
static inline void fsw_arm(void) { __atomic_store_n(&fsw_armed, (unsigned char)1, __ATOMIC_RELAXED); }
static inline void fsw_mark_once_armed(int id, int32_t v) {
    if (__atomic_load_n(&fsw_armed, __ATOMIC_RELAXED)) fsw_mark_once(id, v);
}

// 这一轮走到某一步（after_id 已经记过）之后才记（决策 118）：上一轮剩下的东西在这一轮早先触发时不记，
// 比如上一句回答剩下的一包声音在新一轮聆听时才到
static inline void fsw_mark_once_after(int id, int after_id, int32_t v) {
    if (fsw_ok(after_id) && __atomic_load_n(&fsw_done[after_id], __ATOMIC_RELAXED)) fsw_mark_once(id, v);
}

// 高频位置：只在内存里记最后一次，到后面某个计时点再打印（附加值 = 这一轮发生了几次）
static inline void fsw_keep_last(int id) {
    if (!fsw_ok(id)) return;
    __atomic_store_n(&fsw_last_us[id], esp_timer_get_time(), __ATOMIC_RELAXED);
    __atomic_fetch_add(&fsw_last_n[id], 1, __ATOMIC_RELAXED);
}
static inline void fsw_flush_last(int id) {
    if (!fsw_ok(id)) return;
    int64_t us = __atomic_exchange_n(&fsw_last_us[id], (int64_t)0, __ATOMIC_RELAXED);
    int32_t n = __atomic_exchange_n(&fsw_last_n[id], (int32_t)0, __ATOMIC_RELAXED);
    if (us) fsw_print(id, us, n);
}

// 一轮开始：清零
static inline void fsw_round_reset(void) {
    for (int i = 0; i < FSW_MAX_ID; i++) {
        __atomic_store_n(&fsw_done[i], (unsigned char)0, __ATOMIC_RELAXED);
        __atomic_store_n(&fsw_last_us[i], (int64_t)0, __ATOMIC_RELAXED);
        __atomic_store_n(&fsw_last_n[i], (int32_t)0, __ATOMIC_RELAXED);
    }
    __atomic_store_n(&fsw_armed, (unsigned char)0, __ATOMIC_RELAXED);
    printf("FSW,RESET,%lld,0\n", (long long)esp_timer_get_time());
}

#ifdef __cplusplus
}
#endif

#define FSW_MARK(id, v) fsw_mark((id), (int32_t)(v))
#define FSW_MARK_ONCE(id, v) fsw_mark_once((id), (int32_t)(v))
#define FSW_MARK_ONCE_ARMED(id, v) fsw_mark_once_armed((id), (int32_t)(v))
#define FSW_MARK_ONCE_AFTER(id, after, v) fsw_mark_once_after((id), (after), (int32_t)(v))
#define FSW_ARM() fsw_arm()
#define FSW_KEEP_LAST(id) fsw_keep_last(id)
#define FSW_FLUSH_LAST(id) fsw_flush_last(id)
#define FSW_ROUND_RESET() fsw_round_reset()

#else  // 总开关关掉：全部是空语句

#define FSW_MARK(id, v) do {} while (0)
#define FSW_MARK_ONCE(id, v) do {} while (0)
#define FSW_MARK_ONCE_ARMED(id, v) do {} while (0)
#define FSW_MARK_ONCE_AFTER(id, after, v) do {} while (0)
#define FSW_ARM() do {} while (0)
#define FSW_KEEP_LAST(id) do {} while (0)
#define FSW_FLUSH_LAST(id) do {} while (0)
#define FSW_ROUND_RESET() do {} while (0)

#endif  // FW_STOPWATCH_ENABLE
"""

PLAN_VER = 2  # 插入提示词改过（决策 118 加了 FSW_MARK_ONCE_AFTER）：之前存的插入方案不再沿用
HELPERS = {"include", "reset", "arm", "flush"}
LINE_OK = re.compile(r'^\s*(//[^\n]*|#include\s+["<][^"<>\n]*fw_stopwatch\.h[">]\s*(//[^\n]*)?|FSW_[A-Z_]+\([^;\n]*\);\s*(//[^\n]*)?)?$')
ONCE = {"first": "一轮只记第一次", "last": "一轮里记最后一次", "every": "每次都记"}
EXTRA_GENERATED = {"sdkconfig.fsw-board"}

PROMPT = """你是"固件码表"工具里的计时点插入助手。固件源码就在当前目录，只能读，不能改；真正插入由码表来做。

## 用户已确认，要在这些地方插计时点
{table}

## 码表提供的计时点工具
码表会把头文件 {header} 放进项目，里面有这些语句（编号用上面表里的数字，附加值没有就写 0）：
- FSW_MARK(编号, 附加值);  每次都记一行
- FSW_MARK_ONCE(编号, 附加值);  一轮只记第一次
- FSW_KEEP_LAST(编号);  高频位置用：只在内存里记最后一次，再在之后的某个计时点处用 FSW_FLUSH_LAST(编号); 打印出来
- FSW_ROUND_RESET();  一轮开始时清零"只记第一次"的标记，放在每轮开始的地方（比如进入聆听状态、读到卡）
- FSW_ARM(); 和 FSW_MARK_ONCE_ARMED(编号, 附加值);  同一处代码别的时候也会走到（比如提示音也走播放）时用：只在 FSW_ARM() 之后才记
- FSW_MARK_ONCE_AFTER(编号, 前一步编号, 附加值);  这一轮走到"前一步"（那个用 FSW_MARK_ONCE 记的计时点）之后才记，只记第一次。
  会被上一轮剩下的东西提前触发的点要用它：比如"收到第一段声音"，上一句回答剩下的一包声音可能在新一轮聆听时才到，
  用 FSW_MARK_ONCE 会记成那一包，这一轮真正的第一包反而记不到。前一步选这一轮里它前面、一定会先发生的那个计时点（比如"收到开口指令"）

{values}## 你的任务
读代码，确定每个计时点具体插在哪一行的后面（after_line 是原文件现在的行号，0 表示插在文件最前面），写出要插的几行（带上和周围一样的缩进）。
- 每个计时点插两行：一行中文注释（// 计时点 P编号：中文名），一行计时点语句。辅助语句（清零、武装、打印最后一次）也各配一行注释。
- 每个用到计时点的文件，在开头的 #include 附近加一行 #include，路径要让编译器能找到 {header}。这一行只写 #include，说明另起一行写注释。
- 只能插注释、#include 和 FSW_ 开头的计时点语句，不能加别的代码，不能改、不能删已有的行。
- 不要插在宏定义、多行语句、if 条件、初始化列表的中间；插在 return / break 之后的执行不到。
- 每个位置都要亲自打开文件核对行号。

## 最后输出
只输出一个 ```json 代码块：
```json
{{"header": "{header}",
 "inserts": [{{"step": "P2", "file": "main/xxx.cc", "after_line": 482, "lines": ["            // 计时点 P2：设备发现你停了", "            FSW_KEEP_LAST(2);"]}}],
 "notes": ["你觉得有风险的地方（比如某个计时点在很频繁的线程里），没有就空"]}}
```
step 写计时点（比如 P2），辅助语句写 include / reset / arm / flush。{values_out}
"""

VALUES = """## 要顺带记附加值的计时点
码表要靠这几个计时点的附加值查问题，请在计时点语句的第二个参数里写上（别的计时点附加值写 0）：
{rows}
- 附加值只能读：已有的变量、成员，或不带参数的函数返回值（比如 GetDeviceState()），转成整数，比如 (int)xxx 或 static_cast<int>(xxx)。
- 不能赋值、不能自增自减、不能调用带参数的函数，不能改任何逻辑。读不到就写 0，在 notes 里说为什么。
- 去代码里查清楚"正常时"对应的整数是几（比如枚举里第几个），写在 values 里。

"""
VALUES_OUT = """
另外在 json 里加 "values"：[{{"step": "P7", "expect": 正常时附加值对应的整数, "why": "在哪个文件哪一行查到的"}}]，每个要记附加值的计时点一条。"""


def number(step_id, steps):
    """计时点编号：P3 → 3；你自己加的 U1、U2 → 21、22（不和识别出来的撞）。"""
    m = re.fullmatch(r"P(\d+)", step_id)
    if m:
        return int(m.group(1))
    users = [s["p"] for s in steps if not re.fullmatch(r"P\d+", s["p"])]
    return 20 + users.index(step_id) + 1


def point_id(step, steps):
    """步骤插成的计时点编号（P3、P21）。撤掉过一处的，按插入时的编号记在步骤的 fsw 里（后面的不重新编号）。"""
    return step.get("fsw") or f"P{number(step['p'], steps)}"


def default_header(root):
    return "main/fw_stopwatch.h" if (Path(root) / "main").is_dir() else "fw_stopwatch.h"


def _needs_by_id(steps, needs):
    ids = {s["p"]: f"P{number(s['p'], steps)}" for s in steps}
    return {ids[k]: v for k, v in (needs or {}).items() if k in ids}


def build_prompt(steps, header, needs=None):
    nb = _needs_by_id(steps, needs)
    names = {f"P{number(s['p'], steps)}": s["n"] for s in steps}
    vrows = "\n".join(f"- {k}（{names.get(k, k)}）：附加值记{v['what']}；正常时是{v['ok']}"
                      f"{'（标准里记的是 ' + str(v['value']) + '）' if v.get('value') is not None else ''}"
                      f"{'；' + v['how'] if v.get('how') else ''}；用来查：{'、'.join(v.get('for') or [])}" for k, v in nb.items())
    rows = "\n".join(
        f"- P{number(s['p'], steps)}（编号 {number(s['p'], steps)}）{s['n']}：{s.get('path') or s.get('f')} 第 {s['l']} 行"
        f"{'之前' if s.get('where') == 'before' else '之后' if s.get('where') == 'after' else ''}，"
        f"{ONCE.get(s.get('once'), '一轮只记第一次')}" + (f"；为什么在这：{s['why']}" if s.get("why") else "")
        for s in steps)
    return PROMPT.format(table=rows, header=header, values=VALUES.format(rows=vrows) if nb else "",
                         values_out=VALUES_OUT if nb else "")


MARK_CALL = re.compile(r"FSW_(MARK|MARK_ONCE|MARK_ONCE_ARMED|MARK_ONCE_AFTER)\(\s*(\d+)\s*,\s*(.*)\)\s*;")
CAST = re.compile(r"\b(?:static_cast|reinterpret_cast)\s*<\s*[A-Za-z_][\w:]*\s*>\s*\(|\(\s*(?:unsigned\s+)?[A-Za-z_][\w:]*\s*\)")
VALUE_TOKENS = re.compile(r"\s*(?:[A-Za-z_]\w*|::|->|\.|\(|\)|\d+|\*|&|-)")


def value_ok(expr):
    """附加值只能读：变量、成员、不带参数的函数返回值、转整数；不能赋值、自增、调用带参数的函数。"""
    e = expr.strip()
    if re.fullmatch(r"-?\d+", e):
        return True
    e = CAST.sub(" ( ", e)  # (int)x、static_cast<int>(x) 这样的转换去掉再看
    pos = 0
    while pos < len(e):
        m = VALUE_TOKENS.match(e, pos)
        if not m or m.end() == pos:
            return False
        pos = m.end()
    if re.search(r"\+\+|--|=|,", e):
        return False
    return not re.search(r"[A-Za-z_]\w*\s*\((?!\s*\))", e)  # 函数调用只能不带参数


def _marks(lines):
    """计时点语句里的 (编号, 附加值)。"""
    out = []
    for l in lines:
        if isinstance(l, str) and (m := MARK_CALL.search(l)):
            v = m.group(3).strip()
            if m.group(1) == "MARK_ONCE_AFTER":  # 第二个参数是"前一步编号"，附加值在后面
                v = re.sub(r"^\d+\s*,\s*", "", v)
            out.append((int(m.group(2)), v))
    return out


def _fail(kind, message, **extra):
    return {"ok": False, "kind": kind, "message": message, **extra}


def _read_lines(p):
    return Path(p).read_bytes().decode("utf-8", errors="surrogateescape").splitlines(keepends=True)


def parse_plan(text, root, steps, needs=None):
    """读 AI 给的插入清单，逐条核对；不对就整份不用（一个文件都不改）。"""
    root = Path(root).resolve()
    data = _json_block(text)
    if not isinstance(data, dict) or not isinstance(data.get("inserts"), list):
        return _fail("bad_reply", "大模型回答的格式不对，码表看不懂，一个文件都没改。点\"重新插入\"再试一次。")
    header = str(data.get("header") or default_header(root)).lstrip("./")
    if not header.endswith("fw_stopwatch.h") or (root / header).resolve().parent != (root / header).parent.resolve() \
            or root not in (root / header).resolve().parents:
        header = default_header(root)
    ids = {f"P{number(s['p'], steps)}": s for s in steps}
    out, problems = [], []
    for x in data["inserts"]:
        if not isinstance(x, dict):
            continue
        step, rel, lines = str(x.get("step") or ""), str(x.get("file") or "").lstrip("./"), x.get("lines")
        p = (root / rel).resolve()
        if step not in ids and step not in HELPERS:
            problems.append(f"不认识的计时点 {step}")
            continue
        if root not in p.parents or not p.is_file():
            problems.append(f"找不到文件 {rel}")
            continue
        try:
            after = int(x.get("after_line"))
        except (TypeError, ValueError):
            problems.append(f"{rel} 的行号写得不对")
            continue
        n = len(_read_lines(p))
        if not 0 <= after <= n:
            problems.append(f"{rel} 没有第 {after} 行（只有 {n} 行）")
            continue
        bad = [l for l in lines if not (isinstance(l, str) and LINE_OK.match(l))] if isinstance(lines, list) else [str(lines)]
        if not lines or bad:
            problems.append(f"{rel} 第 {after} 行后面要插的内容里夹带了别的代码：<code>{html.escape(str(bad[0] if bad else '')[:80])}</code>")
            continue
        badv = [v for _, v in _marks(lines) if not value_ok(v)]
        if badv:
            problems.append(f"{rel} 第 {after} 行的附加值不是只读的：<code>{html.escape(badv[0][:80])}</code>"
                            "（附加值只能读已有的变量或不带参数的函数返回值）")
            continue
        out.append({"step": step, "file": p.relative_to(root).as_posix(), "after_line": after, "lines": lines})
    missing = [s["n"] for k, s in ids.items() if not any(x["step"] == k for x in out)]
    if missing:
        problems.append("这些计时点没插：" + "、".join(missing))
    for f in {x["file"] for x in out if x["step"] != "include"}:
        has = any(x["file"] == f and x["step"] == "include" for x in out) or "fw_stopwatch.h" in (root / f).read_text(errors="replace")
        if not has:
            problems.append(f"{f} 用了计时点，但没加 #include")
    if problems:
        return _fail("bad_plan", "AI 给的插入清单不对，码表没有用，一个文件都没改：" + "；".join(problems[:4]) +
                     "。只能加计时点、注释和 #include。点\"重新插入\"再试一次。")
    notes = [str(n) for n in data.get("notes") or [] if n]
    said = {str(v.get("step")): v for v in data.get("values") or [] if isinstance(v, dict)}
    values = {}
    for k, need in _needs_by_id(steps, needs).items():
        got = [v for x in out if x["step"] == k for n, v in _marks(x["lines"]) if n == int(k[1:])]
        expr = next((v for v in got if not re.fullmatch(r"-?\d+", v)), None)
        if not expr:
            notes.append(f"{k}（{ids[k]['n']}）没记上{need['what']}：这次查不了{'、'.join(need.get('for') or [])}")
            continue
        expect = said.get(k, {}).get("expect")
        values[k] = {"expr": expr, "expect": expect if isinstance(expect, int) and not isinstance(expect, bool) else need.get("value"),
                     "what": need["what"]}
    return {"ok": True, "header": header, "inserts": out, "notes": notes, "values": values}


def _record_path(backup):
    return Path(backup) / "record.json"


def apply(root, plan, backup):
    """把计时点插进复制的源码。要改的文件先原样存一份；插完核对只加不删。"""
    root, backup = Path(root), Path(backup)
    if _record_path(backup).exists():
        restore(root, backup)  # 上一次插的先还原，保证总是在原版上插
    files = sorted({x["file"] for x in plan["inserts"]})
    for f in files:
        dst = backup / "files" / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / f, dst)
    header = root / plan["header"]
    created = not header.exists()
    rec = {"header": plan["header"], "created_header": created, "files": files, "steps": {}, "places": [],
           "added": 0, "deleted": 0, "values": plan.get("values") or {}}
    for f in files:
        old = _read_lines(root / f)
        nl = "\r\n" if old and old[0].endswith("\r\n") else "\n"
        mine = sorted((x for x in plan["inserts"] if x["file"] == f), key=lambda x: x["after_line"])
        new, i = [], 0
        for x in mine:
            while i < x["after_line"]:
                new.append(old[i])
                i += 1
            if new and not new[-1].endswith(("\n", "\r")):
                new[-1] += nl  # 文件最后一行没有换行，插在它后面时补一个换行
            start = len(new) + 1
            new += [l.rstrip() + nl for l in x["lines"]]
            lines_at = list(range(start, len(new) + 1))
            ctx = old[x["after_line"] - 1].rstrip("\r\n") if x["after_line"] else None
            rec["places"].append({"step": x["step"], "file": f, "at": x["after_line"], "context": ctx,
                                  "lines": [l.rstrip() for l in x["lines"]], "new_lines": lines_at})
            if x["step"] not in HELPERS:
                s = rec["steps"].setdefault(x["step"], {"file": f, "lines": [], "at": x["after_line"]})
                s["lines"] += lines_at
        new += old[i:]
        (root / f).write_bytes("".join(new).encode("utf-8", errors="surrogateescape"))
        a = [l.rstrip("\r\n") for l in old]
        b = [l.rstrip("\r\n") for l in new]
        ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        rec["added"] += sum(j2 - j1 for t, _, _, j1, j2 in ops if t == "insert")
        rec["deleted"] += sum(i2 - i1 for t, i1, i2, _, _ in ops if t in ("delete", "replace"))
    if created:
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(HEADER, encoding="utf-8")
        rec["added"] += len(HEADER.splitlines())
    _record_path(backup).write_text(json.dumps({**rec, "plan": plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    if rec["deleted"]:
        restore(root, backup)
        return _fail("deleted", f"码表核对时发现会删掉或改掉 {rec['deleted']} 行原有代码，已经撤回，一个文件都没改。")
    return {"ok": True, **rec}


def restore(root, backup):
    """还原：把改过的文件原样放回去，删掉码表加的计时点工具。"""
    root, backup = Path(root), Path(backup)
    rp = _record_path(backup)
    if not rp.exists():
        return {"ok": True, "restored": 0}
    rec = json.loads(rp.read_text(encoding="utf-8"))
    for f in rec["files"]:
        shutil.copy2(backup / "files" / f, root / f)
    if rec.get("created_header"):
        (root / rec["header"]).unlink(missing_ok=True)
    shutil.rmtree(backup / "files", ignore_errors=True)
    rp.unlink()
    return {"ok": True, "restored": len(rec["files"])}


def current(backup):
    rp = _record_path(backup)
    return json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None


def _files(root):
    root = Path(root)
    out = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and not SKIP_DIRS & set(rel.parts) and p.name not in GENERATED | EXTRA_GENERATED:
            out[rel.as_posix()] = p
    return out


def compare(work, original):
    """逐字节比对：原来源码里的每个文件，这边都要一字节不差；码表自己加的计时点工具要删干净。
    只在这边有的其他文件（编译时生成的，比如语言配置）单独列出，不算不一样。"""
    fw, fo = _files(work), _files(original)
    diff = sorted(k for k in fo if k not in fw or fw[k].read_bytes() != fo[k].read_bytes())
    extra = sorted(k for k in fw if k not in fo)
    diff += [k for k in extra if Path(k).name == "fw_stopwatch.h"]
    return {"same": not diff, "files": len(fo), "diff": sorted(diff),
            "extra": [k for k in extra if Path(k).name != "fw_stopwatch.h"]}


def diff_view(rec, steps):
    """网页"看改动"用：每处一行，可展开看代码。"""
    names = {f"P{number(s['p'], steps)}": s["n"] for s in steps}
    for k, v in (rec.get("values") or {}).items():
        if k in names:
            names[k] += f"（顺带记{v['what']}）"
    places = []
    for x in rec["places"]:
        lines = ([[" ", x["context"]]] if x["context"] is not None else []) + [["+", l] for l in x["lines"]]
        places.append({"step": x["step"], "n": names.get(x["step"], {"include": "加上计时点工具", "reset": "每轮开始清零",
                                                                     "arm": "武装（之后才记）", "flush": "打印最后一次"}.get(x["step"], x["step"])),
                       "file": x["file"], "at": x["at"], "lines": lines, "added": len(x["lines"])})
    return {"files": len(rec["files"]) + (1 if rec["created_header"] else 0), "added": rec["added"], "deleted": rec["deleted"],
            "header": rec["header"], "header_lines": len(HEADER.splitlines()), "places": places,
            "steps": sorted(rec["steps"], key=lambda k: int(k[1:]))}


ERR = re.compile(r"([^\s:'\"]+\.(?:c|cc|cpp|cxx|h|hpp)):(\d+)(?::\d+)?:\s*(?:fatal )?error")


def locate_error(text, rec, root):
    """按报错的"文件:行号"对回是哪一处计时点；对不上返回 None。"""
    root = Path(root).resolve()
    for m in ERR.finditer(text or ""):
        f, line = m.group(1), int(m.group(2))
        try:
            rel = Path(f).resolve().relative_to(root).as_posix() if f.startswith("/") else f.lstrip("./")
        except ValueError:
            continue
        for sid, s in rec["steps"].items():
            if s["file"] == rel and (line in s["lines"] or line == max(s["lines"]) + 1):
                return sid
    return None


def drop(root, plan, step, backup):
    """只撤掉一处计时点，其他的照样插着。"""
    rest = [x for x in plan["inserts"] if x["step"] != step]
    used = {x["file"] for x in rest if x["step"] not in HELPERS}
    rest = [x for x in rest if x["step"] != "include" or x["file"] in used]
    return apply(root, {**plan, "inserts": rest}, backup)


def insert(llm, root, steps, backup, on_progress=None, needs=None):
    """调用一次大模型（只读）拿到插入清单，码表核对后插进去。清单不对就一个文件都不改。"""
    t0 = time.time()
    header = default_header(root)
    state = {"read": 0}

    def tool(_desc):
        state["read"] += 1
        on_progress and on_progress({"seconds": round(time.time() - t0, 1), "read": state["read"]})

    try:
        text = llm.investigate(build_prompt(steps, header, needs), root, on_tool=tool)
    except LLMError as e:
        return _fail(e.kind, f"{e.message}<br>一个文件都没改，点\"重新插入\"再试一次。", seconds=round(time.time() - t0, 1))
    plan = parse_plan(text, root, steps, needs)
    if not plan["ok"]:
        return {**plan, "seconds": round(time.time() - t0, 1)}
    rec = apply(root, plan, backup)
    if not rec["ok"]:
        return {**rec, "seconds": round(time.time() - t0, 1)}
    return {"ok": True, "plan": plan, "rec": rec, "view": diff_view(rec, steps), "notes": plan["notes"],
            "seconds": round(time.time() - t0, 1)}


MARK = re.compile(r"^FSW,P(\d+),\d+,-?\d+\s*$")


def count_marks(lines):
    """试一次：数收到了哪几个计时点。"""
    return {int(m.group(1)) for l in lines if (m := MARK.match(l.strip()))}
