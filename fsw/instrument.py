"""插计时点：AI 只读代码，给出"每个计时点插在哪一行后面、插哪几行"，码表自己插进复制的那份源码。

- AI 给的每一行只能是：中文注释、#include 计时点工具、计时点语句（FSW_ 开头）。夹带别的代码就整份不用。
- 码表插完逐个文件核对：原来的每一行都在、顺序不变，只多不少（只加不删）。
- 插之前把要改的文件原样存一份，先写"进行中"的插入记录再改文件，中途出错就撤回（要么全插上，要么一个文件都不改）。
  "还原"就是核对记录和副本对得上、放回去、删掉计时点工具，再和原来的源码逐字节比对。
- 编译出错时，按报错的"文件:行号"对回是哪一处计时点，可以只撤掉这一处重编。
- 附加值：问题规则要看的计时点（比如"第一包声音到达时设备的状态"）让 AI 顺带记上；附加值只能读已有的变量或
  不带参数的函数返回值（转成整数），不能赋值、自增、调用带参数的函数。记上了写进插入记录，判问题时才用。
计时点工具（fw_stopwatch.h）是码表自己的固定模板，C 和 C++ 文件都能用，有总开关。
"""

import difflib
import hashlib
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
// "记最后一次"的点到一轮开始时还没打印出来（比如设备先回到等待、后放空声音）：清零前先补打一行
// FSW,LATE,P<编号>,<那次的微秒>,<这一轮几次>，码表只把它当"取最后一次"的终点用
// 可能在清零之后才走到的点（FSW_TAIL_MASK，码表按流程先后找出来写进来）：清零后 FSW_TAIL_US 内、下一轮起点之前走到，
// 还是上一轮的尾巴——"记最后一次"的直接补打一行 LATE，"每次都记"的照常打印；
// 更晚才走到的"记最后一次"打一行 FSW,TAIL,P<编号>,<微秒>,0（每轮一次），只是告诉码表它晚了，不算数
// 跟着起点的点（FSW_FOLLOW_MASK，码表按流程找出来写进来：起点和第一个结果之间的几步，比如"静音回调""上传首包"）：
// 起点一轮会报好几次（每停一下判一次"说完"、杂音也算），码表取第一个结果之前的最后一次；这几个"只记第一次"的点
// 每报一次起点就重新记一次（第一个结果之后的点走到过就不再重新记），量的是同一次"说完"之后的那一下
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

// 这一轮的起点是哪个计时点（码表插点时写进来）。-1 = 不知道（老的插法）：和以前一样
#ifndef FSW_START_ID
#define FSW_START_ID -1
#endif

// 可能在每轮清零之后才走到的点（第几位 = 编号）。0 = 没有（老的插法）：和以前一样
#ifndef FSW_TAIL_MASK
#define FSW_TAIL_MASK 0UL
#endif
#ifndef FSW_TAIL_US
#define FSW_TAIL_US 1000000
#endif
// 跟着起点重新记的点（第几位 = 编号）。0 = 没有（老的插法）：和以前一样
#ifndef FSW_FOLLOW_MASK
#define FSW_FOLLOW_MASK 0UL
#endif

// 几个文件共用同一份记录：weak 让链接时合成一份（C 和 C++ 文件都行）
__attribute__((weak)) unsigned char fsw_done[FSW_MAX_ID];   // "一轮只记第一次"：这一轮记过没有
__attribute__((weak)) int64_t fsw_last_us[FSW_MAX_ID];      // "记最后一次"：最后一次的时间
__attribute__((weak)) int32_t fsw_last_n[FSW_MAX_ID];       // "记最后一次"：这一轮发生了几次
__attribute__((weak)) unsigned char fsw_armed;              // 这一轮是否已经"武装"
__attribute__((weak)) unsigned char fsw_started;            // 这一轮的起点到了没有
__attribute__((weak)) unsigned char fsw_pre[FSW_MAX_ID];    // 起点之前打过没有：起点之前每个点最多打一次
__attribute__((weak)) int64_t fsw_tail_until;               // 上一轮（有起点的）清零后，到这个时间之前算它的尾巴
__attribute__((weak)) int32_t fsw_tail_n[FSW_MAX_ID];       // 尾巴里"记最后一次"的点：接着上一轮数到第几次
__attribute__((weak)) unsigned char fsw_past;               // 起点之后，跟着起点的那几步以外的点走到过没有（走到过就不再重新记）

static inline int fsw_ok(int id) { return id >= 0 && id < FSW_MAX_ID; }

static inline void fsw_print(int id, int64_t us, int32_t v) {
    printf("FSW,P%d,%lld,%ld\n", id, (long long)us, (long)v);
}

// 这一轮的起点到了：之前暂存的"记最后一次"都是这一轮开始之前的（比如唤醒提示音的最后一段），清掉，不算进这一轮
static inline void fsw_seen(int id) {
    if (id != FSW_START_ID || __atomic_exchange_n(&fsw_started, (unsigned char)1, __ATOMIC_RELAXED)) return;
    for (int i = 0; i < FSW_MAX_ID; i++) {
        if (i == FSW_START_ID) continue;
        __atomic_store_n(&fsw_last_us[i], (int64_t)0, __ATOMIC_RELAXED);
        __atomic_store_n(&fsw_last_n[i], (int32_t)0, __ATOMIC_RELAXED);
    }
}

// 起点还没到（知道起点是哪个时）：别的点照常打印，但每个点只打一次（比如聆听时每发一包都走到的点，不刷屏）
static inline int fsw_before_start(int id) {
    return FSW_START_ID >= 0 && id != FSW_START_ID && !__atomic_load_n(&fsw_started, __ATOMIC_RELAXED);
}
static inline void fsw_print_pre(int id, int32_t v) {
    if (!__atomic_exchange_n(&fsw_pre[id], (unsigned char)1, __ATOMIC_RELAXED)) fsw_print(id, esp_timer_get_time(), v);
}

// 上一轮的尾巴：这个点可能在清零之后才走到（FSW_TAIL_MASK），清零后还没过 FSW_TAIL_US、下一轮起点还没到
static inline int fsw_tail(int id, int64_t now) {
    return ((((unsigned long)FSW_TAIL_MASK) >> id) & 1UL) && fsw_before_start(id)
        && now <= __atomic_load_n(&fsw_tail_until, __ATOMIC_RELAXED);
}

static inline int fsw_follow(int id) { return (int)((((unsigned long)FSW_FOLLOW_MASK) >> id) & 1UL); }
// 起点之后走到了跟着起点的那几步以外的点（比如收到识别结果）：之后再报起点，不再重新记那几步
static inline void fsw_passed(int id) {
    if (FSW_FOLLOW_MASK && id != FSW_START_ID && !fsw_follow(id) && !fsw_before_start(id))
        __atomic_store_n(&fsw_past, (unsigned char)1, __ATOMIC_RELAXED);
}
// 又报了一次起点：跟着起点的那几步重新记
static inline void fsw_rearm(int id) {
    if (!FSW_FOLLOW_MASK || id != FSW_START_ID || __atomic_load_n(&fsw_past, __ATOMIC_RELAXED)) return;
    for (int i = 0; i < FSW_MAX_ID; i++)
        if (fsw_follow(i)) __atomic_store_n(&fsw_done[i], (unsigned char)0, __ATOMIC_RELAXED);
}

// 每次都记
static inline void fsw_mark(int id, int32_t v) {
    if (!fsw_ok(id)) return;
    fsw_seen(id);
    fsw_rearm(id);
    int64_t now = esp_timer_get_time();
    if (fsw_before_start(id) && !fsw_tail(id, now)) { fsw_print_pre(id, v); return; }
    fsw_passed(id);
    fsw_print(id, now, v);
}

