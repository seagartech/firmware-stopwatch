"""找关键步骤：让大模型只读源码，找出"从什么到什么"经过的关键步骤和计时点位置，再换成网页要的样子。

提示词底子是验证期用过的识别提示词，加了三样：
边找边报"找到：…"（网页显示已找到几个）、最后给一个 json（码表自己核对行号、画清单和流程地图）、
顺带发现的另一条线（比如开口前先换表情）。

位置由码表逐个打开文件核对：文件不存在、行号超出的，标"需核对"并写清楚，不当真。
确认前不改任何文件：大模型只拿到只读工具。
"""

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from fsw import structure
from fsw.llm import LLMError, redact

PICK_PROMPT = """你是"固件码表"工具里的固件分析助手。用户是不写代码的硬件负责人。

## 用户的要求
固件源码就在当前目录（源码根目录）。
用户想测：{what}
{chip}
## 你的任务
只读源码，不改任何文件。上一步已经理清了一轮的流程（下面的事件链），这一步只从链里选计时点、写插入位置（插一句记录当前时间的语句，不改原有逻辑）。需要时可以再打开源码核对。

## 计时点拿来做什么
码表把计时点插进固件，让人连续操作很多轮（比如连续对话、连续刷卡），每轮用相邻计时点的时间差算出每一段用了多久。所以：每一轮每个点恰好记到一次；点记到的先后就是真实发生的先后；只量设备和服务器在忙的时间，不含人操作、等人的时间。

## 已理清的流程
{flow}
## 怎么选点
1. 计时点只能从事件链里选：每个点都写 chain_step（第几步），点就插在那一步那一行，或那一行所在的那一支里；链外不加点。起点是第 1 步（line_of 写 start）。
2. 链里标了上传首包、上传末包、首个结果、最后结果的步骤都要有点；第一下感觉到的结果也要有。同一步同时标了上传首包和上传末包的，在这一步放两个点：一个 once 写 first，一个写 last。
3. 点所在那一层是循环体（或逐帧、逐包反复调用的函数）时，它每圈都执行，不能写 once：要么放进循环里判断出"就是这一次"的那一支（这时写 once），要么写 first 或 last。不写 every。首包、首个结果这类，放在判断出"确实会被用上"之后。
4. 插点写法：line 写一句真正的语句，点插在它前面、和它同一层（where 写 before）；要放进某一支，就写这一支大括号里的第一句。if、while、for、switch、else、case 那一行不能当 line：点会落在整个判断或循环外面，不在你想要的那一支里。只有 {{ 或 }} 的行、#if/#else/#endif 也不能写；只有放在块里最后一句之后才写 after；同一句只放一个点（上面第 2 条的首包、末包除外）。例：要记 `}} else if (done) {{` 这一支，写它里面第一句 `stop();`、before。条件成立才执行的那一句（比如调用回调、发出数据），点写在 if 大括号里的第一句，不写在 if 那一行；首个结果、最后结果这类点也放在认出是这种消息之后那一支里，不放在消息分发入口。发送或调用本身写在 if 条件里的（比如 if (!send(x))），只能写 if 这一行、where 写 before。
5. 点放在链写明的那一支里，出错、失败、重试那一支不放。外面套着条件、可能不走的，always 写 false，除非在 why 里说明正常每轮都满足。
6. 链里先后写着"不确定"的，不要串成前后相连的段，写进 doubts。
7. 顺带发现的线（比如播放回复前先切换表情、开门前先亮灯）也列出来；它的分段除了从主线分出的那个点，用的点都要属于这条线本身（line_of 写 side；这条线的点可以不在事件链里）。同一件事有"收到"和"真正生效"两个时刻的，两个都列。
{known}
## 边找边报
每确认一个关键步骤，单独输出一行：找到：<中文名>（<文件>:<行号>）

## 最后输出
最后只输出一个 ```json 代码块，格式如下（中文；名字用业内通行术语，说明句子写给不写代码的人看）：
```json
{{
 "category": "这是哪一类设备，只能从下面的类别里挑一个原样写，都不像就写：其他",
 "summary": "一段话结论：链路有几步，有没有用户需要知道的重要情况（比如某段不在设备上）",
 "start_found": true, "end_found": true,
 "missing": "起点或终点找不到时说清是哪一头、为什么；都找到就空着",
 "start": {{"name": "起点的短名字，6 个字以内，比如：说话结束、读到卡号", "note": "起点之前设备看不见的那段，没有就空", "late": "true / false（人做完到起点之间是否隔着上传、服务器或手机处理；只差几十毫秒的写 false）"}},
 "main": {{"name": "要测的这条线的名字，比如：播放这条、开锁这条", "end": "终点的短名字，6 个字以内，比如：开始播放、门锁打开"}},
 "side": null 或 {{"name": "顺带发现的线，比如：表情这条", "end": "比如：换上表情", "found": "发现了什么，一句话", "why": "为什么推荐也测，一句话"}},
 "steps": [
  {{"id": "P1", "chain_step": 1, "name": "中文名，8 个字以内，比如：收到识别结果、读到卡号", "file": "相对源码根目录的路径", "line": 123,
    "func": "函数名", "where": "before / after（见插点写法）", "once": "once / first / last（见上）",
    "line_of": "start（起点）/ main（要测的这条）/ side（顺带发现的那条）", "sure": "高 / 中 / 低",
    "always": "true / false（连续使用时每一轮都一定经过这一步吗）",
    "felt": "first / last / 空（用户能感觉到的结果：first 是第一下，比如开始出声、灯刚亮；last 是最后一下，比如声音放完、门完全打开；只有一下的写 last；其余写空）",
    "kind": "felt 不为空时写怎么感觉到的：声音 / 画面 / 灯光 / 动作 / 其他；否则空",
    "why": "为什么放这里（大白话）", "doubt": "拿不准的地方，没有就空"}}
 ],
 "segments": [{{"from": "P1", "to": "P2", "name": "这段在做什么", "who": "固件 / 硬件 / 网络 / 服务器，可以组合", "outside": false}}],
 "side_from": "顺带那条线从哪个计时点分出去，没有就空",
 "side_segments": [{{"from": "P1", "to": "P5", "name": "…", "who": "…", "outside": false}}],
 "act": "人要怎么操作设备，才能走完一轮要测的这条线，一句话、写给操作的人看，比如：喊唤醒词，再说一句话；刷一次卡。只写人做的动作，不写设备会怎么回应（比如响一声、亮灯，不同状态下不一样）；要人等设备做完再动的，写成「等它做完」这类，不写「别出声」「等着不出声」这类叫人保持安静的话",
 "outside_note": "设备外那段在做什么，12 个字以内，比如：服务器：语音识别、生成回复；没有就空",
 "doubts": [{{"text": "读代码时发现的可能变慢或出错的地方", "sure": "高 / 中 / 低"}}]
}}
```
steps 按发生的先后顺序排；segments 是要测的这条线从起点到终点的每一段，outside=true 表示这段在设备外面（服务器、网络）。

## 设备类别（填 category 用）
{categories}

## 规则
- 每个位置必须是你亲自打开文件核对过的真实行号。
- "切换完成""生效"类的计时点放在这次切换引起的动作（比如清空缓存、关掉录音）都做完之后。
- 拿不准就标"低"或"中"，并在 doubt 里写为什么，不要编。
- 说明文字里不要写 P1、P2 这样的编号，写步骤的中文名。
- 起点、终点、线、计时点、段的名字和 outside_note 用业内通行术语（比如语音识别、首包音频、扬声器、权限校验），不用口语（比如听写、想回答、喇叭出声）。
- 起点或终点找不到，start_found / end_found 写 false。
"""

CHAIN_PROMPT = """你是"固件码表"工具里的固件分析助手。这一步只理清流程，不选计时点。

## 用户想测
{what}
{chip}固件源码就在当前目录（源码根目录）。只读源码，不改任何文件。

## 要做的事
理清连续使用时的第二轮里，从设备察觉人做完输入，到最后一个结果（比如回复全部播完、门完全打开），设备依次做了哪些事。
1. 选定一条实际的触发路径（比如喊唤醒词、按键、点屏幕、刷卡），写进 trigger。从处理这个输入事件的函数开始，沿真实的调用关系往下追（谁调用了谁、事件交给了谁），不能挑一个名字像的函数就用。
2. 第一步用设备自己能察觉人做完输入的检测（比如静音或语音活动检测翻转、读卡完成、按键松开）。哪怕它只用来切换显示、指示灯，不上报，也算设备察觉到了，第一步就用它。确实找不到，在 detect 里写明找过哪里。
3. 运行时走哪种模式、哪种协议：找到真正决定它的那一行代码，再查 sdkconfig、sdkconfig.defaults* 或 Kconfig 的取值，写进 mode。
4. 每一步写文件、行号、这一行的原文、所在的函数名、走的是哪一支（哪个条件成立才走）、一轮里只执行一次还是反复执行（逐帧、逐包、轮询），以及它为什么排在上一步后面。
5. 排顺序前，先读触发它的那一行代码或协议文档：消息或事件什么时候到、在哪种模式下才走，不能按名字猜。交给队列、Schedule、回调之后才执行的，按真正执行的时刻排。依据只能是代码、协议文档或注释，没有就写"不确定"。
6. 链里必须有：设备往外发数据的第一包和最后一包（有上传的话）、收到的第一个结果、最后一个结果，在对应步骤的 role 里标出来。逐包反复执行的同一句发送，可以同时标上传首包、上传末包（指这一句一轮里第一次和最后一次执行），下一步用 first、last 区分，不用猜哪一包是最后一包。首个结果、最后结果这类收到某种消息或某个条件成立才发生的步骤，写认出是它之后那一支里的语句（比如处理这种消息的那一支里的第一句），不写通用的消息分发入口，也不写 else if、else 那一行。"最后"一类优先用整段真正结束的那一句（比如结束消息、输出或播放队列放空），不用逐包、逐帧执行的那一行。链写到最后一个结果为止，下一轮的事不写。
7. 只在第一轮才走的步骤（比如唤醒、建立连接、打开音频通道），以及注册回调、初始化这类不在这一轮运行时执行的代码，不进链。
{hint}
## 最后输出
只输出一个 ```json 代码块（中文）：
```json
{{"trigger": "选定的触发路径", "upload": "设备往外发什么数据；不往外发写：无",
 "mode": {{"what": "运行时走哪种模式、哪种协议；不用选的写：无", "file": "决定它的那一行所在文件", "line": 123, "code": "那一行的原文", "config": "相关配置和取值"}},
 "detect": {{"kind": "第一步用的是哪种输入结束检测，比如：静音检测翻转、读卡完成、按键松开", "found": true, "looked": "找不到时写找过哪里"}},
 "chain": [{{"n": 1, "what": "这一步设备做了什么", "file": "相对源码根目录的路径", "line": 123, "code": "这一行的原文", "func": "这一行所在的函数名", "role": "上传首包 / 上传末包 / 首个结果 / 最后结果，可写几个，用、隔开；其余空", "branch": "走哪一支或哪个条件成立才走，没有就空", "repeat": "once / repeat", "after": "为什么排在上一步后面：哪段代码、文档或注释；没有就写 不确定（第 1 步空着）"}}],
 "doubts": ["拿不准的地方"]}}
```
行号必须是你亲自打开文件核对过的，code 照抄那一行。
"""

KNOWN = "\n上次读到一半断了，已经找到这些步骤：{names}。请核对它们，然后接着找剩下的，最后照样给出完整结果。\n"
FOUND = re.compile(r"找到[：:]\s*([^\n（(]+)(?:[（(]\s*([^）)\n]+?)\s*[）)])?")
SURE = {"高", "中", "低"}
LINE_OF = {"start": "start", "main": "talk", "side": "face"}