// 一轮只记第一次。这一轮的起点还没到时打印一次、但不算"记过"：上一轮的尾巴（比如打断回答后上一句的结束消息）
// 在新一轮开始后才到，不能把这一轮真正的那一次吞掉（码表按起点之前的不算，丢掉它）。
// 起点本身每次都记：唤醒词说完、话说到一半停一下都会判一次"说完"，码表取第一下感觉到的结果之前的最后一次
static inline void fsw_mark_once(int id, int32_t v) {
    if (!fsw_ok(id)) return;
    fsw_seen(id);
    if (id == FSW_START_ID) {
        fsw_rearm(id);
        __atomic_store_n(&fsw_done[id], (unsigned char)1, __ATOMIC_RELAXED);
        fsw_print(id, esp_timer_get_time(), v);
        return;
    }
    if (fsw_before_start(id)) {
        fsw_print_pre(id, v);
        return;
    }
    if (!__atomic_exchange_n(&fsw_done[id], (unsigned char)1, __ATOMIC_RELAXED)) {
        fsw_passed(id);
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
    fsw_seen(id);
    int64_t now = esp_timer_get_time();
    if (fsw_tail(id, now)) {  // 上一轮的尾巴（清零时补打过的那次之后又走到了）：直接补打这一次，不然下一轮起点会把它清掉
        printf("FSW,LATE,P%d,%lld,%ld\n", id, (long long)now, (long)__atomic_add_fetch(&fsw_tail_n[id], 1, __ATOMIC_RELAXED));
        return;
    }
    // 可能晚到的点在清零 FSW_TAIL_US 之后、下一轮起点之前才走到：打一行 TAIL 告诉码表（每轮一次），码表试一次时会点名它晚了多久
    if (((((unsigned long)FSW_TAIL_MASK) >> id) & 1UL) && fsw_before_start(id) && __atomic_load_n(&fsw_tail_until, __ATOMIC_RELAXED)
        && !__atomic_exchange_n(&fsw_pre[id], (unsigned char)1, __ATOMIC_RELAXED))
        printf("FSW,TAIL,P%d,%lld,0\n", id, (long long)now);
    if (!fsw_before_start(id)) fsw_passed(id);
    __atomic_store_n(&fsw_last_us[id], now, __ATOMIC_RELAXED);
    __atomic_fetch_add(&fsw_last_n[id], 1, __ATOMIC_RELAXED);
}
// 起点之前不打印（那是这一轮开始之前的，比如唤醒提示音放完）
static inline void fsw_flush_last(int id) {
    if (!fsw_ok(id)) return;
    int64_t us = __atomic_exchange_n(&fsw_last_us[id], (int64_t)0, __ATOMIC_RELAXED);
    int32_t n = __atomic_exchange_n(&fsw_last_n[id], (int32_t)0, __ATOMIC_RELAXED);
    if (us && !fsw_before_start(id)) fsw_print(id, us, n);
}

// 一轮开始：清零（"记最后一次"还没打印的先补打一行 LATE，不然这一轮就少了这个点；这一轮连起点都没有就不补打）
// 这一轮有起点时，之后 FSW_TAIL_US 内可能在清零之后才走到的点（FSW_TAIL_MASK）还算这一轮的尾巴
static inline void fsw_round_reset(void) {
    int had = FSW_START_ID >= 0 && __atomic_load_n(&fsw_started, __ATOMIC_RELAXED);
    for (int i = 0; i < FSW_MAX_ID; i++) {
        __atomic_store_n(&fsw_done[i], (unsigned char)0, __ATOMIC_RELAXED);
        __atomic_store_n(&fsw_pre[i], (unsigned char)0, __ATOMIC_RELAXED);
        int64_t us = __atomic_exchange_n(&fsw_last_us[i], (int64_t)0, __ATOMIC_RELAXED);
        int32_t n = __atomic_exchange_n(&fsw_last_n[i], (int32_t)0, __ATOMIC_RELAXED);
        if (us && !fsw_before_start(i)) printf("FSW,LATE,P%d,%lld,%ld\n", i, (long long)us, (long)n);
        __atomic_store_n(&fsw_tail_n[i], n, __ATOMIC_RELAXED);
    }
    __atomic_store_n(&fsw_armed, (unsigned char)0, __ATOMIC_RELAXED);
    __atomic_store_n(&fsw_started, (unsigned char)0, __ATOMIC_RELAXED);
    __atomic_store_n(&fsw_past, (unsigned char)0, __ATOMIC_RELAXED);
    int64_t now = esp_timer_get_time();
    __atomic_store_n(&fsw_tail_until, had ? now + FSW_TAIL_US : (int64_t)0, __ATOMIC_RELAXED);
    printf("FSW,RESET,%lld,0\n", (long long)now);
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

def _bits(ks):
    return sorted({int(str(k).lstrip("P")) for k in ks or [] if str(k).lstrip("P").isdigit() and 0 < int(str(k).lstrip("P")) < 32})


def header_text(start=None, tail=None, follow=None):
    """计时点工具的内容：知道这一轮的起点是哪个计时点就写进去（"一轮只记第一次"的点起点之前不占名额）；
    可能在每轮清零之后才走到的点（见 tail_points）写进 FSW_TAIL_MASK；跟着起点重新记的点（见 follow_points）写进 FSW_FOLLOW_MASK。"""
    out = HEADER if start is None else HEADER.replace("#define FSW_START_ID -1", f"#define FSW_START_ID {int(start)}", 1)
    for macro, ks in (("FSW_TAIL_MASK", tail), ("FSW_FOLLOW_MASK", [k for k in follow or [] if str(k).lstrip("P") != str(start)])):
        ids = _bits(ks)
        if ids and start is not None:
            out = out.replace(f"#define {macro} 0UL", f"#define {macro} 0x{sum(1 << i for i in ids):X}UL  // {' '.join(f'P{i}' for i in ids)}", 1)
    return out


def follow_points(steps, flow=None):
    """跟着起点的点（识别时码表按流程标的 follow，见 identify.follow_start_points；老的步骤表看识别结果里的 follow）：
    起点和第一个结果之间、"只记第一次"的点。起点一轮报好几次时，每报一次起点它们重新记一次。返回 [P 编号]。"""
    names = set((flow or {}).get("follow") or [])
    return [point_id(s, steps) for s in steps if (s.get("follow") or s.get("p") in names) and s.get("line") != "start"]


LATE_SECS = 1  # 和 measure.LATE_US、头文件里的 FSW_TAIL_US 一样：清零之后这么久内到的尾巴还算上一轮
PLAN_VER = 3  # 插入提示词改过（决策 118 加了 FSW_MARK_ONCE_AFTER；插点改成以"插在哪一句之前"为主）：之前存的插入方案不再沿用
HELPERS = {"include", "reset", "arm", "flush"}
LINE_OK = re.compile(r'^\s*(//[^\n]*|#include\s+["<][^"<>\n]*fw_stopwatch\.h[">]\s*(//[^\n]*)?|FSW_[A-Z_]+\([^;\n]*\);\s*(//[^\n]*)?)?$')
ONCE = {"once": "一轮只经过一次（只记第一次）", "first": "一轮只记第一次", "last": "一轮里记最后一次", "every": "每次都记"}
EXTRA_GENERATED = {"sdkconfig.fsw-board"}

PROMPT = """你是"固件码表"工具里的计时点插入助手。固件源码就在当前目录，只能读，不能改；真正插入由码表来做。

## 用户已确认，要在这些地方插计时点
{table}

## 码表提供的计时点工具
码表会把头文件 {header} 放进项目，里面有这些语句（编号用上面表里的数字，附加值没有就写 0）：
- FSW_MARK(编号, 附加值);  每次都记一行
- FSW_MARK_ONCE(编号, 附加值);  一轮只记第一次
- FSW_KEEP_LAST(编号);  高频位置用：只在内存里记最后一次，再在之后的某个计时点处用 FSW_FLUSH_LAST(编号); 打印出来。
  表里写"一轮里记最后一次"的点用它，不用 FSW_MARK（FSW_MARK 每次都打一行，逐帧、逐包的地方会打出成百上千行）
- FSW_ROUND_RESET();  一轮开始时清零"只记第一次"的标记，放在每轮开始、一轮只经过一次的地方（比如进入聆听状态的那一支、读到卡）。
  不能放在主循环、事件循环、任务循环的循环体里（每来一个事件、每一包、每一下定时都会转一圈，一轮会清零几十上百次），
  也不能放在逐帧、逐包、定时执行的地方。FSW_ARM(); 同样只放在一轮只经过一次的地方
- FSW_ARM(); 和 FSW_MARK_ONCE_ARMED(编号, 附加值);  只在 FSW_ARM() 之后才记；FSW_ARM(); 单独一行，不能套在 if 里
- FSW_MARK_ONCE_AFTER(编号, 前一步编号, 附加值);  这一轮走到"前一步"（那个用 FSW_MARK_ONCE 记的计时点）之后才记，只记第一次。
  会被上一轮剩下的东西提前触发的点要用它：比如"收到第一段声音"，上一句回答剩下的一包声音可能在新一轮聆听时才到，
  用 FSW_MARK_ONCE 会记成那一包，这一轮真正的第一包反而记不到。前一步选这一轮里它前面、一定会先发生的那个计时点（比如"收到开口指令"）

{values}## 你的任务
读代码，确定每个计时点具体插在哪一行的后面（after_line 是原文件现在的行号，0 表示插在文件最前面），写出要插的几行（带上和周围一样的缩进）。
- 每个计时点插两行：一行中文注释（// 计时点 P编号：中文名），一行计时点语句。辅助语句（清零、武装、打印最后一次）也各配一行注释。
- 每个用到计时点的文件，在开头的 #include 附近加一行 #include，路径要让编译器能找到 {header}。这一行只写 #include，说明另起一行写注释。
- 只能插注释、#include 和 FSW_ 开头的计时点语句，每行一句；不能加 if、条件判断、变量、赋值或别的代码，不能改、不能删已有的行。
  不用自己筛"只记回复的声音、不记提示音""不记上一轮剩下的"：码表的计时点只认这一轮起点之后记到的（起点之前记到的码表不算），
  提示音、上一轮剩下的不会被算进这一轮；要"某一步之后才记"就用 FSW_MARK_ONCE_AFTER，不写判断。
- 不要插在宏定义、多行语句、if 条件、初始化列表的中间；插在 return / break 之后的执行不到。
- 表里写"这一句之前"的，插在那一句的紧前面、和它同一层（after_line 就是它上一行）；写"这一句之后"的，插在那一句（块里最后一句）的紧后面、同一层。不要跨过 }}、else 或判断那一行，跨过就到了别的分支或块外。
- 计时点必须和它对应的那一句在同一个代码块里：插的位置和那一句之间不能隔着 }}、{{、else、case 或 default（差一行就可能进了旁边另一支，永远走不到）。
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


def build_prompt(steps, header, needs=None, extra=None):
    nb = _needs_by_id(steps, needs)
    names = {f"P{number(s['p'], steps)}": s["n"] for s in steps}
    vrows = "\n".join(f"- {k}（{names.get(k, k)}）：附加值记{v['what']}；正常时是{v['ok']}"
                      f"{'（标准里记的是 ' + str(v['value']) + '）' if v.get('value') is not None else ''}"
                      f"{'；' + v['how'] if v.get('how') else ''}；用来查：{'、'.join(v.get('for') or [])}" for k, v in nb.items())
    rows = "\n".join(
        f"- P{number(s['p'], steps)}（编号 {number(s['p'], steps)}）{s['n']}：{s.get('path') or s.get('f')} 第 {s['l']} 行"
        f"{'这一句之前' if s.get('where') == 'before' else '这一句之后' if s.get('where') == 'after' else ''}，"
        f"{ONCE.get(s.get('once'), '一轮只记第一次')}"
        + ("（跟着起点：起点一轮会报好几次，码表的工具每报一次起点就让它重新记，照样写 FSW_MARK_ONCE，不用自己判断）"
           if s.get("follow") and s.get("once") in ("once", "first") else "")
        + (f"；为什么在这：{s['why']}" if s.get("why") else "")
        for s in steps)
    out = PROMPT.format(table=rows, header=header, values=VALUES.format(rows=vrows) if nb else "",
                        values_out=VALUES_OUT if nb else "")
    if extra:  # 上一次试的时候码表看出来的问题（比如每轮半路被清零）：这次别再犯
        out += f"\n\n## 上一次插完试的时候发现的问题\n{extra}。这次请避开。"
    return out


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


KEEP_CALL = re.compile(r"FSW_KEEP_LAST\(\s*(\d+)\s*\)")
FLUSH_CALL = re.compile(r"FSW_FLUSH_LAST\(\s*(\d+)\s*\)")
ARM_CALL = re.compile(r"FSW_ARM\(\s*\)")
RESET_CALL = re.compile(r"FSW_ROUND_RESET\(\s*\)")


def _needs_lost(out, dropped):
    """去掉不合规的那几条以后，留下的计时点还能不能照常工作：留下的"武装后才记"要有武装那一句，
    留下的"记最后一次"原来有打印那一句（被去掉了）就不行，清零那一句全被去掉也不行。返回说明（空 = 没缺）。"""
    def text(xs):
        return "\n".join(l for x in xs for l in x.get("lines") or [] if isinstance(l, str))
    kept, gone, lost = text(out), text(dropped), []
    if any(m.group(1) == "MARK_ONCE_ARMED" for m in MARK_CALL.finditer(kept)) and not ARM_CALL.search(kept):
        lost.append("用了「武装后才记」的计时点，但武装那一句被去掉了")
    for n in sorted({int(m.group(1)) for m in KEEP_CALL.finditer(kept)}):
        if n in {int(m.group(1)) for m in FLUSH_CALL.finditer(gone)} and n not in {int(m.group(1)) for m in FLUSH_CALL.finditer(kept)}:
            lost.append(f"P{n} 记最后一次，打印它的那一句被去掉了")
    if RESET_CALL.search(gone) and not RESET_CALL.search(kept):
        lost.append("每轮清零那一句被去掉了")
    return lost


ROUND_ONCE = re.compile(r"FSW_(?:ROUND_RESET|ARM)\(\s*\)")


def _once_place(root, rel, after, lines, steps):
    """清零（FSW_ROUND_RESET）、武装（FSW_ARM）一轮只该经过一次：插在第 after 行后面的这几句如果落在会一直转的地方，返回说明。
    看结构，不认文件名、函数名：
    - 直接在循环体里（不在里面哪一支里）：主循环、事件循环、任务循环每来一个事件、每一包、每一下定时都转一圈；
    - 直接写在函数体里（不在哪一支里），而反复执行的计时点（一轮记第一次、最后一次、每次都记的那几步，起点除外）
      也直接写在这个函数体里：这个函数是逐帧、逐包执行的。
    放在循环里某一支（比如"进入聆听"那一支）、或只在某个事件才调用的函数里，不算。"""
    if not any(isinstance(l, str) and ROUND_ONCE.search(l) for l in lines):
        return None
    from fsw import structure
    try:
        proj = structure.Project(root)
        src = proj.file(rel)
        if src is None:
            return None
        at = src.pos(after, "after") if after else 0
        loops, _inner, _ = src.context(at)
        what = "清零" if any(isinstance(l, str) and "ROUND_RESET" in l for l in lines) else "武装"
        here = src.chain(at)
        if loops and here[:1] == loops[:1]:
            b = src.blocks[loops[0]]
            return (f"{rel} 第 {after} 行后面的{what}放在第 {src.line_of(b['open'])} 行「{structure._cond_text(b['head'])}」这个循环体里，"
                    f"每转一圈就{what}一次，一轮会{what}很多次；要放在一轮只经过一次的地方（比如进入聆听状态的那一支）")
        for s in steps:
            if str(s.get("once") or "") not in ("first", "last", "every") or (s.get("path") or s.get("f")) != rel \
                    or s.get("line") == "start":  # 起点那一步：每轮从它开始，清零放在它旁边是对的
                continue
            try:
                pos = src.pos(int(s.get("l")), s.get("where") or "before")
            except (TypeError, ValueError, IndexError):
                continue
            mine = src.chain(pos)
            if here and mine and here[0] == mine[0] and src.blocks[here[0]]["kind"] in ("function", "lambda"):
                return (f"{rel} 第 {after} 行后面的{what}和「{s.get('n')}」在同一段代码里，那里逐帧、逐包反复执行，"
                        f"一轮会{what}很多次；要放在一轮只经过一次的地方（比如进入聆听状态的那一支）")
        if here and src.blocks[here[0]]["kind"] in ("function", "lambda"):
            b = src.blocks[here[0]]
            if re.search(r"\bswitch\b|\belse\b", src.code[at:b["close"]]):
                name = b.get("name") or "这个函数"
                return (f"{rel} 第 {after} 行后面的{what}放在「{name}」最开头、不在哪一支里，而这个函数后面按情况分开走（switch 或 if/else），"
                        f"不管是哪种情况都会先{what}一次，一轮会在半路被{what}；要放进一轮开始（或一轮结束）的那一支里")
    except Exception:  # 结构读不懂：不拦（插的内容照样只有计时点语句）
        return None
    return None


_CROSS = re.compile(r"[{}]|\belse\b|\bcase\b|\bdefault\s*:")


def _crosses(code):
    """一段代码（已去掉注释、字符串）里有没有跨出或跨进代码块：出现没配对的 } 或 {、或同一层的 else / case / default。
    中间夹着一整个配对好的 {…}（比如旁边一个完整的 if 块）不算跨。返回跨过的那个记号，没有就 None。"""
    depth = 0
    for m in _CROSS.finditer(code):
        tok = m.group().strip()
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth < 0:
                return "}"
        elif depth == 0:
            return tok.rstrip(":").strip()
    return "{" if depth > 0 else None


def _same_block(root, rel, after, s, cache):
    """计时点有对应的那一句（表里的第几行、之前/之后）：插的位置要和那一句在同一个代码块里，中间不能隔着 { } else case default。
    返回 None（没问题、或看不懂不拦）或 (说明, 该挪到第几行后面 / None=说不准、要问 AI)。只看结构，不认文件名、函数名。"""
    from fsw import structure
    if (s.get("path") or s.get("f")) != rel:
        return None  # AI 放到别的文件：这里不判
    try:
        line, where = int(s.get("l")), (s.get("where") or "before")
    except (TypeError, ValueError):
        return None
    try:
        if rel not in cache:
            cache[rel] = structure.Project(root).file(rel)
        src = cache[rel]
        if src is None or not 1 <= line <= len(src.lines) or not 0 <= after <= len(src.lines):
            return None
        first = structure.stmt_start(src, line) if where != "after" else line
        c = src.clines[first - 1].strip()
        if not c or re.match(r"(?:\}|else\b|case\b|default\s*:)", c) or src.lines[first - 1].strip().startswith("#"):
            return None  # 那一句本身是 } / else / case 开头、空行、编译开关：怎么算同一块说不清，不拦
        tgt = src.pos(first, "before") if where != "after" else src.pos(line, "after")
        ins = src.starts[after] if after else 0  # 插在第 after 行后面 = 第 after+1 行的开头
        lo, hi = sorted((ins, tgt))
        crossed = _crosses(src.code[lo:hi])
        if not crossed and src.chain(ins)[:1] == src.chain(tgt)[:1]:
            return None
        what = "之前" if where != "after" else "之后"
        tl = src.lines[line - 1].strip()
        why = (f"{rel} 第 {after} 行后面插的「{s.get('n')}」和它对应的第 {line} 行「{tl[:60]}」不在同一个代码块里"
               f"（中间隔着「{crossed or '块的边界'}」），会落进旁边另一支或块外，可能永远走不到；"
               f"要插在第 {line} 行{what}、同一层")
        if where != "after":
            # 对应的那一句是一整个 if / switch 的开头（点要放在整个判断前面，见 identify.lift_alt_branches）：它前面就是同一层
            to = first - 1 if structure.real_stmt(src, first) or structure.call_in_if(src.clines[first - 1]) \
                or re.match(r"(?:if|switch)\b", c) else None
        else:
            to = line if structure.real_stmt(src, line) and not structure._UNFINISHED.search(c) else None
        if to is not None:
            t2 = src.starts[to] if to else 0
            a, b = sorted((t2, tgt))
            if _crosses(src.code[a:b]) or src.chain(t2)[:1] != src.chain(tgt)[:1]:
                to = None
        return why, to
    except Exception:  # 结构读不懂：不拦
        return None


SIDE_LINE = "face"  # 顺带发现的那条线（见 identify.to_page）：不算进要测的这一轮怎么结束


def _main_steps(steps):
    return [s for s in steps if s.get("line") != SIDE_LINE]


def _hinted(steps, hint):
    """试一次看出来的"一轮其实到这一步才完"（{"end": P 编号, "name": 名字}）：步骤还在、名字对得上、一轮只走一次才用。"""
    if not isinstance(hint, dict) or not hint.get("end"):
        return None
    s = next((s for s in _main_steps(steps) if point_id(s, steps) == hint["end"]), None)
    if s and s.get("n") == hint.get("name") and s.get("once") == "once" and s.get("line") != "start":
        return s
    return None


_SIGNAL = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b")  # 事件位、标志这类全大写带下划线的名字（MAIN_EVENT_X、EVT_DONE）
_CALLEE = re.compile(r"(?<![\w])([A-Za-z_]\w*)\s*\(")


def _stmt_text(src, line):
    first = structure_stmt_start(src, line)
    last = line
    while last < len(src.clines) and re.search(r"(?:,|\(|&&|\|\||[^=!<>]=)$", src.clines[last - 1].rstrip()):
        last += 1
    return " ".join(src.clines[k - 1].strip() for k in range(first, last + 1))


def structure_stmt_start(src, line):
    from fsw import structure
    return structure.stmt_start(src, line)


def _registered_signals(proj, names):
    """这几个回调名字被赋成的 lambda（name = [..](..){ ... }）里出现的事件位、标志（全大写带下划线的名字）。只看文本结构，不认名字。"""
    out = set()
    for rel in proj.sources():
        f = proj.file(rel)
        if f is None:
            continue
        for n in names:
            for m in re.finditer(rf"(?<![\w]){re.escape(n)}\s*=\s*\[", f.code):
                k = f.code.find("{", m.end())
                b = next((b for b in f.blocks if b["open"] == k), None) if k >= 0 else None
                if b is not None:
                    out |= set(_SIGNAL.findall(f.code[b["open"]:b["close"]]))
    return out


def repeat_driven(root, steps):
    """一轮只记一次（once）的点，插的那一句其实是跟着一个"一轮会走好几次"的点（每次都记、记最后一次、记第一次）触发的：
    那个点那一句调用的回调，被赋成的 lambda 里置了某个事件位 / 标志，这个点就在判断这个事件位的那一支里；
    或者这个点就在那一句直接调用的函数里。那个点走几次，它就跟着走几次（10-26 8783：每次播放放空都会置"放空"事件，
    主循环里"上报播放结束"跟着走一次，回复分几句播时一轮里会走好几次）。
    返回 {点编号: {"by": 触发它的点编号, "why": 说明}}。只看代码结构，不认文件名、函数名；读不懂就当没有。"""
    from fsw import structure
    out = {}
    try:
        proj = structure.Project(Path(root).resolve())
    except Exception:
        return out
    main = _main_steps(steps)
    multi = [x for x in main if x.get("once") in ("every", "last", "first")]
    for d in multi:
        try:
            dsrc = proj.file(str(d.get("path") or d.get("f") or ""))
            if dsrc is None:
                continue
            stmt = _stmt_text(dsrc, int(d.get("l")))
        except (TypeError, ValueError, IndexError):
            continue
        callees = [n for n in _CALLEE.findall(stmt) if n not in structure.KEYWORDS and not n.startswith("FSW_")]
        if not callees:
            continue
        sigs = _registered_signals(proj, callees)
        for e in main:
            if e is d or e.get("once") != "once" or e.get("line") == "start" or point_id(e, steps) in out:
                continue
            rel = str(e.get("path") or e.get("f") or "")
            try:
                esrc = proj.file(rel)
                if esrc is None:
                    continue
                pos = esrc.pos(int(e.get("l")), e.get("where") or "before")
            except (TypeError, ValueError, IndexError):
                continue
            heads = [esrc.blocks[i]["head"] for i in esrc.chain(pos)]
            hit = next((g for g in sigs for h in heads if re.search(rf"\b{re.escape(g)}\b", h)), None)
            func = structure.func_at(esrc, int(e.get("l")))[0]
            direct = func and (func.split("::")[-1] in callees)
            if hit or direct:
                why = (f"「{e.get('n')}」在判断 {hit} 的那一支里，{hit} 是「{d.get('n')}」那一句（{stmt[:50]}）每走一次就置一次的"
                       if hit else f"「{e.get('n')}」在「{d.get('n')}」那一句直接调用的 {func} 里")
                out[point_id(e, steps)] = {"by": point_id(d, steps), "why": why + f"：「{d.get('n')}」一轮会走好几次，它也跟着走好几次"}
    return out


def effective_steps(root, steps):
    """插计时点、定一轮结束时用的步骤：跟着"一轮会走好几次"的点触发的那几个（见 repeat_driven）当成"每次都记"，
    不拿它当一轮结束、不在它那里清零；它是人感觉到的结束（felt=last）时，码表取这一轮最后那次。返回 (步骤, {点编号: 说明})。"""
    if root is None:
        return steps, {}
    driven = repeat_driven(root, steps)
    if not driven:
        return steps, {}
    return [dict(x, once="every", driven_by=driven[point_id(x, steps)]["by"]) if point_id(x, steps) in driven else x
            for x in steps], driven


def round_end(steps, hint=None):
    """一轮的结束那一步：要测的这条线上（顺带的那条不算），表里（按流程先后）最后一个"一轮只经过一次"、不是可选、不是起点的计时点；
    一轮只经过一次的全是可选的，就用其中最后那个（不然没处清零，"记最后一次"的点一直报不出来）。
    试一次看出来一轮其实到后面某一步才完（hint，见 _hinted）就用那一步。没有就 None。"""
    h = _hinted(steps, hint)
    if h:
        return h
    main = _main_steps(steps)
    st = next((s for s in main if s.get("line") == "start"), None)
    ends = [s for s in main if s is not st and not s.get("opt") and s.get("once") == "once"]
    if not ends:  # 一轮只经过一次的全标成了可选（识别说"不一定走"）：还是用最后那个，一轮结束总得有个地方
        ends = [s for s in main if s is not st and s.get("once") == "once"]
    return ends[-1] if ends else None


def _seg_pairs(flow):
    out = []
    for g in (flow or {}).get("segs") or []:
        a, b = (g.get("a"), g.get("b")) if isinstance(g, dict) else (tuple(g) + (None, None))[:2]
        if a and b:
            out.append((str(a), str(b)))
    return out


def end_check(steps, flow=None):
    """核对"一轮结束那一步"（round_end）和识别结果对不对得上，对不上就别按它放清零。返回 [大白话的矛盾]，空 = 对得上。
    flow：{"end": 识别时说这条线到哪一步结束（名字）, "segs": 分段 [{a, b}], "hint": 试一次看出来的结束}。
    - 识别说的结束那一步是一轮只走一次的、不是可选的，却不是码表挑的那一步（它后面还有一轮只走一次的步骤）；
    - 分段里结束那一步后面还接着一轮只走一次的步骤；
    - 分段说的先后和步骤表里排的先后，在结束那一步上正好相反。
    一轮会走好几次的步骤（比如"播放全部放完"）是结束：不算矛盾，它可能在清零之后才走到，见 tail_points。"""
    flow = flow or {}
    end = round_end(steps, flow.get("hint"))
    if not end or _hinted(steps, flow.get("hint")):
        return []
    main = _main_steps(steps)
    by_p = {s["p"]: s for s in main}
    order = [s["p"] for s in main]
    out = []
    name = str(flow.get("end") or "").strip()
    named = next((s for s in main if str(s.get("n") or "").strip() == name), None) if name else None
    if named is not None and named is not end and named.get("once") == "once" and not named.get("opt") and named.get("line") != "start":
        later = "在它前面" if order.index(named["p"]) > order.index(end["p"]) else "在它后面"
        out.append(f"识别时说这一轮到「{name}」结束，可按步骤先后，一轮只走一次的最后一步是「{end['n']}」（{later}），两边对不上")
    for a, b in _seg_pairs(flow):
        if a == end["p"] and b in by_p and by_p[b].get("once") == "once" and not by_p[b].get("opt") and by_p[b].get("line") != "start":
            out.append(f"分段里「{end['n']}」后面还接着一轮只走一次的「{by_p[b]['n']}」，说明「{end['n']}」不是一轮的最后一步")
        elif end["p"] in (a, b) and a in by_p and b in by_p and order.index(a) > order.index(b):
            out.append(f"分段说「{by_p[a]['n']}」在「{by_p[b]['n']}」前面，可步骤表里排的先后正好反过来")
    return list(dict.fromkeys(out))


def tail_points(steps, flow=None):
    """可能在每轮结束那一步之后才走到的点（清零之后才打出来，会算不进这一轮）。只看流程，不认名字：
    - 步骤表里排在结束那一步后面的（要测的这条线上）；
    - 一轮会走好几次的（"记最后一次""每次都记"）：从它到结束那一步之间，没有一轮只走一次的步骤卡着
      （有的话，它最后那次一定在那一步之前），最后那次和结束谁先谁后流程说不准，比如最后一段声音还在放、设备已经回到等待；
    - 分段里它是一段的终点、后面不再接别的（和结束那一步并排的终点）。
    起点、结束那一步本身、"一轮只记第一次"的不算（第一次一定在结束之前）。返回 [step]，按步骤表的先后。"""
    flow = flow or {}
    end = round_end(steps, flow.get("hint"))
    main = _main_steps(steps)
    if end is None or end not in main:
        return []
    i = main.index(end)
    pairs = _seg_pairs(flow)
    heads = {a for a, _ in pairs}
    sinks = {b for _, b in pairs if b not in heads}
    out = []
    for k, s in enumerate(main):
        if s is end or s.get("line") == "start":
            continue
        if k > i:
            out.append(s)
        elif s.get("once") in ("last", "every") and all(x is end or x.get("opt") or x.get("once") in ("last", "every", "first")
                                                         for x in main[k + 1:i + 1]):
            out.append(s)
        elif s.get("once") != "first" and s["p"] in sinks and pairs:
            out.append(s)
    return out


def _before_reset(root, s, auto):
    """这个点在码表放的清零那一句之前、同一个函数里按顺序执行（不在循环里）：一定先于清零。看不出来返回 False。"""
    from fsw import structure
    try:
        if (s.get("path") or s.get("f")) != auto["file"]:
            return False
        src = structure.Project(root).file(auto["file"])
        line = int(s.get("l"))
        if src is None or not src.balanced or line > auto["after_line"]:
            return False
        a = src.pos(line, s.get("where") or "before")
        b = src.starts[auto["after_line"]] if auto["after_line"] < len(src.starts) else len(src.code)
        la, ia, _ = src.context(a)
        lb, ib, _ = src.context(b)
        ca, cb = src.chain(a), src.chain(b)
        # 点所在的那一块包着清零（清零在它里面、或更深的一支里），不是旁边已经关上的另一支；点不直接在 switch 里（各 case 互斥）
        return ia is not None and ia == ib and not la and a < b and bool(ca) and ca[0] in cb \
            and not src.blocks[ca[0]]["head"].startswith("switch")
    except Exception:
        return False


def after_reset(root, steps, auto, flow=None):
    """核对码表放的清零：这一轮哪几个点可能在清零之后才打出来。按流程先后挑出可能晚于结束的点（tail_points），
    同一个函数里清零之前按顺序执行的算核对过了。返回 [{"step", "name", "kind", "why"}]。"""
    out = []
    end = round_end(steps, (flow or {}).get("hint"))
    for s in tail_points(steps, flow):
        if auto and _before_reset(root, s, auto):
            continue
        kind = {"last": "last", "every": "every"}.get(s.get("once"), "once")
        why = ("步骤表里排在每轮结束那一步后面" if _main_steps(steps).index(s) > _main_steps(steps).index(end) else
               "一轮会走好几次，最后那次和每轮结束谁先谁后流程里说不准" if kind != "once" else
               "和每轮结束那一步并排（分段里是另一段的终点），谁先谁后说不准")
        out.append({"step": point_id(s, steps), "name": s.get("n"), "kind": kind, "why": why})
    return out


def _indent(src, line):
    raw = src.lines[line - 1]
    return raw[:len(raw) - len(raw.lstrip())]


def _next_stmt(src, line):
    """第 line 行后面第一句有代码的行号（跳过空行、注释、单独的 {），没有就 None。"""
    for k in range(line + 1, len(src.clines) + 1):
        c = src.clines[k - 1].strip()
        if c and c != "{":
            return k
    return None


def _reach(root, calls, targets, depth=3, limit=60):
    """从这几个函数出发，顺着函数体里的调用往下最多 depth 层，走得到 targets 里的哪几个函数（按名字，只看文本，不认文件名）。"""
    from fsw import structure
    if root is None:
        return set()
    proj = structure.Project(root)
    raws = {}
    for rel in proj.sources():
        try:
            raws[rel] = (proj.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    short = lambda n: (n or "").split("::")[-1]
    want = {short(t): t for t in targets}
    got, seen, cur = set(), set(), [short(c) for c in calls]
    for _ in range(depth + 1):
        nxt = []
        for name in cur:
            if name in seen or len(seen) > limit:
                continue
            seen.add(name)
            if name in want:
                got.add(want[name])
            pat = re.compile(rf"(?<![\w]){re.escape(name)}\s*\(")
            for rel, raw in raws.items():
                if not pat.search(raw):
                    continue
                f = proj.file(rel)
                for b in f.blocks if f is not None else []:
                    if b["kind"] == "function" and short(b["name"]) == name:
                        nxt += [n for n in re.findall(r"(?<![\w])([A-Za-z_]\w*)\s*\(", f.code[b["open"]:b["close"]])
                                if n not in structure.KEYWORDS]
        cur = list(dict.fromkeys(nxt))
    return got


def _state_case(src, rel, first, line, root=None):
    """结束那一句是"把状态设成某个值"（参数里有个名字，同一个文件里正好有一处 case 这个名字:）：清零放进这个 case 的第一句前面——
    进入这个状态的每条路（自动、手动、唤醒）都经过它，一轮一次。返回 (插在第几行后面, 缩进, 说明) 或 None。"""
    from fsw import structure
    stmt = " ".join(src.clines[k - 1].strip() for k in range(first, line + 1))
    m = re.search(r"\((.*)\)", stmt)
    if not m:
        return None
    names = {n for n in re.findall(r"[A-Za-z_]\w*", m.group(1)) if n not in structure.KEYWORDS}
    hits = []
    for k, c in enumerate(src.clines, 1):
        cm = re.match(r"\s*case\s+(?:[\w:]*::)?([A-Za-z_]\w*)\s*:", c)
        if cm and cm.group(1) in names:
            hits.append((k, cm.group(1), c[cm.end():].strip()))
    if len(hits) > 1:  # 好几处 case 这个值：挑结束那一句调用的函数（往下最多三层）走得到的那个函数里的
        calls = [n for n in re.findall(r"(?<![\w])([A-Za-z_]\w*)\s*\(", stmt) if n not in structure.KEYWORDS]
        reach = _reach(root, calls, {structure.func_at(src, k)[0] for k, _, _ in hits} - {None})
        mine = [h for h in hits if structure.func_at(src, h[0])[0] in reach]
        if len(mine) != 1:
            return {"unsure": f"{rel} 里有 {len(hits)} 处「case {hits[0][1]}:」，从「{src.lines[first - 1].strip()[:50]}」"
                              f"{'一处都看不出走得到' if not mine else '好几处都走得到'}，说不准放哪一处"}
        hits = mine
    if len(hits) != 1:
        return None
    k, name, rest = hits[0]
    if rest and rest != "{":
        return None  # case 那一行后面还写着语句：说不清插哪
    s1 = _next_stmt(src, k)
    if s1 is None or re.match(r"(?:case\b|default\s*:|\})", src.clines[s1 - 1].strip()):
        return None  # 和下一个 case 共用、或空的 case
    loops, inner, _ = src.context(src.pos(s1, "before"))
    if loops or inner is None:
        return None  # 在循环里（每转一圈都走）、或不在函数里
    return s1 - 1, _indent(src, s1), f"{rel} 第 {k} 行「case {name}:」这一支（进入这个状态时走一次）"


def place_reset(root, steps, flow=None, warn=None):
    """每轮清零由码表按找到的关键步骤放，不用 AI 写的位置。一轮的结束那一步（见 round_end）：
    - 它那一句是把状态设成某个值、同一个文件里正好有一处处理这个值的 case：放进那个 case 的第一句前面；
    - 否则放在结束那一步的计时点紧后面（同一块、同一个位置）。
    放好后照样用 AI 插法的那几条检查核对（不在一直转的地方、不在函数开头分支前）；说不准就返回 None（用 AI 的位置，加一句提醒）。
    结束那一步和识别结果对不上（见 end_check）：不放，返回 None，矛盾写进 warn。
    试一次看出来一轮其实到后面某一步才完（flow["hint"]）：放在那一步那一句的紧后面。
    返回 {"file", "after_line", "lines", "where"}。只看结构和表里的步骤，不认文件名、函数名。"""
    from fsw import structure
    warn = [] if warn is None else warn
    flow = flow or {}
    bad = end_check(steps, flow)
    if bad:
        warn += bad
        return None
    end = round_end(steps, flow.get("hint"))
    hinted = _hinted(steps, flow.get("hint")) is not None
    if not end:
        return None
    rel = str(end.get("path") or end.get("f") or "")
    try:
        root = Path(root).resolve()
        src = structure.Project(root).file(rel)
        line, where = int(end.get("l")), end.get("where") or "before"
        if src is None or not 1 <= line <= len(src.lines):
            return None
        first = structure.stmt_start(src, line) if where != "after" else line
        if not (structure.real_stmt(src, first) or structure.call_in_if(src.clines[first - 1])):
            return None
        last = line
        while last < len(src.clines) and structure._UNFINISHED.search(src.clines[last - 1].rstrip()):
            last += 1
        got = _state_case(src, rel, first, last, root) if not hinted else None
        if isinstance(got, dict):  # 好几处 case、说不准：照旧放在结束那一步旁边，提醒一句
            warn.append(got["unsure"] + f"，清零放在「{end['n']}」那一句旁边")
            got = None
        if got:
            after, ind, place = got
        elif hinted and not re.match(r"(?:return|break|continue|goto)\b", src.clines[first - 1].strip()):
            # 试一次看出来的：一轮到这一步才完，清零放在它那一句的紧后面（那一句是 return / break 的话放它前面，后面执行不到）
            after, ind, place = last, _indent(src, first), f"{rel} 第 {last} 行「{end['n']}」那一句后面（试一次看出来一轮到这一步才完）"
        elif where != "after":
            after, ind, place = first - 1, _indent(src, first), f"{rel} 第 {first} 行「{end['n']}」那一句前面（这一轮结束的地方）"
        else:
            after, ind, place = line, _indent(src, line), f"{rel} 第 {line} 行「{end['n']}」那一句后面（这一轮结束的地方）"
        lines = [f"{ind}// 计时点：一轮结束、回到等待，清零（码表按找到的关键步骤放的）", f"{ind}FSW_ROUND_RESET();"]
        if _once_place(root, rel, after, lines, steps):
            return None
        return {"file": rel, "after_line": after, "lines": lines, "where": place}
    except Exception:  # 结构读不懂：用 AI 的位置
        return None


def _drop_model_flush(data):
    """码表放了每轮清零时，AI 写的"打印最后一次"（FSW_FLUSH_LAST）不用：清零前设备会把这一轮还没打印的"最后一次"补打一行
    （FSW,LATE,…，见 fw_stopwatch.h 的 fsw_round_reset），试一次和多轮实测都把它当这个点的最后一次；
    清零之后不久才发生的（设备先回到等待、后放完），可能晚到的点（FSW_TAIL_MASK）设备照样补打。去掉不少东西。返回去掉的那几条。"""
    gone, keep = [], []
    for x in data["inserts"]:
        ls = x.get("lines") if isinstance(x, dict) else None
        if not isinstance(ls, list) or not any(isinstance(l, str) and FLUSH_CALL.search(l) for l in ls):
            keep.append(x)
            continue
        gone.append(x)
        rest = [l for l in ls if not (isinstance(l, str) and FLUSH_CALL.search(l))]
        if any(isinstance(l, str) and l.strip().startswith(("FSW_", "#include")) for l in rest):
            keep.append({**x, "lines": rest})
    data["inserts"] = keep
    return gone


UNREADABLE = "大括号对不上（多半是宏或编译开关把括号藏起来了），码表看不懂这个文件的结构，插的位置在不在函数里、在不在对应那一句的同一块里没核对"


def _unchecked(root, rel, cache):
    """这个文件的结构码表看不懂（大括号配不上）：返回说明，看得懂返回 None。"""
    from fsw import structure
    try:
        if rel not in cache:
            cache[rel] = structure.Project(root).file(rel)
        src = cache[rel]
        return UNREADABLE if src is not None and not src.balanced else None
    except Exception:
        return "码表读这个文件的结构时出错，插的位置没核对"


def _outside_body(root, rel, after, lines, cache):
    """插在第 after 行后面的计时点语句落在函数（或 lambda）外面：返回说明，否则 None。
    只有注释、#include 的不查；文件里大括号对不上（宏藏了括号、#if 两边不配对）说不准，不拦（parse_plan 记"没核对"）。"""
    if not any(isinstance(l, str) and l.strip().startswith("FSW_") for l in lines):
        return None
    from fsw import structure
    try:
        if rel not in cache:
            cache[rel] = structure.Project(root).file(rel)
        src = cache[rel]
        if src is None or not src.balanced or not 0 <= after <= len(src.lines) \
                or not any(b["kind"] in ("function", "lambda") for b in src.blocks):
            return None  # 大括号对不上、或一个函数都认不出来（比如只有片段）：说不准
        at = src.starts[after] if after else 0
        if any(src.blocks[i]["kind"] in ("function", "lambda") for i in src.chain(at)):
            return None
        near = src.lines[after - 1].strip()[:60] if after else ""
        return (f"{rel} 第 {after} 行（「{near}」）后面插的语句落在函数外面，编译不过；"
                "要插在函数的大括号里面（比如函数最后一个 } 之前）")
    except Exception:  # 结构读不懂：不拦
        return None


def _tool_include_at(root, rel):
    """码表自己加 #include 的位置：文件最外层（不在编译开关、大括号里）最后一个 #include 后面。"""
    from fsw import structure
    try:
        src = structure.Project(root).file(rel)
        if src is not None and src.balanced:
            return structure.include_spot(src)
    except Exception:
        pass
    src_lines = _read_lines(Path(root) / rel)
    return max((k for k, l in enumerate(src_lines, 1) if l.lstrip().startswith("#include")), default=0)


def _src_of(root, rel, cache):
    from fsw import structure
    if rel not in cache:
        cache[rel] = structure.Project(root).file(rel)
    return cache[rel]


def _include_place(root, rel, after, cache):
    """#include 插在第 after 行后面不行（在 #if/#ifdef/#else 里、大括号里、多行宏中间）：返回码表定的位置（文件最外层），行就返回 None。
    文件结构看不懂（大括号对不上）时不挪。"""
    from fsw import structure
    try:
        src = _src_of(root, rel, cache)
        if src is None or not src.balanced or structure.include_ok(src, after):
            return None
        to = structure.include_spot(src)
        return to if to != after and structure.include_ok(src, to) else None
    except Exception:
        return None


def _pp_note(root, rel, after, name, cache):
    """插在第 after 行后面的计时点落在编译开关（#if/#ifdef/#else）里：说清是哪个开关、按这块板子的配置编不编进固件。
    返回 {"file", "after_line", "name", "on": True/False/None, "text"}；不在开关里返回 None。按配置一定会编进去的不提。"""
    from fsw import structure
    try:
        src = _src_of(root, rel, cache)
        if src is None:
            return None
        regions = structure.pp_regions(structure.Project(root), src, after + 1)
    except Exception:
        return None
    if not regions or all(f["on"] is True for f in regions):
        return None
    off = [f for f in regions if f["on"] is False]
    f = (off or [f for f in regions if f["on"] is None])[0]
    sw = f"第 {f['line']} 行「{f['text'][:60]}」"
    if off:
        text = (f"「{name}」插在 {rel} 第 {after} 行后面，在{sw}那一支里：按这块板子的配置（sdkconfig）这一支不编进固件，"
                f"这个点一次也记不到，试一次会一直缺它。回到「找关键步骤」换成开关外面、每种配置都会走的那一句，或者删掉这一步")
    else:
        text = (f"「{name}」插在 {rel} 第 {after} 行后面，在{sw}这个编译开关里，码表按配置判断不了编不编进固件；"
                f"试一次一直缺它的话，多半就是这个开关没打开")
    return {"file": rel, "after_line": after, "name": name, "on": False if off else None, "text": text}


def _use_tool_reset(data, root, steps, flow=None, warn=None):
    """把 AI 写的清零换成码表放的（见 place_reset）。返回 (换掉的那几条, 码表放的那一条或 None)。"""
    auto = place_reset(root, steps, flow, warn)
    if not auto:
        return [], None
    mine, keep = [], []
    for x in data["inserts"]:
        ls = x.get("lines") if isinstance(x, dict) else None
        if not isinstance(ls, list) or not any(isinstance(l, str) and RESET_CALL.search(l) for l in ls):
            keep.append(x)
            continue
        mine.append(x)  # AI 写的清零（单独一条，或夹在某个计时点的几行里）：去掉清零那一句，别的照留
        rest = [l for l in ls if not (isinstance(l, str) and RESET_CALL.search(l))]
        if any(isinstance(l, str) and l.strip().startswith(("FSW_", "#include")) for l in rest):
            keep.append({**x, "lines": rest})
    data["inserts"] = keep
    data["inserts"].append({"step": "reset", "file": auto["file"], "after_line": auto["after_line"], "lines": auto["lines"], "_tool": True})
    rel = auto["file"]
    has = any(isinstance(x, dict) and str(x.get("file") or "").lstrip("./") == rel and x.get("step") == "include" for x in data["inserts"])
    if not has and "fw_stopwatch.h" not in (Path(root) / rel).read_text(errors="replace"):
        inc = next((l for x in data["inserts"] if isinstance(x, dict) and x.get("step") == "include"
                    for l in (x.get("lines") or []) if isinstance(l, str) and l.strip().startswith("#include")), None)
        at = _tool_include_at(root, rel)
        if inc:
            data["inserts"].append({"step": "include", "file": rel, "after_line": at, "lines": [inc.strip()]})
    return mine, auto


ONCE_MARK = re.compile(r"FSW_(?:MARK_ONCE|MARK_ONCE_ARMED)\(\s*(\d+)\s*,\s*(.*)\)\s*;|FSW_MARK_ONCE_AFTER\(\s*(\d+)\s*,\s*\d+\s*,\s*(.*)\)\s*;")


def _mark_every(data, driven):
    """跟着"一轮会走好几次"的点触发的点（见 repeat_driven）：AI 写的"只记第一次"换成"每次都记"（FSW_MARK），
    码表取这一轮最后那次（它是人感觉到的结束时）。返回换掉的 [{step, from, to, why}]。"""
    if not driven:
        return []
    ids = {int(k[1:]) for k in driven}
    fixed = []
    for x in data.get("inserts") or []:
        ls = x.get("lines") if isinstance(x, dict) else None
        if not isinstance(ls, list):
            continue
        for i, l in enumerate(ls):
            if not isinstance(l, str):
                continue
            m = ONCE_MARK.search(l)
            if not m:
                continue
            n = int(m.group(1) or m.group(3))
            if n not in ids:
                continue
            new = l[:m.start()] + f"FSW_MARK({n}, {m.group(2) or m.group(4)});" + l[m.end():]
            ls[i] = new
            fixed.append({"step": f"P{n}", "from": l.strip(), "to": new.strip(), "why": driven[f"P{n}"]["why"]})
    return fixed


NOTE_CALL = re.compile(r"FSW_([A-Z_]+)\(\s*(\d+)?")


def _notes_match(notes, out, auto):
    """AI 写的说明要和最后真正插进去的对得上（10-26 8783：说明里写"在 P8 处 FSW_FLUSH_LAST(7) 打印"，
    码表放了清零后这句已经去掉，说明还留着）。说明里提到的计时点语句（宏 + 编号），最后插的里面没有的：这句说明去掉；
    码表自己放了清零时，AI 讲它把清零放在哪的那几句也去掉（码表另写一句放在哪）。去掉了几句写一句。"""
    have = set()
    for x in out:
        for l in x.get("lines") or []:
            if isinstance(l, str):
                for m in NOTE_CALL.finditer(l):
                    have.add((m.group(1), m.group(2)))
    keep, gone = [], 0
    for n in notes:
        calls = [(m.group(1), m.group(2)) for m in NOTE_CALL.finditer(n)]
        if auto and any(c == "ROUND_RESET" for c, _ in calls):
            gone += 1
            continue
        if any((c, k) not in have and not (k is None and any(h == c for h, _ in have)) for c, k in calls):
            gone += 1
            continue
        keep.append(n)
    if gone:
        keep.append(f"AI 的说明里有 {gone} 句提到的语句最后没插（码表改过插法），已去掉，免得和实际插的对不上")
    return keep


def _keep_without_reset(out, auto, ids):
    """只记最后一次（FSW_KEEP_LAST）的点要在清零时、或单独写的 FSW_FLUSH_LAST 处才打出来：
    清单里两样都没有的，这个点插了也一次都报不出来（10-26 真测：上行末包一直等不到）。返回这些点的名字。"""
    lines = [l for x in out for l in x.get("lines") or [] if isinstance(l, str)]
    if auto or any(RESET_CALL.search(l) for l in lines):
        return []
    flushed = {int(m.group(1)) for l in lines for m in FLUSH_CALL.finditer(l)}
    kept = sorted({int(m.group(1)) for l in lines for m in KEEP_CALL.finditer(l)} - flushed)
    return [ids[f"P{k}"]["n"] if f"P{k}" in ids else f"P{k}" for k in kept]


def parse_plan(text, root, steps, needs=None, flow=None):
    """读 AI 给的插入清单，逐条核对。只能插计时点语句、注释和 #include，别的代码一行都不插。
    不合规的那几条（比如夹带了 if 判断）：去掉以后每个计时点都还在、留下的都能照常工作，就只去掉这几条，其余照插，
    去掉了什么写进 fixes 和说明；会缺计时点（或缺它要的武装、打印、清零那一句），整份不用（一个文件都不改），
    返回里带 bad（是哪几条、为什么），调用的地方可以追问 AI 一次只改这几条（见 fix_plan）。"""
    root = Path(root).resolve()
    data = _json_block(text)
    if not isinstance(data, dict) or not isinstance(data.get("inserts"), list):
        return _fail("bad_reply", "大模型回答的格式不对，码表看不懂，一个文件都没改。点\"重新插入\"再试一次。")
    header = str(data.get("header") or default_header(root)).lstrip("./")
    if not header.endswith("fw_stopwatch.h") or (root / header).resolve().parent != (root / header).parent.resolve() \
            or root not in (root / header).resolve().parents:
        header = default_header(root)
    steps, driven = effective_steps(root, steps)
    ids = {f"P{number(s['p'], steps)}": s for s in steps}
    every_fix = _mark_every(data, driven)
    out, problems, bad, moved, srcs, ifdef = [], [], [], [], {}, []
    warn = []  # 每轮结束那一步和识别结果对不上、好几处 case 说不准：照旧插，说清楚
    replaced, auto = _use_tool_reset(data, root, steps, flow, warn)
    flushed = _drop_model_flush(data) if auto else []
    unchecked = {}  # 结构看不懂、没核对位置的文件

    def reject(x, why):
        problems.append(why)
        bad.append({"step": str(x.get("step") or ""), "file": str(x.get("file") or ""), "after_line": x.get("after_line"),
                    "lines": [str(l) for l in x.get("lines")] if isinstance(x.get("lines"), list) else [str(x.get("lines"))],
                    "why": re.sub(r"<[^>]+>", "", why)})

    for x in data["inserts"]:
        if not isinstance(x, dict):
            continue
        step, rel, lines = str(x.get("step") or ""), str(x.get("file") or "").lstrip("./"), x.get("lines")
        p = (root / rel).resolve()
        if step not in ids and step not in HELPERS:
            reject(x, f"不认识的计时点 {step}")
            continue
        if root not in p.parents or not p.is_file():
            reject(x, f"找不到文件 {rel}")
            continue
        try:
            after = int(x.get("after_line"))
        except (TypeError, ValueError):
            reject(x, f"{rel} 的行号写得不对")
            continue
        n = len(_read_lines(p))
        if not 0 <= after <= n:
            reject(x, f"{rel} 没有第 {after} 行（只有 {n} 行）")
            continue
        badl = [l for l in lines if not (isinstance(l, str) and LINE_OK.match(l))] if isinstance(lines, list) else [str(lines)]
        if not lines or badl:
            reject(x, f"{rel} 第 {after} 行后面要插的内容里夹带了别的代码：<code>{html.escape(str(badl[0] if badl else '')[:80])}</code>")
            continue
        badv = [v for _, v in _marks(lines) if not value_ok(v)]
        if badv:
            reject(x, f"{rel} 第 {after} 行的附加值不是只读的：<code>{html.escape(badv[0][:80])}</code>"
                      "（附加值只能读已有的变量或不带参数的函数返回值）")
            continue
        rel0 = p.relative_to(root).as_posix()
        if any(isinstance(l, str) and l.strip().startswith("#include") for l in lines):
            to = _include_place(root, rel0, after, srcs)
            if to is not None:  # 码表自己挪，不用问 AI
                moved.append({"kind": "moved", "step": step, "file": rel0, "after_line": after, "to": to, "lines": [str(l) for l in lines],
                              "why": f"#include 放在第 {after} 行后面会落进编译开关（#if/#else）或大括号里，有的配置下根本不会被读到"
                                     f"（计时点语句会报\"没有声明\"、编译不过）；码表放到文件最外层第 {to} 行后面"})
                after = to
        if any(isinstance(l, str) and l.strip().startswith("FSW_") for l in lines):
            pp = _pp_note(root, rel0, after, ids[step]["n"] if step in ids else HELPER_NAMES.get(step, step), srcs)
            if pp:
                ifdef.append(pp)
        un = _unchecked(root, p.relative_to(root).as_posix(), srcs) if any(isinstance(l, str) and l.strip().startswith("FSW_") for l in lines) else None
        if un:
            unchecked.setdefault(p.relative_to(root).as_posix(), {"file": p.relative_to(root).as_posix(), "why": un, "steps": []})["steps"].append(step)
        outside = _outside_body(root, p.relative_to(root).as_posix(), after, lines, srcs)
        if outside:
            reject(x, outside)
            continue
        where = _once_place(root, rel, after, lines, steps)
        if where:
            reject(x, where)
            continue
        rel2 = p.relative_to(root).as_posix()
        off = _same_block(root, rel2, after, ids[step], srcs) if step in ids else None
        if off and off[1] is None:
            reject(x, off[0])
            continue
        if off:  # 对应的那一句清楚：码表自己挪到那一句紧前面（或紧后面），不用问 AI
            moved.append({"kind": "moved", "step": step, "file": rel2, "after_line": after, "to": off[1],
                          "lines": [str(l) for l in lines], "why": re.sub(r"<[^>]+>", "", off[0])})
            after = off[1]
        out.append({"step": step, "file": rel2, "after_line": after, "lines": lines})
    whole = []  # 去掉不合规的几条之后还缺的（缺了就不能只去掉）
    missing = [s["n"] for k, s in ids.items() if not any(x["step"] == k for x in out)]
    if missing:
        whole.append("这些计时点没插：" + "、".join(missing))
    for f in {x["file"] for x in out if x["step"] != "include"}:
        has = any(x["file"] == f and x["step"] == "include" for x in out) or "fw_stopwatch.h" in (root / f).read_text(errors="replace")
        if not has:
            whole.append(f"{f} 用了计时点，但没加 #include")
    whole += _needs_lost(out, bad) if bad else []
    if whole or (problems and not out):
        return _fail("bad_plan", "AI 给的插入清单不对，码表没有用，一个文件都没改：" + "；".join((problems + whole)[:4]) +
                     "。只能加计时点、注释和 #include。点\"重新插入\"再试一次。", bad=bad, lost=whole)
    fixes = [{"kind": "dropped", **b} for b in bad] + moved  # 只去掉不合规的那几条，其余照插；挪了位置的也记下
    fixes += [{"kind": "reset_by_tool", "step": "reset", "file": str(x.get("file") or ""), "after_line": x.get("after_line"),
               "to": auto["after_line"], "to_file": auto["file"],
               "lines": [str(l) for l in x.get("lines") or []], "why": f"每轮清零由码表放在{auto['where']}，不用 AI 写的位置"}
              for x in replaced] if auto else []
    fixes += [{"kind": "dropped_by_tool", "step": str(x.get("step") or ""), "file": str(x.get("file") or ""),
               "after_line": x.get("after_line"), "lines": [str(l) for l in x.get("lines") or [] if isinstance(l, str) and FLUSH_CALL.search(l)],
               "why": "码表放的每轮清零会在清零前补打这一轮还没打印的「最后一次」，AI 写的打印最后一次不用"} for x in flushed]
    if auto and not replaced:
        fixes.append({"kind": "reset_by_tool", "step": "reset", "file": "", "after_line": None, "to": auto["after_line"],
                      "to_file": auto["file"], "lines": [], "why": f"AI 没写每轮清零，码表放在{auto['where']}"})
    fixes += [{"kind": "every_by_tool", "step": f["step"], "file": "", "after_line": None, "lines": [f["from"]], "to_lines": [f["to"]],
               "why": f["why"] + "；码表改成每次都记，取这一轮最后那次，不在它那里清零"} for f in every_fix]
    notes = [str(n) for n in data.get("notes") or [] if n]
    notes = _notes_match(notes, out, auto)
    notes += [f"「{ids[f['step']]['n']}」跟着「{ids[driven[f['step']]['by']]['n'] if driven[f['step']]['by'] in ids else driven[f['step']]['by']}」"
              f"走，一轮会走好几次：码表把它改成每次都记（{f['to']}），取这一轮最后那次；一轮结束和清零不放在它那里"
              for f in every_fix if f["step"] in ids]
    notes += [f"每轮清零由码表放在{auto['where']}（{auto['file']} 第 {auto['after_line']} 行后面），AI 写的清零没用"] if auto else \
        ["码表没找到能确定放每轮清零的地方，用的是 AI 给的位置（已经核对过不在一直转的地方）；试一次时要是每轮在半路被清零，码表会说"] \
        if any(RESET_CALL.search(l) for x in out for l in x["lines"] if isinstance(l, str)) else []
    if warn and not auto:
        fixes.append({"kind": "reset_end_unsure", "step": "reset", "file": "", "after_line": None, "lines": [], "why": "；".join(warn)})
        notes.append("每轮结束是哪一步说不准（" + "；".join(warn) + "），码表没自己放清零，用的是 AI 给的位置；试一次时要是每轮半路被清零，码表会说")
    elif warn:
        notes += [f"提醒：{w}" for w in warn]
    tail = after_reset(root, steps, auto, flow) if auto else []
    if tail:
        names = "」「".join(x["name"] for x in tail)
        notes.append(f"「{names}」可能在每轮清零之后才打出来（{tail[0]['why']}）：清零后 {LATE_SECS} 秒内走到的码表照样记回这一轮"
                     f"（设备上「记最后一次」的会补打出来）；试一次时会核对，晚过 {LATE_SECS} 秒会点名")
    for u in unchecked.values():
        notes.append(f"{u['file']}：{u['why']}（这里插了 {'、'.join(u['steps'])}）；照样插了，编译、试一次会再核对")
    notes += [f"码表把 {m['step']} 从 {m['file']} 第 {m['after_line']} 行后面挪到第 {m['to']} 行后面：{m['why']}" for m in moved]
    notes += [f"码表去掉了 AI 多加的一处（{b['file']} 第 {b['after_line']} 行后面，{b['step']}）：{b['why']}。"
              "只能插计时点、注释和 #include；去掉后每个计时点都还在" for b in bad]
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
    stuck = _keep_without_reset(out, auto, ids)
    if stuck:
        return _fail("no_round_end", f"「{'」「'.join(stuck)}」要记一轮里的最后一次，得等一轮结束（清零）才报出来，"
                     "可这份清单里没有每轮清零，也没有单独打印它，插进去以后它一次也报不出来，码表没有用，一个文件都没改。"
                     "多半是识别时一轮只走一次的那几步都没找到（或者都标成了不一定走）：回到识别结果页，"
                     "确认最后一步（比如回到聆听、回到等待）在表里、没有标「可能不走」，再点\"重新插入\"。", lost=stuck)
    st = next((s for s in steps if s.get("line") == "start"), steps[0] if steps else None)  # 一轮的起点（和场景里一样）
    start = number(st["p"], steps) if st and f"P{number(st['p'], steps)}" in {x["step"] for x in out} else None
    notes += [x["text"] for x in ifdef]
    return {"ok": True, "header": header, "inserts": out, "notes": notes, "values": values, "start": start, "fixes": fixes, "ifdef": ifdef,
            "tail": tail if start is not None else [], "unchecked": list(unchecked.values()),
            "follow": follow_points(steps, flow) if start is not None else [],
            "end": (lambda e: {"step": point_id(e, steps), "name": e.get("n")} if e else None)(round_end(steps, (flow or {}).get("hint")))
            if auto else None}


PLAN_FIX = """你给的插入清单里有几条码表不能用（一个文件都还没改）。规则：只能插注释、#include 和 FSW_ 开头的计时点语句，每行一句；
不能加 if、条件判断、变量、赋值或任何别的代码。不用自己筛"只记回复的声音、不记提示音"：码表的计时点只认这一轮起点之后记到的
（起点之前记到的码表不算），上一轮剩下的、提示音不会被算进这一轮；要"某一步之后才记"就用 FSW_MARK_ONCE_AFTER(编号, 前一步编号, 0)，
不写判断，也不要套在 if 里用 FSW_ARM()。
清零（FSW_ROUND_RESET）、武装（FSW_ARM）只能放在一轮只经过一次的地方（比如进入聆听状态的那一支、读到卡），
不能放在主循环、事件循环的循环体里，也不能放在逐帧、逐包、定时执行的地方；放错的请挪到这种地方。
计时点必须和表里对应的那一句在同一个代码块里，插的位置和那一句之间不能隔着 }}、{{、else、case 或 default。

不能用的是这几条：
{items}
{lost}
只改这几条（以及因为它们改了、要跟着改的那个计时点），其余照原样抄，不用再读代码。最后只输出一个完整的 ```json 代码块，格式和上次一样。"""


def fix_plan(llm, text, root, steps, needs=None, flow=None):
    """核对插入清单；不合规又不能只去掉那几条（会缺计时点）时，在同一段对话里追问 AI 一次，只改这几条。
    返回 (插入清单或失败, 追问后的回答或 None)。追问后还不对就失败，说清楚。接法不支持追问（比如沿用的、命令行）就直接失败。"""
    plan = parse_plan(text, root, steps, needs, flow)
    if plan["ok"] or not plan.get("bad") or llm is None:
        return plan, None
    items = "\n".join(f"- {b['step']}（{b['file']} 第 {b['after_line']} 行后面）：{b['why']}；原来写的：{' / '.join(l.strip() for l in b['lines'])}"
                      for b in plan["bad"])
    lost = ("去掉它们会出的问题：" + "；".join(plan.get("lost") or []) + "\n") if plan.get("lost") else ""
    try:
        again = llm.followup(PLAN_FIX.format(items=items, lost=lost))
    except LLMError as e:
        return {**plan, "asked": True, "message": plan["message"].replace("点\"重新插入\"再试一次。", "") +
                f"追问 AI 改的时候出错：{e.message}。点\"重新插入\"再试一次。"}, None
    if again is None:  # 这种接法不支持追问
        return plan, None
    if not again.strip():
        return {**plan, "asked": True}, None
    plan2 = parse_plan(again, root, steps, needs, flow)
    if not plan2["ok"]:
        return {**plan2, "asked": True, "message": "AI 给的插法里有不能用的地方，码表追问它改了一次还是不对，一个文件都没改：" +
                plan2["message"].split("一个文件都没改：", 1)[-1], "first_bad": plan["bad"]}, again
    plan2["fixes"] = [{"kind": "rewritten", **b} for b in plan["bad"]] + plan2.get("fixes", [])
    plan2["asked"] = True
    plan2["notes"] = [f"AI 第一次给的插法有 {len(plan['bad'])} 处不能用（{plan['bad'][0]['why']}），码表追问它改了一次，改好的照插"] + plan2["notes"]
    return plan2, again


def _record_path(backup):
    return Path(backup) / "record.json"


def _sha(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _write_record(backup, rec, plan):
    _record_path(backup).write_text(json.dumps({**rec, "plan": plan}, ensure_ascii=False, indent=1), encoding="utf-8")


def _why(e):
    return getattr(e, "strerror", None) or str(e) or type(e).__name__


def apply(root, plan, backup):
    """把计时点插进复制的源码。插入要么全成，要么一个文件都不改：
    先在内存里算好每个文件插完的样子、核对只加不删；要改的文件原样存一份，先写"进行中"的插入记录（列出要改哪些文件、
    改前改后的内容摘要），再逐个写文件；中途出错就把已改的撤回。码表被强行关掉，留下的"进行中"记录照样能还原。"""
    root, backup = Path(root), Path(backup)
    if _record_path(backup).exists():
        r = restore(root, backup)  # 上一次插的先还原，保证总是在原版上插
        if not r["ok"]:
            return r
    files = sorted({x["file"] for x in plan["inserts"]})
    header = root / plan["header"]
    created = not header.exists()
    rec = {"header": plan["header"], "created_header": created, "files": files, "steps": {}, "places": [],
           "added": 0, "deleted": 0, "values": plan.get("values") or {}}
    news = {}
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
        news[f] = "".join(new).encode("utf-8", errors="surrogateescape")
        a = [l.rstrip("\r\n") for l in old]
        b = [l.rstrip("\r\n") for l in new]
        ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        rec["added"] += sum(j2 - j1 for t, _, _, j1, j2 in ops if t == "insert")
        rec["deleted"] += sum(i2 - i1 for t, i1, i2, _, _ in ops if t in ("delete", "replace"))
    if rec["deleted"]:  # 还没动任何文件
        return _fail("deleted", f"码表核对时发现会删掉或改掉 {rec['deleted']} 行原有代码，已经撤回，一个文件都没改。")
    if created:
        rec["added"] += len(header_text(plan.get("start"), [x["step"] for x in plan.get("tail") or []], plan.get("follow")).splitlines())
    rec.update(state="applying", before={f: _sha(root / f) for f in files},
               after={f: hashlib.sha256(news[f]).hexdigest() for f in files})
    try:
        shutil.rmtree(backup / "files", ignore_errors=True)
        for f in files:
            dst = backup / "files" / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / f, dst)
        _write_record(backup, rec, plan)
    except OSError as e:
        shutil.rmtree(backup / "files", ignore_errors=True)
        _record_path(backup).unlink(missing_ok=True)
        return _fail("write", f"插计时点前存原样备份时出错（{_why(e)}），一个文件都没改。看看磁盘是不是满了，再点\"重新插入\"。")
    try:
        for f in files:
            (root / f).write_bytes(news[f])
        if created:
            header.parent.mkdir(parents=True, exist_ok=True)
            header.write_text(header_text(plan.get("start"), [x["step"] for x in plan.get("tail") or []], plan.get("follow")), encoding="utf-8")
        rec["state"] = "done"
        _write_record(backup, rec, plan)
    except BaseException as e:  # 只读文件、磁盘满、杀毒软件锁住文件、被强行中断：已改的撤回
        _undo(root, backup)
        if not isinstance(e, Exception):
            raise
        return _fail("write", f"插计时点时写文件出错（{_why(e)}），已经撤回，一个文件都没改。"
                              "看看工作副本所在的磁盘是不是满了、文件是不是只读，再点\"重新插入\"。")
    return {"ok": True, **rec}


def _undo(root, backup):
    """插到一半出错：按"进行中"的记录把已改的文件放回去。"""
    try:
        restore(root, backup)
    except Exception:
        pass  # 撤回也没做成：记录留着，下次还原或插入前照样能放回去


def _mismatch(root, backup, rec):
    """插入记录和副本对不上的文件：副本里这个文件既不是插完的样子、也不是插之前的原样（副本换过、被改过），
    或者备份丢了、变了。旧版码表的记录没有内容摘要，核对不了，也算对不上。"""
    before, after = rec.get("before"), rec.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return rec.get("files") or ["插入记录"]
    bad = []
    for f in rec.get("files") or []:
        cur = _sha(Path(root) / f)
        if cur is None or cur not in (before.get(f), after.get(f)) or _sha(Path(backup) / "files" / f) != before.get(f):
            bad.append(f)
    return bad


def restore(root, backup):
    """还原：把改过的文件原样放回去，删掉码表加的计时点工具。
    先核对插入记录和副本对得上才放回去；对不上（比如源码改过、副本重新复制过）就一个文件都不动，
    交给调用的地方重新复制副本，免得把旧备份盖到新副本上。"""
    root, backup = Path(root), Path(backup)
    rp = _record_path(backup)
    if not rp.exists():
        return {"ok": True, "restored": 0}
    try:
        rec = json.loads(rp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    bad = _mismatch(root, backup, rec)
    if bad:
        return _fail("stale", f"工作副本里的 {bad[0]} 和上次的插入记录对不上，没有拿旧备份去盖。", files=bad)
    try:
        for f in rec["files"]:
            if _sha(root / f) != rec["before"][f]:
                shutil.copy2(backup / "files" / f, root / f)
        if rec.get("created_header"):
            (root / rec["header"]).unlink(missing_ok=True)
    except OSError as e:  # 记录留着，下次还原接着放
        return _fail("write", f"还原时写文件出错（{_why(e)}），看看磁盘是不是满了、文件是不是只读，再还原一次。")
    discard(backup)
    return {"ok": True, "restored": len(rec["files"])}


def discard(backup):
    """作废插入备份和记录（工作副本已经重新复制过，不再需要）。"""
    backup = Path(backup)
    shutil.rmtree(backup / "files", ignore_errors=True)
    _record_path(backup).unlink(missing_ok=True)


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


def plan_checks(plan):
    """看改动那页要直接摆出来的提醒（不折叠）：结构看不懂、没核对位置的文件；每轮结束是哪一步说不准。"""
    out = [f"没核对：{u['file']} {u['why'].split('，', 1)[0]}，这个文件里插的 {'、'.join(u['steps'])} 在不在函数里、在不在对应那一句的同一块里码表没法核对，编译和试一次时会再看"
           for u in plan.get("unchecked") or []]
    out += [f"每轮结束是哪一步说不准：{f['why']}。清零用的是 AI 给的位置" for f in plan.get("fixes") or [] if f.get("kind") == "reset_end_unsure"]
    out += [x["text"] for x in plan.get("ifdef") or [] if x.get("on") is False]
    return out


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


# 报错指到码表的头文件里（宏展开出来的）：编译器接着会说"在哪里展开的这个宏"，那才是插进去的那一句
EXPAND = re.compile(r"([^\s:'\"]+\.(?:c|cc|cpp|cxx|h|hpp)):(\d+)(?::\d+)?:\s*note:\s*(?:in expansion of macro|expanded from macro)")
HELPER_NAMES = {"reset": "每轮清零", "arm": "武装", "flush": "打印最后一次", "include": "#include"}


def _rel(f, root):
    try:
        return Path(f).resolve().relative_to(root).as_posix() if f.startswith("/") else f.lstrip("./")
    except ValueError:
        return None


def _undeclared(text, rel):
    """报错说 FSW_ 计时点语句"没有声明"：不是点放错了，是这个文件里码表的头文件没被读到（#include 落进了没编译的那一支）。"""
    return bool(re.search(rf"{re.escape(rel)}:\d+(?::\d+)?:\s*error:\s*'FSW_\w+' was not declared", text or ""))


def error_place(text, rec, root):
    """按报错的"文件:行号"（报错在码表头文件里时，按"在哪里展开的这个宏"）对回是插进去的哪一处（计时点或辅助语句）。
    返回 {"step", "file", "at"（原文件第几行后面）, "line"（插完后的行号）, "lines"}；对不上返回 None。"""
    root = Path(root).resolve()
    hits = []
    for m in re.finditer(ERR.pattern + "|" + EXPAND.pattern, text or ""):
        f, line = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        rel = _rel(f, root)
        if rel and not rel.endswith("fw_stopwatch.h"):
            hits.append((rel, int(line)))
    for rel, line in hits:
        for pl in rec.get("places") or []:
            nl = pl.get("new_lines") or []
            if pl["file"] == rel and nl and (line in nl or line == max(nl) + 1):
                return {"step": pl["step"], "file": rel, "at": pl["at"], "line": line, "lines": pl.get("lines") or [],
                        "undeclared": _undeclared(text, rel)}
        for sid, s in rec["steps"].items():  # 老的插入记录没有 new_lines
            if s["file"] == rel and s["lines"] and (line in s["lines"] or line == max(s["lines"]) + 1):
                return {"step": sid, "file": rel, "at": s.get("at"), "line": line, "lines": []}
    return None


def error_text(place, steps):
    """编译报错对回的那一处，用大白话说是哪一句、放在哪（页面上显示，重插时也告诉 AI）。"""
    sid = place["step"]
    name = HELPER_NAMES.get(sid) or next((s.get("n") for s in steps if point_id(s, steps) == sid), None) or sid
    what = f"「{name}」那一句" if sid in HELPER_NAMES else f"计时点 {sid}「{name}」"
    if place.get("undeclared"):
        return (f"{place['file']} 里的计时点语句编译时说\"没有声明\"：这个文件里码表加的 #include 没被读到"
                f"（多半落进了 #if/#else 编译开关里没编译的那一支），不是{what}放错了。重新插入时码表会把 #include 放到文件最外层")
    return f"码表插的{what}放的地方不对，编译不过：{place['file']} 第 {place['at']} 行后面（插完后是第 {place['line']} 行）"


def locate_error(text, rec, root):
    """按报错的"文件:行号"对回是哪一处计时点（辅助语句不算）；对不上返回 None。"""
    pl = error_place(text, rec, root)
    return pl["step"] if pl and pl["step"] not in HELPERS else None


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
    plan, _again = fix_plan(llm, text, root, steps, needs)
    if not plan["ok"]:
        return {**plan, "seconds": round(time.time() - t0, 1)}
    rec = apply(root, plan, backup)
    if not rec["ok"]:
        return {**rec, "seconds": round(time.time() - t0, 1)}
    return {"ok": True, "plan": plan, "rec": rec, "view": diff_view(rec, steps), "notes": plan["notes"],
            "seconds": round(time.time() - t0, 1)}


MARK = re.compile(r"^FSW,(?:LATE,)?P(\d+),\d+,-?\d+\s*$")


def count_marks(lines):
    """试一次：数收到了哪几个计时点。"""
    return {int(m.group(1)) for l in lines if (m := MARK.match(l.strip()))}