def _categories():
    """体验标准库里的设备类别（fsw/standards/reference.json）：AI 读完代码顺手挑一个，码表按它选体验标准。"""
    from fsw import standards as stds
    return "\n".join(f"- {c['key']}：{c['hint']}" for c in stds.categories()) or "- 其他"


PROMPT = PICK_PROMPT  # 旧名字：第二次调用（选计时点）的提示词
STAGES = {"chain": "理清流程", "pick": "选计时点"}


def _chip(chip):
    return f"芯片和板子：{chip}\n" if chip else ""


# 按设备类别给第一步的提醒（类别来自标准库的类别名；认不出类别就不加）
VOICE_HINT = """
## 这一类设备（语音对话）要注意
- 要量的是连续对话的第二轮。自动聆听（自动停止）模式下，回复播完后设备自己回到聆听，第二轮从输入结束检测（静音、说话结束）开始，不从触摸或按键开始；只有代码表明只能手动聆听时，才从按键或触摸开始。
- 链里要有：上传的首包和末包、收到识别出的文字、第一段和最后一段声音播出、播放全部放完；协议里有回复开始和回复结束这一对消息的，两条都要进链（本地合成、一次性返回的没有就不写）。
- 上传首包、末包写真正发出数据的那一句，不写循环头、不写判断行；每一包都走同一句发送的，这一句同时标上传首包、上传末包。
- 声音全部播完，这一轮就结束；之后恢复聆听、开始下一轮都不进链。
"""
CARD_HINT = """
## 这一类设备（刷卡、门禁、门锁）要注意
- 要量的是第二次刷卡（第一次可能还在初始化、建连接）。
- 链按这个顺序找：察觉有卡、读出卡号、校验（本地比对或发给服务器）、继电器或门锁动作、声音或指示灯反馈；找不到的在 doubts 里写明。
"""
HINTS = {"语音对话": VOICE_HINT, "门禁": CARD_HINT, "智能门锁": CARD_HINT, "刷卡终端": CARD_HINT}


def build_chain_prompt(what, chip="", category=""):
    """第一次调用：只理清一轮的流程。category 是认出的设备类别，有对应提醒就加上。"""
    return CHAIN_PROMPT.format(what=what, chip=_chip(chip), hint=HINTS.get(str(category or "").strip(), ""))


# 第一步之前认设备类别（不调用大模型）：先用这个项目上次识别时 AI 填的类别，没有再按源码里的词粗猜，拿不准就不猜
_VOICE_WORDS = ("opus", "vad", "tts", "stt", "asr", "wake_?word", "wakenet", "speech", "microphone|mic_")
_CARD_WORDS = ("rfid", "nfc", "rc522|mfrc522|pn532|pn5180", "wiegand", "mifare|iso14443", "card_?uid|uid_ok|read_uid", "relay|lock_")


def _saved_category(project_dir):
    best = None
    for f in sorted((Path(project_dir) / "identify").glob("*.json"), key=lambda x: x.stat().st_mtime):
        try:
            c = str(json.loads(f.read_text(encoding="utf-8")).get("category") or "").strip()
        except (OSError, ValueError, AttributeError):
            continue
        if c in HINTS or c in {x["key"] for x in _cats()}:
            best = c
    return best


def _cats():
    from fsw import standards as stds
    return stds.categories()


def guess_category(root, project_dir=None):
    """返回 (类别, 依据)；认不出返回 ("", "")。"""
    if project_dir:
        c = _saved_category(project_dir)
        if c:
            return c, "上次识别的结果"
    hits = {"v": set(), "c": set()}
    pats = {"v": [re.compile(rf"(?<![a-z])(?:{w})", re.I) for w in _VOICE_WORDS], "c": [re.compile(rf"(?:{w})", re.I) for w in _CARD_WORDS]}
    try:
        files = structure.Project(root).sources()[:3000]
    except OSError:
        return "", ""
    for rel in files:
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")[:200000]
        except OSError:
            continue
        for k in pats:
            for i, pt in enumerate(pats[k]):
                if i not in hits[k] and pt.search(text):
                    hits[k].add(i)
    v, c = len(hits["v"]), len(hits["c"])
    if v >= 4 and v > c + 1:
        return "语音对话", "源码里的词"
    if c >= 3 and c > v + 1:
        return "刷卡终端", "源码里的词"
    return "", ""


def _flow_text(flow):
    """把理清的流程写成给第二次调用看的几行字。"""
    if not flow:
        return "（没有）\n"
    mode, det = flow.get("mode") or {}, flow.get("detect") or {}
    at = f"（{mode.get('file')}:{mode.get('line')} `{mode.get('code') or ''}`）" if mode.get("file") else ""
    rows = [f"触发路径：{flow.get('trigger') or ''}",
            f"模式和协议：{mode.get('what') or ''}{at}{'；配置：' + str(mode['config']) if mode.get('config') else ''}",
            f"输入结束检测：{det.get('kind') or ''}{'' if det.get('found', True) not in (False, 'false') else '（没找到，找过：' + str(det.get('looked') or '') + '）'}",
            *([f"往外发的数据：{flow['upload']}"] if flow.get("upload") else []),
            "事件链："]
    for c in flow.get("chain") or []:
        rows.append(f"{c['n']}. {c['what']}｜{c['file']}:{c['line']} `{c['code']}`" + (f"｜函数 {c['func']}" if c.get("func") else "")
                    + (f"｜标为：{'、'.join(c['role'])}" if c.get("role") else "")
                    + f"｜{'分支：' + c['branch'] if c.get('branch') else '不在分支里'}｜{'反复执行' if c.get('repeat') == 'repeat' else '一轮一次'}"
                    + (f"｜排在上一步后面：{c['after']}" if c.get("after") else ""))
    for d in flow.get("doubts") or []:
        rows.append(f"拿不准：{d}")
    return "\n".join(rows) + "\n"


def build_prompt(what, chip="", known=None, flow=None):
    """第二次调用：拿着理清的流程只选计时点。"""
    return PICK_PROMPT.format(what=what, categories=_categories(), chip=_chip(chip), flow=_flow_text(flow),
                              known=KNOWN.format(names="、".join(known)) if known else "")


def found_names(text, with_at=False):
    """从边找边报的文字里数出已找到的步骤名（和"文件:行号"）。"""
    out, at = [], []
    text = text[:text.rfind("\n") + 1]  # 最后一行可能还没写完（AI 一段段往外吐）：只数写完的行，不然半截名字（"听""设"）也算一步
    for m in FOUND.finditer(text):
        n = m.group(1).strip()
        if n and n not in out:
            out.append(n)
            at.append((m.group(2) or "").strip())
    return (out, at) if with_at else out


def _json_block(text):
    blocks = re.findall(r"```json\s*(.*?)```", text, re.S)
    for b in reversed(blocks):
        try:
            return json.loads(b)
        except ValueError:
            continue
    return None


FORMAT_AGAIN = "你上一条回答最后的 JSON 码表读不懂（格式不对，比如少了引号或逗号）。不要再读源码，只按要求的格式把同样的内容重新输出一个 ```json 代码块。"


def _fail(kind, message, **extra):
    return {"ok": False, "kind": kind, "message": message, **extra}


def _rel(root, file):
    """大模型有时写绝对路径：换成相对源码根目录的。"""
    f = str(file or "").strip()
    if f.startswith("/"):
        try:
            return Path(f).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            return f
    return f[2:] if f.startswith("./") else f


def line_no(v):
    """AI 给的行号规范成整数：返回 (行号, 说明)。写成 "655-660"、"约 655" 的取第一个数，并说明是按哪一行核对的；
    没给或认不出的返回 (None, 说明)。后面对标准、插计时点都按整数算，不能让这里放过去的值在后面出错。"""
    if isinstance(v, bool):
        return None, f"AI 给的行号认不出来：{v}"
    if isinstance(v, int):
        return v, ""
    if isinstance(v, float) and v.is_integer():
        return int(v), ""
    m = re.search(r"\d+", v) if isinstance(v, str) else None
    if not m:
        return None, "AI 没给出行号，要你自己填" if v in (None, "") else f"AI 给的行号认不出来：{v}"
    n = int(m.group())
    return n, "" if v.strip() == m.group() else f"AI 给的行号是「{v.strip()}」，码表按第 {n} 行核对"


def check_location(root, file, line):
    """打开文件核对行号：返回 (那一行的代码, 问题说明)。"""
    root = Path(root).resolve()
    rel = _rel(root, file)
    p = (root / rel).resolve()
    if root not in p.parents or not p.is_file():
        return None, f"码表核对时找不到这个文件：{rel}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        n = int(line)
    except (OSError, TypeError, ValueError):
        return None, f"行号写得不对：{line}"
    if not 1 <= n <= len(lines):
        return None, f"码表核对时发现 {rel} 没有这一行（只有 {len(lines)} 行）"
    return lines[n - 1].strip(), None


_NONE = ("无", "没有", "不用", "不需要", "none", "-")
ROLES = ("上传首包", "上传末包", "首个结果", "最后结果")  # 事件链里必须标出的几步（不往外发数据的不要上传两项）


def _same_code(said, real):
    """模型抄的原文和文件里那一行对不对得上：去掉空白比较，允许抄掉行尾或只抄一部分（至少 6 个字符）。"""
    a, b = re.sub(r"\s+", "", str(said or "")), re.sub(r"\s+", "", str(real or ""))
    return bool(a) and (a == b or (len(a) >= 6 and a in b) or (len(b) >= 6 and b in a))


def _where_code(root, file, line, code):
    """核对一处 文件/行号/原文：返回 (行号, 问题)。行号差几行但原文就在附近的，按原文所在行算。"""
    n, note = line_no(line)
    if n is None:
        return None, note or "没写行号"
    real, problem = check_location(root, file, n)
    if problem:
        return None, problem
    if not str(code or "").strip():
        return None, "没抄这一行的原文"
    if _same_code(code, real):
        return n, None
    p = (Path(root) / _rel(root, file))
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    for k in (1, -1, 2, -2, 3, -3):
        if 1 <= n + k <= len(lines) and _same_code(code, lines[n + k - 1]):
            return n + k, None
    return None, f"{_rel(root, file)} 第 {n} 行是「{real[:40]}」，和它抄的原文「{str(code).strip()[:40]}」对不上"


def check_chain(root, data):
    """两次调用之间码表检查理清的流程（只做做得可靠的）：
    文件、行号、原文对不对得上；必填项有没有写；第一步有没有写明是哪种输入结束检测。
    返回 (整理好的流程, 问题列表)；问题列表不空就是不合格。"""
    if not isinstance(data, dict):
        return None, ["回答的格式不对，码表看不懂"]
    bad, tips, roles, proj = [], [], set(), structure.Project(root)
    trigger = str(data.get("trigger") or "").strip()
    if not trigger:
        bad.append("没写选定的触发路径")
    mode = data.get("mode") if isinstance(data.get("mode"), dict) else {}
    mwhat = str(mode.get("what") or "").strip()
    if not mwhat:
        bad.append("没写运行时走哪种模式、哪种协议")
    elif mode.get("file") or mode.get("line"):
        n, why = _where_code(root, mode.get("file"), mode.get("line"), mode.get("code"))
        if why:
            bad.append(f"决定模式和协议的那一行：{why}")
        else:
            mode = {**mode, "file": _rel(root, mode.get("file")), "line": n}
    elif not mwhat.lower().startswith(_NONE):
        bad.append("写了运行时走哪种模式、哪种协议，但没写真正决定它的那一行代码")
    det = data.get("detect") if isinstance(data.get("detect"), dict) else {}
    if not str(det.get("kind") or "").strip():
        bad.append("第一步没写明用的是哪种输入结束检测")
    if det.get("found") in (False, "false", "否") and not str(det.get("looked") or "").strip():
        bad.append("说找不到输入结束检测，但没写找过哪里")
    raw = [c for c in data.get("chain") or [] if isinstance(c, dict)] if isinstance(data.get("chain"), list) else []
    if len(raw) < 2:
        bad.append("事件链不到 2 步")
    chain, seen = [], set()
    for i, c in enumerate(raw):
        n = chain_no(c.get("n")) or i + 1
        tag = f"第 {n} 步"
        if n in seen:
            bad.append(f"{tag}的序号重复了")
        seen.add(n)
        miss = [label for k, label in (("what", "做了什么"), ("file", "文件"), ("line", "行号"), ("code", "原文"), ("func", "所在的函数"))
                if c.get(k) in (None, "")]
        rep = str(c.get("repeat") or "").strip().lower()  # 只看开头：允许后面带说明，比如「once（每次翻转）」
        rep = ("repeat" if rep.startswith(("repeat", "反复", "every")) else
               "once" if rep.startswith(("once", "一次", "只执行一次", "一轮一次", "只一次")) else "")
        role = [r for r in ROLES if r in str(c.get("role") or "")]
        roles.update(role)  # 先记下角色：这一步别处有问题，也不会被误报成"没标出"
        if not rep:
            miss.append("是否反复执行")
        if i and not str(c.get("after") or "").strip():
            miss.append("为什么排在上一步后面")
        if miss:
            bad.append(f"{tag}没写：{'、'.join(miss)}")
            continue
        line, why = _where_code(root, c.get("file"), c.get("line"), c.get("code"))
        if why:
            bad.append(f"{tag}：{why}")
            continue
        src = proj.file(_rel(root, c["file"]))
        got = structure.check_func(src, line, c["func"]) if src is not None else None
        if got and got[0] == "fail":
            bad.append(f"{tag}：{got[1]}")
            continue
        if got:
            tips.append(f"{tag}：{got[1]}")
        chain.append({"n": n, "what": str(c["what"]).strip(), "file": _rel(root, c["file"]), "line": line,
                      "code": check_location(root, c["file"], line)[0], "func": str(c["func"]).strip(), "role": role,
                      "branch": str(c.get("branch") or "").strip(), "repeat": rep, "after": str(c.get("after") or "").strip()})
    upload = str(data.get("upload") or "").strip()
    need = [r for r in ROLES if r not in roles and not (r.startswith("上传") and upload.lower().startswith(_NONE))]
    if raw and need:
        bad.append(f"事件链里没标出：{'、'.join(need)}（在对应步骤的 role 里写；确实不往外发数据的，upload 写「无」）")
    flow = {"trigger": trigger, "upload": upload, "mode": mode, "detect": det, "chain": chain,
            "doubts": [str(d) for d in data.get("doubts") or [] if d] if isinstance(data.get("doubts"), list) else [],
            "tips": tips}  # 码表判断不了、放行的地方（比如 lambda 里写的函数名），只提醒
    return flow, bad


def chain_no(v):
    """计时点对应事件链的第几步：正整数，没写或看不懂就是 None。"""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse(text, root, chain=None, strict=False):
    """读大模型的回答：取最后的 json，逐个核对位置。chain 是第一次调用理清的事件链（点只能从里面选）。"""
    data = _json_block(text)
    if not isinstance(data, dict):
        return _fail("bad_reply", "大模型读完了代码，但回答的格式不对，码表看不懂。点\"重试\"再找一次。", found=found_names(text))
    if chain:
        data["chain"] = chain
    if strict:
        data["strict"] = True  # 两步识别的新结果：沿用缓存时也按这套更严的结构检查
    warn = None
    if data.get("start_found") is False or data.get("end_found") is False:
        which = "起点" if data.get("start_found") is False else "终点"
        if data.get("start_found") is False and data.get("end_found") is False:
            which = "起点和终点"
        why = (data.get("missing") or "").strip()
        why = why + "。" if why and why[-1] not in "。！？.!?" else why
        if len(data.get("steps") or []) < 2:  # 找到的太少，列不出清单：请换个说法
            return _fail("no_end" if which == "终点" else "no_start",
                         f"在代码里没找到{which}。{why}<br>可以换个说法写\"测从什么到什么\"再找一次；"
                         f"找到一部分时，码表会先列出来，你在结果页手动添加步骤补上{which}。",
                         found=[s.get("name") for s in data.get("steps") or []])
        warn = f"在代码里没找到{which}。{why}可以点最下面的「添加步骤」手动补上{which}，或者回首页换个说法重找。"
    steps = []
    for i, s in enumerate(data.get("steps") or []):
        if not isinstance(s, dict):
            continue
        line, note = line_no(s.get("line"))
        code, problem = check_location(root, s.get("file"), line) if line is not None else (None, None)
        problem = "；".join(x for x in (note, problem) if x) or None  # 行号不是整数的也标需核对
        step = {"id": str(s.get("id") or f"P{i + 1}"), "name": str(s.get("name") or f"第 {i + 1} 步"),
                "file": _rel(root, s.get("file")), "line": line if line is not None else 0, "func": s.get("func") or "",
                "where": s.get("where") or "", "once": s.get("once") or "",
                "line_of": s.get("line_of") if s.get("line_of") in LINE_OF else "main",
                "sure": s.get("sure") if s.get("sure") in SURE else "中",
                "why": s.get("why") or "", "doubt": s.get("doubt") or "", "code": code, "loc_ok": problem is None,
                "always": s.get("always") not in (False, "false", "否"),
                "felt": s.get("felt") if s.get("felt") in ("first", "last") else "",
                "kind": str(s.get("kind") or "") if s.get("felt") in ("first", "last") else "",
                "chain_step": chain_no(s.get("chain_step"))}  # 对应事件链第几步；旧结果没有这一项
        if chain and not strict and step["chain_step"] is not None and step["chain_step"] not in {c["n"] for c in chain}:
            problem = "；".join(x for x in (problem, f"理清的流程里没有第 {step['chain_step']} 步，这个点不是从流程里选的") if x)
        if problem:
            step["sure"], step["doubt"] = "低", problem + (("；" + step["doubt"]) if step["doubt"] else "")
        twin = next((x for x in steps if x["file"] and (x["file"], x["line"], x["where"]) == (step["file"], step["line"], step["where"])), None)
        if twin:  # 和前面的点插在同一处：记到的时间一样，分不出先后
            step["sure"] = "低"
            step["doubt"] = f"和「{twin['name']}」插在同一行代码上，两点记到的时间一样，分不出先后" + (("；" + step["doubt"]) if step["doubt"] else "")
        steps.append(step)
    if len(steps) < 2:
        return _fail("bad_reply", "大模型只给出了不到 2 个步骤，量不出用时。点\"重试\"再找一次，或者换个说法写\"测从什么到什么\"。",
                     found=[s["name"] for s in steps])
    structure.check(root, steps, data.get("segments"), data.get("side_segments"), data.get("chain"), strict=strict)  # 位置在不在之外，再看插下去落在什么结构里
    return {"ok": True, **data, "steps": steps, "warn": warn}


def _segs(items, ids):
    out = []
    for g in items or []:
        if g.get("from") in ids and g.get("to") in ids:
            seg = {"a": g["from"], "b": g["to"], "t": g.get("name") or "", "who": g.get("who") or ""}
            if g.get("outside"):
                seg["out"] = True
            out.append(seg)
    return out


def to_page(r, what):
    """换成网页"找关键步骤"那页要的样子（和演示数据同样的字段）。"""
    steps = [{"p": s["id"], "n": s["name"], "f": Path(s["file"]).name or s["file"], "path": s["file"], "l": s["line"],
              "line": LINE_OF[s["line_of"]], "why": s["why"], "sure": s["sure"], "code": s["code"], "loc_ok": s["loc_ok"],
              "where": s["where"], "once": s["once"], "func": s["func"],
              "struct_ok": s.get("struct_ok", True), "chk": [{"level": c["level"], "text": c["text"]} for c in s.get("chk") or []],
              **({"note": s["doubt"]} if s["doubt"] else {}), **({} if s.get("always", True) else {"opt": True}),
              **({"felt": s["felt"], "kind": s.get("kind") or ""} if s.get("felt") else {})} for s in r["steps"]]
    ids = {s["p"] for s in steps}
    start, main, side = r.get("start") or {}, r.get("main") or {}, r.get("side")
    side_steps = [s["p"] for s in steps if s["line"] == "face"]
    if not side_steps:
        side = None
    main_ids = [s["p"] for s in steps if s["line"] != "face"]
    segs = _segs(r.get("segments"), ids)
    sname, mname, mend = start.get("name") or "起点", main.get("name") or "要测的这条", main.get("end") or "终点"
    branch_from = r.get("side_from") if r.get("side_from") in main_ids else (main_ids[0] if main_ids else None)
    page = {
        "steps": steps, "segs": segs, "fsegs": _segs(r.get("side_segments"), ids) if side else [],
        "ln": {"start": "起点", "talk": mname, "face": (side or {}).get("name") or "顺带的那条"},
        "main": main_ids, "branch": {"from": branch_from, "nodes": side_steps},
        "dash": [i for i, (a, b) in enumerate(zip(main_ids, main_ids[1:]))
                 if any(g.get("out") and g["a"] == a and g["b"] == b for g in segs)],
        "server": r.get("outside_note") or "", "mapWhat": what, "category": str(r.get("category") or "").strip(),
        "mapSel": next((s["p"] for s in steps if s["sure"] != "高"), steps[0]["p"]),
        "measure": f"{sname} → {mend}", "mainTitle": f"{mname}：{sname} → {mend}",
        "act": str(r.get("act") or "").strip(), "summary": r.get("summary") or "", "doubts": r.get("doubts") or [], "warn": r.get("warn"), "startNote": start.get("note") or "", "startLate": start.get("late") in (True, "true", "是"),
        "sideAsk": None, "sideTitle": "", "sideLbl": "",
    }
    if side:
        k, send = len(side_steps), side.get("end") or "顺带的那一步"
        page.update(sideTitle=f"{side.get('name')}：{sname} → {send}", sideLbl=f"{side.get('name')} →")
        page["sideAsk"] = {
            "found": (side.get("found") or "").rstrip("。"), "main": f"{sname} → {mend}", "side": f"{sname} → {send}",
            "why": side.get("why") or "", "k": k, "q": send, "addEx": "", "addGuess": None,
            "h": f"码表读代码发现：{(side.get('found') or '').rstrip('。')}。{side.get('why') or ''}要不要把\"{send}\"也一起测？",
            "no": f"不用，只测{mend}", "d1": f"{side.get('name')}一起测（多 {k} 个计时点）",
            "note": f"一起测：多插 {k} 个计时点，报告里多一条线；不影响{mname}的结果。",
        }
    return page


def error_record(e, stage, stage_seconds, seconds):
    """调用大模型出错时存下的记录（页面提示仍用好懂的话）：哪一步、第几次请求、异常类型和原话、HTTP 状态码、用时。
    文字已去掉像密钥、Authorization 头的部分，不带请求内容。"""
    info = {k: v for k, v in (getattr(e, "info", None) or {}).items() if v is not None}
    return {"kind": getattr(e, "kind", "other"), "stage": STAGES.get(stage, stage), "detail": redact(getattr(e, "detail", "") or ""),
            **info, "stage_seconds": stage_seconds, "seconds": seconds}


def identify(llm, root, what, chip="", known=None, on_progress=None, flow=None, category=""):
    """真识别：第一次调用理清流程（flow 给了就跳过），码表检查过再第二次调用选计时点。
    on_progress 收到 {stage, seconds, found, read, last}；出错时已找到的留着，重试时告诉大模型接着找。
    返回里带 flow（理清的流程，没通过检查的不带）和 calls（这次真调用了几次大模型）。"""
    t0, text = time.time(), []
    state = {"stage": "chain" if flow is None else "pick", "found": list(known or []), "at": [""] * len(known or []), "read": 0, "last": ""}
    calls, chain_reply, t_stage = 0, None, [t0]
    retries = []  # 发请求时重发过的：[{stage, request_no, retry, why, wait}]（只有会重发的接法才有）
    followups = []  # 回答的 JSON 格式坏了、在同一段对话里追问过的：[{stage, ok, seconds}]，每一步最多一次

    def fix_format(reply, stage):
        """回答里的 JSON 读不懂：同一段对话里追问一次"只按格式重新输出"（不读源码）。返回 (用哪份回答, 原来那份或 None)。"""
        if isinstance(_json_block(reply or ""), dict):
            return reply, None
        t = time.time()
        again = llm.followup(FORMAT_AGAIN)
        if again is None:  # 这种接法不支持追问
            return reply, None
        ok = isinstance(_json_block(again), dict)
        followups.append({"stage": STAGES[stage], "ok": ok, "seconds": round(time.time() - t, 1)})
        print(f"[识别] {STAGES[stage]}的回答格式不对，追问一次重新输出：{'好了' if ok else '还是不对'}", file=sys.stderr, flush=True)
        return (again if ok else reply), reply

    def took_retries(stage):
        for x in getattr(llm, "retry_log", None) or []:
            retries.append({"stage": STAGES[stage], **x})
        if getattr(llm, "retry_log", None):
            llm.retry_log = []

    def report():
        on_progress and on_progress({"seconds": round(time.time() - t0, 1), **state,
                                     "found": list(state["found"]), "at": list(state["at"])})

    def on_text(piece):
        text.append(piece)
        if state["stage"] != "pick":
            return
        for n, a in zip(*found_names("".join(text), with_at=True)):
            if n not in state["found"]:
                state["found"].append(n)
                state["at"].append(a)
        report()

    def on_tool(desc):
        state["read"] += 1
        state["last"] = desc
        report()

    def failed(e, stage):
        tail = (f"已找到的 {len(state['found'])} 个留着，点\"重试\"接着找" if stage == "pick"
                else "点\"重试\"再试一次") + "，也可以到设置页换一个大模型。"
        took_retries(stage)
        err = error_record(e, stage, round(time.time() - t_stage[0], 1), round(time.time() - t0, 1))
        err["retried_before"] = len(retries)  # 这次识别里出错前一共重发过几次（含成功的）
        print(f"[识别] {err['stage']}出错：{err['detail'] or err['kind']}（第 {err.get('request_no', '?')} 次请求，"
              f"这一步已用 {err['stage_seconds']} 秒，这次请求重发过 {err.get('retries', 0)} 次）", file=sys.stderr, flush=True)  # 运行日志：原始报错，已去掉密钥
        return _fail(e.kind, f"{STAGES[stage]}时出错：{e.message}<br>{tail}", found=state["found"], stage=stage, error=err, retries=retries, followups=followups,
                     seconds=round(time.time() - t0, 1), calls=calls,
                     **({"flow": flow} if flow else {}), **({"chain_reply": chain_reply} if chain_reply else {}))

    if flow is None:
        report()
        try:
            chain_reply = llm.investigate(build_chain_prompt(what, chip, category), root, on_text=on_text, on_tool=on_tool)
        except LLMError as e:
            calls += 1
            return failed(e, "chain")
        calls += 1
        took_retries("chain")
        try:
            chain_reply, chain_first = fix_format(chain_reply, "chain")
        except LLMError as e:
            return failed(e, "chain")
        took_retries("chain")
        got, bad = check_chain(root, _json_block(chain_reply))
        if bad:  # 不合格就停下说明原因，不自动重试
            more = f"等 {len(bad)} 处" if len(bad) > 3 else ""
            return _fail("bad_chain", f"理清流程这一步的结果没通过码表检查：{'；'.join(bad[:3])}{more}。点\"重试\"再理一次，也可以到设置页换一个大模型。",
                         found=[], stage="chain", problems=bad, chain_reply=chain_reply, retries=retries, followups=followups,
                         **({"chain_reply_first": chain_first} if chain_first else {}),
                         seconds=round(time.time() - t0, 1), calls=calls)
        flow = got
        state["stage"], text, t_stage[0] = "pick", [], time.time()
        report()
    try:
        reply = llm.investigate(build_prompt(what, chip, known, flow), root, on_text=on_text, on_tool=on_tool)
    except LLMError as e:
        calls += 1
        return failed(e, "pick")
    calls += 1
    took_retries("pick")
    try:
        reply, reply_first = fix_format(reply, "pick")
    except LLMError as e:
        return failed(e, "pick")
    took_retries("pick")
    r = parse(reply, root, chain=flow["chain"], strict=True)
    r.update(seconds=round(time.time() - t0, 1), reply=reply, flow=flow, calls=calls, stage="pick", chain_category=category or "",
             retries=retries, followups=followups)
    if reply_first:
        r["reply_first"] = reply_first
    if chain_reply is not None:
        r["chain_reply"] = chain_reply
    if r["ok"]:
        r["page"] = to_page(r, what)
        r["found"] = [s["name"] for s in r["steps"]]
    else:
        r["found"] = r.get("found") or state["found"]
    return r


def _key(*parts):
    return hashlib.sha1("\0".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _cache_file(project_dir, fingerprint, what, provider):
    # 找步骤的要求（两份提示词）改了，旧结果按旧要求找的：不沿用（旧文件留着，名字对不上就不会被读到）
    return Path(project_dir) / "identify" / f"{_key(fingerprint, what.strip(), provider, CHAIN_PROMPT, PICK_PROMPT, _categories())}.json"


def _flow_file(project_dir, fingerprint, what, provider, chip="", category=""):
    """第一次调用（理清流程）单独存：第二次失败了重试，不用再理一遍。放在子目录里，不和整份结果混在一起。"""
    return Path(project_dir) / "identify" / "flow" / f"{_key(fingerprint, what.strip(), provider, chip, CHAIN_PROMPT, HINTS.get(category, ""))}.json"


def _load_flow(f, root):
    try:
        saved = json.loads(f.read_text(encoding="utf-8"))
        flow, bad = check_chain(root, saved.get("raw"))  # 按现在的源码重新核对一遍，对不上就当没有
        return None if bad else flow
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def identify_cached(llm, root, what, project_dir, fingerprint, provider, reuse=True, reuse_flow=None, flow=None, **kw):
    """源码没变、要测的也没变时，直接用上次的识别结果（设置页的勾选），不再调用大模型。
    两次调用分别存：整份结果；理清的流程（reuse_flow，默认跟 reuse 一样；重试时码表传 True，第二次失败不用重跑第一次）。
    flow：调用方手里已有的理清的流程（没有项目目录、存不了文件时，网页重试把上次的带过来）。"""
    f = _cache_file(project_dir, fingerprint, what, provider) if project_dir and fingerprint else None
    if reuse and f and f.exists():
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            # 沿用的结果也按现在的源码重新做一遍结构检查（存下时可能还没有这层检查，或者是改完再测挪过行号的）
            structure.check(root, r.get("steps") or [], r.get("segments"), r.get("side_segments"), r.get("chain"),
                            strict=r.get("strict") is True)  # 旧缓存没有这一项：照旧宽松
            # 页面数据按存下的步骤重新算一遍：码表升级后页面要的新字段（比如"一轮记哪一次"）也有
            return {**r, "page": to_page(r, what), "reused": True, "seconds": 0, "calls": 0}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
    if "category" not in kw:
        kw["category"] = guess_category(root, project_dir if project_dir and fingerprint else None)[0]
    ff = _flow_file(project_dir, fingerprint, what, provider, kw.get("chip", ""), kw["category"]) if project_dir and fingerprint else None
    use = reuse if reuse_flow is None else reuse_flow
    if flow is not None and (not use or check_chain(root, flow)[1]):
        flow = None  # 带过来的流程按现在的源码对不上：不用
    if flow is None and ff and ff.exists() and use:
        flow = _load_flow(ff, root)
    r = identify(llm, root, what, flow=flow, **kw)
    if ff and not flow and r.get("flow"):
        raw = _json_block(r.get("chain_reply") or "")
        if isinstance(raw, dict):
            ff.parent.mkdir(parents=True, exist_ok=True)
            ff.write_text(json.dumps({"raw": raw, "what": what, "provider": provider}, ensure_ascii=False, indent=1), encoding="utf-8")
    if r["ok"] and f:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({**r, "what": what, "provider": provider}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**r, "reused": False, "flow_reused": bool(flow)}


SUGGEST = """你是"固件码表"工具里的助手。下面是一个单片机固件项目的概况。请推荐 3 个用户最可能想测的"从什么到什么"（用户感受得到的一段等待），
每个不超过 15 个字，写成"从……到……"，按推荐程度排序。只输出一个 JSON 数组，比如 ["从说完话到开始播放", "从按键到屏幕亮", "从开机到连上网"]。

项目名：{name}
{chip}
目录：
{tree}

说明文件开头：
{readme}
"""


def suggest(llm, root, info):
    """选好固件后扫一遍，推荐常用语（只给项目概况，不让它读代码，一般十几秒）。"""
    root = Path(root)
    tree = []
    for d in [root, root / "main"]:
        if d.is_dir():
            tree += [f"{e.relative_to(root).as_posix()}{'/' if e.is_dir() else ''}" for e in sorted(d.iterdir())
                     if e.name not in {".git", "build", "managed_components"}][:60]
    readme = ""
    for n in ["README_zh.md", "README.md", "readme.md"]:
        if (root / n).is_file():
            readme = (root / n).read_text(encoding="utf-8", errors="replace")[:2500]
            break
    prompt = SUGGEST.format(name=info.get("name") or root.name, chip=f"芯片：{info['chip']}" if info.get("chip") else "",
                            tree="\n".join(tree), readme=readme or "（没有）")
    try:
        text = llm.complete(prompt)
    except LLMError:
        return []
    m = re.search(r"\[.*\]", text, re.S)
    try:
        out = json.loads(m.group(0)) if m else []
    except ValueError:
        return []
    return [str(x).strip()[:30] for x in out if isinstance(x, str) and x.strip()][:3]


ADD = """你是"固件码表"工具里的固件分析助手。源码在当前目录，只能读，不能改。
用户在测"{what}"，想加一个漏掉的步骤："{sentence}"。
请读代码找到这一步发生的那一行，最后只输出一个 ```json 代码块：
{{"name": "中文名", "file": "相对源码根目录的路径", "line": 行号, "why": "为什么是这里（大白话）", "sure": "高 / 中 / 低"}}
行号必须是你亲自打开文件核对过的。找不到就写 {{"name": "", "file": "", "line": 0, "why": "为什么找不到"}}。
"""


def add_step(llm, root, sentence, what=""):
    """结果页"添加步骤 → 让 AI 找位置"：调用一次大模型找这一步在哪一行，码表再核对。"""
    try:
        reply = llm.investigate(ADD.format(what=what or "这条线", sentence=sentence), root)
    except LLMError as e:
        return _fail(e.kind, e.message)
    data = _json_block(reply)
    if not isinstance(data, dict):
        return _fail("bad_reply", "大模型回答的格式不对，码表看不懂。换个说法再试，或者自己填文件和行号。")
    if not data.get("file"):
        return _fail("not_found", f"AI 没找到这一步：{data.get('why') or ''}换个说法再试，或者自己填文件和行号。")
    line, note = line_no(data.get("line"))
    code, problem = check_location(root, data.get("file"), line) if line is not None else (None, note)
    if problem:
        return _fail("bad_location", f"AI 给的位置不对（{problem}）。换个说法再试，或者自己填文件和行号。")
    rel = _rel(root, data["file"])
    return {"ok": True, "n": data.get("name") or sentence, "f": Path(rel).name, "path": rel, "l": line,
            "why": data.get("why") or "", "sure": data.get("sure") if data.get("sure") in SURE else "中", "code": code}
