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

from fsw import candidates as cands_mod, structure
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
1. 计时点只能从事件链里选：每个点都写 chain_step（第几步），点就插在那一步那一行，或那一行所在的那一支里（不挪进那一句调用的函数里，也不挪到它引起、在别处执行的代码里）；链外不加点。起点是第 1 步（line_of 写 start）。
2. 链里标了上传首包、上传末包、首个结果、最后结果、全部放完的步骤都要有点；第一下感觉到的结果也要有。同一步同时标了上传首包和上传末包的，在这一步放两个点：一个 once 写 first，一个写 last；上传首包、末包只放在标了它们的那一步。标了输出的那一步也放 first、last 两个点。全部放完的点放在判断出放完的那一支里，不放在检查状态的入口。第 1 步和这些标了角色的步骤上的点，位置和 first、last、once 由码表按流程放好，你照常列出、起名字、写 felt 就行。
3. 点所在那一层是循环体（或逐帧、逐包反复调用的函数）时，它每圈都执行，不能写 once：要么放进循环里判断出"就是这一次"的那一支（这时写 once），要么写 first 或 last。不写 every。首包、首个结果这类，放在判断出"确实会被用上"之后。
4. 插点写法：line 写一句真正的语句，点插在它前面、和它同一层（where 写 before）；要放进某一支，就写这一支大括号里的第一句。if、while、for、switch、else、case 那一行不能当 line：点会落在整个判断或循环外面，不在你想要的那一支里。只有 {{ 或 }} 的行、#if/#else/#endif 也不能写；只有放在块里最后一句之后才写 after；同一句只放一个点（上面第 2 条的首包、末包除外）。例：要记 `}} else if (done) {{` 这一支，写它里面第一句 `stop();`、before。条件成立才执行的那一句（比如调用回调、发出数据），点写在 if 大括号里的第一句，不写在 if 那一行；首个结果、最后结果这类点也放在认出是这种消息之后那一支里，不放在消息分发入口。发送或调用本身写在 if 条件里的（比如 if (!send(x))），只能写 if 这一行、where 写 before。
5. 点放在链写明的那一支里，出错、失败、重试那一支不放。外面套着条件、可能不走的，always 写 false，除非在 why 里说明正常每轮都满足。
6. segments 按点发生的先后把相邻两点连起来就行（码表用它给每段起名字、写谁在忙，要测的这条线怎么连由码表按流程定）；先后说不准的写进 doubts，怎么量由码表处理。
7. 顺带发现的线（比如播放回复前先切换表情、开门前先亮灯）也列出来；它的分段除了从主线分出的那个点，用的点都要属于这条线本身（line_of 写 side；这条线的点可以不在事件链里；链里标了角色的步骤上的点都是主线，写 main）。同一件事有"收到"和"真正生效"两个时刻的，两个都列。
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
1. 选定一条实际的触发路径（比如喊唤醒词、按键、点屏幕、刷卡），写进 trigger。从处理这个输入事件的函数开始，沿真实的调用关系往下追（谁调用了谁、事件交给了谁），不能挑一个名字像的函数就用。设备会把输入往外发的（比如声音、传感器数据、刷卡上报），先顺着输入从采集一直追到真正把它发出去的那一句（起点仍按第 2 条，不从按键、触摸这类触发动作往下追），再挑步骤；有上传却缺了上传首包、末包或首个结果的链是错的。
2. 第一步用设备自己能察觉人做完输入的检测（比如静音或语音活动检测翻转、读卡完成、按键松开）。哪怕它只用来切换显示、指示灯，不上报，也算设备察觉到了，第一步就用它。确实找不到，在 detect 里写明找过哪里。
3. 运行时走哪种模式（比如聆听、工作模式）、哪种协议或通道：两样分别找到运行时真正决定它的那一行代码（不是变量的默认初始值），再查 sdkconfig、sdkconfig.defaults* 或 Kconfig 的取值，写进 mode 和 mode.protocol；几支判断里选其一的，写最前面那个 if 那一行；定不下来的写进 doubts。
4. 每一步写文件、行号、这一行的原文、所在的函数名、走的是哪一支（哪个条件成立才走）、一轮里只执行一次还是反复执行（逐帧、逐包、轮询），以及它为什么排在上一步后面；和上一步谁先谁后说不准的，after 写"不确定"。
5. 排顺序前，先读触发它的那一行代码或协议文档：消息或事件什么时候到、在哪种模式下才走，不能按名字猜。交给队列、Schedule、回调之后才执行的，按真正执行的时刻排。依据只能是代码、协议文档或注释，没有就写"不确定"。
6. 链里必须有：设备往外发数据的第一包和最后一包（有上传的话）、收到的第一个结果、最后一个结果，在对应步骤的 role 里标出来。逐包反复执行的同一句发送，可以同时标上传首包、上传末包（指这一句一轮里第一次和最后一次执行），下一步用 first、last 区分，不用猜哪一包是最后一包；首包、末包只标在一步上（真正发出数据的那一句；外层一句和它调用的那一句算同一次发送）。首个结果、最后结果这类收到某种消息或某个条件成立才发生的步骤，写认出是它之后那一支里的语句（比如处理这种消息的那一支里的第一句），不写通用的消息分发入口，也不写 else if、else 那一行。"最后"一类优先用整段真正结束的那一句，不用逐包、逐帧执行的那一行。真正把输出送出去的那一句（比如把声音写进扬声器、驱动执行器）单独列一步，role 标输出：首段、末段输出用它的第一次和最后一次执行，不用入队那句代替。代码里有输出或播放队列放空、动作完成这类"全部放完"信号的，单独列一步，写判断出放完的那一支里的语句（不写检查是否放完的入口，也不写之后转发、处理这个通知的地方），role 标全部放完；最后结果标在它和结束消息里更晚的那一步，说不准先后就两步都标；找不到这种信号，在 doubts 里写明"没有全部放完的信号"和找过哪里。链写到最后一个结果为止，下一轮的事不写。
7. 只在第一轮才走的步骤（比如唤醒、建立连接、打开音频通道），以及注册回调、初始化这类不在这一轮运行时执行的代码，不进链。
{hint}{cands}
## 最后输出
只输出一个 ```json 代码块（中文）：
```json
{{"trigger": "选定的触发路径", "upload": "设备往外发什么数据；不往外发写：无",
 "mode": {{"what": "运行时走哪种模式；不用选的写：无", "file": "决定模式的那一行所在文件", "line": 123, "code": "那一行的原文", "config": "相关配置和取值",
   "protocol": {{"what": "哪种协议或通道；只有一种的写：无", "file": "决定它的那一行所在文件", "line": 123, "code": "那一行的原文"}}}},
 "detect": {{"kind": "第一步用的是哪种输入结束检测，比如：静音检测翻转、读卡完成、按键松开", "found": true, "looked": "找不到时写找过哪里"}},
 "chain": [{{"n": 1, "what": "这一步设备做了什么", "file": "相对源码根目录的路径", "line": 123, "code": "这一行的原文", "func": "这一行所在的函数名", "role": "上传首包 / 上传末包 / 首个结果 / 最后结果 / 输出 / 全部放完，可写几个，用、隔开；其余空", "branch": "走哪一支或哪个条件成立才走，没有就空", "repeat": "once / repeat", "after": "为什么排在上一步后面：哪段代码、文档或注释；没有就写 不确定（第 1 步空着）"}}],
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
- 链里要有：上传的首包和末包、收到识别出的文字、第一段和最后一段声音播出、播放全部放完（播放队列放空的那一支）；协议里有回复开始和回复结束这一对消息的，两条都要进链（本地合成、一次性返回的没有就不写）。
- 上传首包、末包写真正发出数据的那一句，不写循环头、不写判断行；每一包都走同一句发送的，这一句同时标上传首包、上传末包。
- 声音全部播完，这一轮就结束；之后恢复聆听、开始下一轮都不进链。
"""
CARD_HINT = """
## 这一类设备（刷卡、门禁、门锁）要注意
- 要量的是第二次刷卡（第一次可能还在初始化、建连接）。
- 链按这个顺序找：察觉有卡、读出卡号、校验（本地比对或发给服务器）、继电器或门锁动作、声音或指示灯反馈；找不到的在 doubts 里写明。
- 门锁动作或提示音有"真正做完"信号的（比如动作到位、提示音放完），这一步标全部放完。
"""
HINTS = {"语音对话": VOICE_HINT, "门禁": CARD_HINT, "智能门锁": CARD_HINT, "刷卡终端": CARD_HINT}


CANDS_RULE = ("第 1 步、链里标了角色的步骤、mode、mode.protocol 先从这些候选里挑：在那一步（或 mode、mode.protocol）里写 \"cand\": \"K编号\"，"
              "file、line、code 码表照候选填。候选里都不合适才自己写，并在 doubts 里写为什么。\n")


def build_chain_prompt(what, chip="", category="", cands=None):
    """第一次调用：只理清一轮的流程。category 是认出的设备类别，有对应提醒就加上；cands 是码表在源码里找到的候选行。"""
    sec = cands_mod.prompt_section(cands) if cands else ""
    return CHAIN_PROMPT.format(what=what, chip=_chip(chip), hint=HINTS.get(str(category or "").strip(), ""),
                               cands=(sec + CANDS_RULE) if sec else "")


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


PICK_FIX_AGAIN = "码表核对你选的计时点时发现下面几处不对，其余都对：\n{items}\n不要再读源码，只改这几个点（id 不变，段照旧连好），把完整的结果重新输出一个 ```json 代码块。"
FIX_AGAIN = "码表核对你的流程时发现下面几处不对，其余都对：\n{items}\n不要再读源码，只改这几处，把完整的结果重新输出一个 ```json 代码块。"
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
ROLES = ("上传首包", "上传末包", "首个结果", "最后结果")
OUTPUT = "输出"  # 真正把输出送出去的那一句（第二步放 first、last 两个点）  # 事件链里必须标出的几步（不往外发数据的不要上传两项）
_DRAIN_WORD = re.compile(r"放空|放完|排空|drain", re.I)
DONE = "全部放完"  # 输出或播放队列放空、动作完成这类信号所在的那一步
NEED_DONE = {"语音对话"}  # 这些类别：链里要有"全部放完"这一步，或者在 doubts 里写明没有这种信号


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


UPLOAD_ROLES = ("上传首包", "上传末包")
_RECOGNIZED = re.compile(r"识别|转写|stt|asr|transcri|recogni", re.I)
_SILENCE = re.compile(r"静音|语音活动|说话结束|语音结束|vad|silence|end[ _-]?of[ _-]?speech|speech[ _-]?end", re.I)


def check_chain(root, data, category="", fixes=None, fresh=False, apply=False, cands=None):
    """两次调用之间码表检查理清的流程（只做做得可靠的）：
    文件、行号、原文对不对得上；必填项有没有写；第一步有没有写明是哪种输入结束检测。
    cands：码表在源码里找到的候选行（写了 cand 的照候选填；关键步骤不在候选里的问一次，还不在由码表换成最近的候选）。
    返回 (整理好的流程, 问题列表)；问题列表不空就是不合格。"""
    if not isinstance(data, dict):
        return None, ["回答的格式不对，码表看不懂"]
    bad, tips, roles, proj, up_steps, out_steps = [], [], set(), structure.Project(root), [], []
    repairs = []  # 码表按规则补上、改过的（只对新回答）
    cmap = {c["id"]: c for c in cands or [] if isinstance(c, dict) and c.get("id")}
    if cmap:
        data = _fill_cands(proj, data, cmap, bad, fixes)
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
            msrc = proj.file(mode["file"])
            mcode = msrc.clines[n - 1] if msrc is not None and 1 <= n <= len(msrc.clines) else ""
            if (fresh and msrc is not None and "=" in mcode and not re.search(r"\b(?:const|constexpr|define)\b", mcode)
                    and structure.func_at(msrc, n) == (None, [])):
                # 函数外面的变量默认值（比如成员的初始值）：运行时可能被改，不是真正决定模式的那一句
                bad.append(f"决定模式的那一行（{mode['file']} 第 {n} 行）是函数外面一个变量的默认初始值，运行时可能被改：要写运行时真正决定它的那一句")
                if fixes is not None:
                    fixes.append({"bad": bad[-1], "text": f"mode 写的是 {mode['file']} 第 {n} 行「{msrc.lines[n - 1].strip()}」，这是函数外面一个变量的默认初始值，"
                                                       "不是运行时真正决定模式的那一句：mode 的 file、line、code 改成运行时给它赋值、或算出它的那一句"
                                                       "（你在 config 里已经找到的话就用那一句）"})
    elif not mwhat.lower().startswith(_NONE):
        bad.append("写了运行时走哪种模式、哪种协议，但没写真正决定它的那一行代码")
    doubts = [str(d) for d in data.get("doubts") or [] if d] if isinstance(data.get("doubts"), list) else []
    if mwhat and not mwhat.lower().startswith(_NONE):  # 有模式要选：协议或通道也要单独写出决定它的那一行（或在 doubts 里说明）
        proto = mode.get("protocol") if isinstance(mode.get("protocol"), dict) else {}
        pwhat = str(proto.get("what") or "").strip()
        if proto.get("file") or proto.get("line"):
            n, why = _where_code(root, proto.get("file"), proto.get("line"), proto.get("code"))
            if why:
                bad.append(f"决定协议或通道的那一行：{why}")
            else:
                mode = {**mode, "protocol": {**proto, "file": _rel(root, proto.get("file")), "line": n}}
        elif not pwhat.lower().startswith(_NONE) and not any(w in d.lower() for d in doubts for w in ("协议", "通道", "protocol")):
            bad.append("没写决定协议或通道的那一行（mode.protocol；只有一种写「无」，定不下来在 doubts 里说明）")
    if fresh:
        mode = _mode_line_fixes(proj, mode, fixes, repairs if apply else None)
    det = data.get("detect") if isinstance(data.get("detect"), dict) else {}
    if not str(det.get("kind") or "").strip():
        bad.append("第一步没写明用的是哪种输入结束检测")
    if det.get("found") in (False, "false", "否") and not str(det.get("looked") or "").strip():
        bad.append("说找不到输入结束检测，但没写找过哪里")
    det_text = f"{det.get('kind') or ''} {det.get('looked') or ''}"
    first = next((c for c in data.get("chain") or [] if isinstance(c, dict)), None) if isinstance(data.get("chain"), list) else None
    if (fresh and first and det.get("found") not in (False, "false", "否") and _SILENCE.search(det_text)
            and not _SILENCE.search(f"{first.get('what') or ''} {first.get('code') or ''} {first.get('func') or ''}")):
        # 设备自己有说话结束一类的检测（静音、语音活动检测翻转）：第 1 步就得是它，不从按键、触摸这类触发动作往下追
        bad.append("detect 里写着设备有自己的输入结束检测（静音、语音活动检测翻转），第 1 步却不是它：第 1 步要用这个检测翻转的那一行"
                   "（只用来切换显示、指示灯也算），不从按键、触摸往下追")
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
        role = [r for r in ROLES + (OUTPUT, DONE) if r in str(c.get("role") or "")]
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
            real = check_location(root, c.get("file"), line_no(c.get("line"))[0]) if line_no(c.get("line"))[0] else (None, "x")
            if fixes is not None and not real[1] and real[0] is not None and "对不上" in why:  # 只是抄的原文对不上：追问时把文件里的原文给它
                fixes.append({"bad": bad[-1], "text": f"第 {n} 步：{_rel(root, c.get('file'))} 第 {line_no(c.get('line'))[0]} 行文件里是「{real[0]}」，"
                                                       f"你写的是「{str(c.get('code')).strip()}」。照抄文件里这一行（行号不对就改成原文所在的行）"})
            continue
        src = proj.file(_rel(root, c["file"]))
        got = structure.check_func(src, line, c["func"]) if src is not None else None
        if got and got[0] == "fail":
            bad.append(f"{tag}：{got[1]}")
            continue
        if got:
            tips.append(f"{tag}：{got[1]}")
        if any(r.startswith("上传") for r in role):
            up_steps.append(n)
        if OUTPUT in role:
            out_steps.append({"n": n, "file": _rel(root, c["file"]), "line": line, "chain_step": None})
        chain.append({"n": n, "what": str(c["what"]).strip(), "file": _rel(root, c["file"]), "line": line,
                      "code": check_location(root, c["file"], line)[0], "func": str(c["func"]).strip(), "role": role,
                      "branch": str(c.get("branch") or "").strip(), "repeat": rep, "after": str(c.get("after") or "").strip()})
    upload = str(data.get("upload") or "").strip()
    if fresh and len(up_steps) == 1:
        c = next(x for x in chain if x["n"] == up_steps[0])
        have = [r for r in UPLOAD_ROLES if r in c["role"]]
        other = [r for r in UPLOAD_ROLES if r not in roles]
        if len(have) == 1 and len(other) == 1 and c["repeat"] == "repeat":
            # 逐包反复执行的同一句发送只标了首包或末包：按提示词，这一句的第一次和最后一次执行就是首包和末包
            c["role"].append(other[0])
            roles.add(other[0])
            repairs.append(f"第 {c['n']} 步是逐包反复执行的发送，只标了{have[0]}：同一句的第一次和最后一次执行就是首包和末包，补上{other[0]}")
    need = [r for r in ROLES if r not in roles and not (r.startswith("上传") and upload.lower().startswith(_NONE))]
    if len(up_steps) > 1:  # 同一次发送只标一处
        bad.append(f"上传首包、上传末包标在了第 {'、'.join(map(str, up_steps))} 步：只能标在一步上（真正发出数据的那一句）")
        if fixes is not None:
            fixes.append({"bad": bad[-1], "text": f"上传首包、上传末包标在了第 {'、'.join(map(str, up_steps))} 步，只留一步（真正发出数据的那一句），其余步骤去掉这两个标记"})
    same_out = sorted({x["n"] for i, x in enumerate(out_steps) for y in out_steps[i + 1:]
                       if (x["file"], x["line"]) != (y["file"], y["line"]) and structure._same_thing(proj, x, y)} |
                      {y["n"] for i, x in enumerate(out_steps) for y in out_steps[i + 1:]
                       if (x["file"], x["line"]) != (y["file"], y["line"]) and structure._same_thing(proj, x, y)})
    if fresh and len(same_out) > 1:  # 外层和它调用的那一句算同一次输出：只标一处
        bad.append(f"输出标在了第 {'、'.join(map(str, same_out))} 步，它们是同一次输出（调用和被调用）：只能标在一步上")
        if fixes is not None:
            fixes.append({"bad": bad[-1], "text": f"输出标在了第 {'、'.join(map(str, same_out))} 步，它们是同一次输出（调用和被调用），只留一步（真正把输出送出去、且一轮里每段都经过的那一句），其余步骤去掉这个标记"})
    located = _locate(proj, chain, fixes, repairs, apply) if fresh else set()
    if fresh and cmap:
        mode = _cand_fixes(proj, chain, mode, list(cmap.values()), located, fixes, repairs, apply, tips)
    done_at = next((c for c in chain if DONE in c["role"]), None)
    said = [c["n"] for c in chain if done_at and c["n"] < done_at["n"] and DONE not in c["role"]
            and _DRAIN_WORD.search(re.split(r"[（(]", c["what"])[0])]
    if fresh and said and DONE not in located:
        # 前面的步骤已经写的是放空、放完：全部放完要标在判断出放完的那一支，不标在之后转发、处理这个通知的地方
        bad.append(f"全部放完标在了第 {done_at['n']} 步，可前面第 {'、'.join(map(str, said))} 步写的已经是放空、放完：要标在判断出放完的那一支里那一步")
        if fixes is not None:
            fixes.append({"bad": bad[-1], "text": f"全部放完标在了第 {done_at['n']} 步，可第 {'、'.join(map(str, said))} 步写的已经是放空、放完。"
                                                  "把全部放完标到其中判断出放完的那一支里那一步上，第 " + str(done_at["n"]) +
                                                  " 步去掉这个标记（它是之后转发、处理这个通知的地方；属于下一轮的事就整步去掉）"})
    if raw and need:
        bad.append(f"事件链里没标出：{'、'.join(need)}（在对应步骤的 role 里写；确实不往外发数据的，upload 写「无」）")
        if fixes is not None:  # 改 role 不用再读源码：步骤已经在链里的，追问时只补标记
            fixes.append({"bad": bad[-1], "text": f"链里没标出：{'、'.join(need)}。这些步骤已经在链里的（比如 doubts 里已经说了是哪一步），"
                                                   "只在那一步的 role 里补上，不加新步骤、不改别的"})
    if raw and str(category or "").strip() in NEED_DONE and DONE not in roles and not any(DONE in d for d in doubts):
        bad.append(f"事件链里没有「{DONE}」这一步（输出或播放队列放空这类信号所在的那一支）；代码里确实没有这种信号的，在 doubts 里写明\"没有{DONE}的信号\"和找过哪里")
    if (fresh and raw and str(category or "").strip() == "语音对话"
            and not any(_RECOGNIZED.search(f"{re.split(r'[（(]', str(c.get('what') or ''))[0]} {c.get('code') or ''}") for c in raw)
            and not any(re.search(r"没有[^，。；]{0,10}识别", str(d)) for d in doubts)):  # 只看这一步说的主句（括号里顺带提到的不算）
        bad.append("语音类的链里没有「收到识别出的文字」这一步（设备收到识别结果的那一支）；协议里确实没有这种消息的，在 doubts 里写明\"没有识别文字的消息\"和找过哪里")
    flow = {"trigger": trigger, "upload": upload, "mode": mode, "detect": det, "chain": chain,
            "doubts": [str(d) for d in data.get("doubts") or [] if d] if isinstance(data.get("doubts"), list) else [],
            "tips": tips, **({"repairs": repairs} if repairs else {})}  # 码表判断不了、放行的地方（比如 lambda 里写的函数名），只提醒
    return flow, bad


_CAND_ID = re.compile(r"\b[Kk]\s*(\d+)\b")
# 链里的角色 → 能用哪一类候选（最后结果可以是结束消息，也可以是全部放完；不用逐段执行的输出）
CAND_ROLES = {"上传首包": ("上传",), "上传末包": ("上传",), "首个结果": ("消息",), "最后结果": ("消息", "全部放完"),
              OUTPUT: ("输出",), DONE: ("全部放完",)}


def _fill_cands(proj, data, cmap, bad, fixes):
    """写了 "cand": "K3" 的：file、line、code（缺的 func）照候选填。编号不在列表里、自己又没写位置的，算问题并告诉它怎么改。"""
    data = dict(data)

    def fill(d, tag):
        if not isinstance(d, dict) or not d.get("cand"):
            return d
        m = _CAND_ID.search(str(d.get("cand")))
        c = cmap.get(f"K{m.group(1)}") if m else None
        if c is None:
            if not (d.get("file") and d.get("line")):
                bad.append(f"{tag}写的候选「{d.get('cand')}」不在码表给的候选列表里")
                if fixes is not None:
                    fixes.append({"bad": bad[-1], "text": f"{tag}写的候选「{d.get('cand')}」不在列表里：改成列表里的编号，或者自己写 file、line、code"})
            return d
        src = proj.file(c["file"])
        d = {**d, "file": c["file"], "line": c["line"], "code": src.lines[c["line"] - 1].strip() if src is not None else c["code"]}
        if not d.get("func") and src is not None:
            d["func"] = structure.func_at(src, c["line"])[0] or ""
        return d

    if isinstance(data.get("chain"), list):
        data["chain"] = [fill(c, f"第 {c.get('n') or i + 1} 步") if isinstance(c, dict) else c for i, c in enumerate(data["chain"])]
    if isinstance(data.get("mode"), dict):
        data["mode"] = fill(data["mode"], "mode ")
        if isinstance(data["mode"].get("protocol"), dict):
            data["mode"] = {**data["mode"], "protocol": fill(data["mode"]["protocol"], "mode.protocol ")}
    return data


def _near_cand(proj, file, line, cands):
    """这一行是不是候选之一，或者落在候选的那一支里（候选的 area：输入结束、收到某种消息、全部放完是那一支；其余只算那一句）。"""
    for c in cands:
        if c["file"] != file:
            continue
        a, b = (c.get("area") or [c["line"], c["line"]])[:2]
        if c["line"] == line or a < line <= b:
            src = proj.file(file)
            if c["line"] == line or (src is not None and structure.func_at(src, line)[0] == structure.func_at(src, c["line"])[0]):
                return c
    return None


def _msg_words(c):
    """消息候选比较的那几个字符串（比如「tts/start」→ tts、start）。"""
    m = re.search(r"为「([^」]+)」的消息", c.get("where") or "")
    return [w for w in m.group(1).split("/") if w] if m else []


def _fallback(proj, file, line, cands, single, text=""):
    """不在候选里时换成哪一个（找不准就 None，不换）：
    1. 这一步自己的说明（text）里写了某种消息的名字（候选比较的那个字符串）：换成这种消息那一支里的第一个候选；
    2. 同一个函数里，和它同在一支判断里的候选（最里层那一支相同的优先，同样深取离得近的）；只同在函数、回调里不算；
    3. 这一类只能有一处（single：输入结束、模式、协议）：同一个函数里最近的候选，或者只有一个候选就用它。
    写在同一个接口几种实现里的一种里的（比如某一种连接方式自己的发送）：换成候选里调用这个方法的那一句。"""
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text or "")}
    scored = [(sum(w.lower() in words for w in _msg_words(c)), bool(_msg_words(c)) and _msg_words(c)[-1].lower() in words, c) for c in cands]
    best = max(((n, last) for n, last, _c in scored), default=(0, False))
    if best[0] and best[1]:
        return min((c for n, last, c in scored if (n, last) == best), key=lambda c: c["line"])
    src = proj.file(file)
    if src is None or not 1 <= line <= len(src.lines):
        return cands[0] if single and len(cands) == 1 else None
    hit = structure.one_of_impls(proj, src, line)
    if hit:  # 写在几种实现里的一种里（只在这种实现下才走到）：换成候选里调用这个方法的那一句（哪种实现都会经过）
        call = re.compile(rf"(?:->|\.)\s*{re.escape(hit[1])}\s*\(")
        callers = [c for c in cands if call.search(c.get("code") or "")]
        if callers:
            return callers[0]
    opened = [i for i in structure._opened_on(src, line) if src.blocks[i]["kind"] != "lambda"]
    at = src.pos(line, "after") if opened else src.pos(line, "before")  # 判断开头那一行：算在它打开的那一支里
    fn = structure.func_at(src, line)[0]
    same = [c for c in cands if c["file"] == file and fn and structure.func_at(src, c["line"])[0] == fn]
    mine = set(src.chain(at))

    def common(c):
        both = mine & set(src.chain(src.pos(c["line"], "before")))
        return max(both, key=lambda i: src.blocks[i]["open"]) if both else None

    inbranch = [c for c in same if common(c) is not None and src.blocks[common(c)]["kind"] == "branch"]
    if inbranch:
        return max(inbranch, key=lambda c: (len(mine & set(src.chain(src.pos(c["line"], "before")))), -abs(c["line"] - line)))
    if single and same:
        return min(same, key=lambda c: abs(c["line"] - line))
    if single and len({(c["file"], tuple(c.get("area") or ())) for c in cands}) == 1:
        return cands[0]  # 候选都在同一支里（比如检测翻转那一支的两句）：用第一句
    return None


END_ROLES = ("最后结果", DONE)


def _end_verified(proj, c, ends, of, located):
    """这一步标了 ends 里的某个角色，而且位置核对过：码表认准了（located，比如全部放完那一支），或在这一类候选旁边。"""
    mine = [r for r in c["role"] if r in ends]
    if not mine:
        return False
    if any(r in located for r in mine):
        return True
    kinds = {k for r in mine for k in CAND_ROLES.get(r, ())}
    opts = of(kinds)
    return bool(opts) and bool(_near_cand(proj, c["file"], c["line"], opts))


def _cand_fixes(proj, chain, mode, cands, located, fixes, repairs, apply, tips):
    """关键步骤（第 1 步、标了角色的步骤）、mode、mode.protocol 和码表找到的候选对一对：
    不在候选里（也不和候选在同一支）的，问一次（不挡），列出能选的候选；apply 时还不在就换成最近的候选，换不了的只提醒。"""
    def of(kinds):
        return [c for c in cands if c["role"] in kinds]

    def listing(opts):
        return "；".join(f"{c['id']} {c['file']}:{c['line']}「{c['code'][:50]}」" for c in opts[:6])

    def ask(t):
        if fixes is not None:
            fixes.append({"bad": t, "text": t, "soft": True})

    for c in chain:
        kinds = set()
        if c is chain[0]:
            kinds.add("输入结束")
        for r in c["role"]:
            if r in (OUTPUT, DONE) and r in located:
                continue
            kinds.update(CAND_ROLES.get(r, ()))
        opts = of(kinds)
        if not opts or _near_cand(proj, c["file"], c["line"], opts):
            continue
        label = "、".join(c["role"]) or "第 1 步"
        ask(f"第 {c['n']} 步（{label}）写的是 {c['file']} 第 {c['line']} 行，不在码表找到的候选里：从这些候选里选一个写进 cand（{listing(opts)}）；"
            "确实都不合适就在 doubts 里写为什么")
        if not apply:
            continue
        single = kinds <= {"输入结束"}
        to = _fallback(proj, c["file"], c["line"], opts, single, f"{c.get('what') or ''} {c.get('branch') or ''}")
        ends = [r for r in c["role"] if r in END_ROLES]
        if to is None and ends:
            # 一轮的结尾（最后结果、全部放完）不在候选里、追问也没改过来（8783：标在了主循环里转发"播放放空"通知的那一支，
            # 一轮会走好几次，清零放在那里每句之间都清一次）：不带着它往下走，按通用的办法自己定——
            # 别的步骤已经标了这个角色、而且在候选里（或码表认准了位置）：去掉这一步的这个标记，一轮的结尾按那一步；
            # 没有：换成候选里离它最近的（同一个文件优先）
            ok = [o for o in chain if o is not c and _end_verified(proj, o, ends, of, located)]
            if ok:
                c["role"] = [r for r in c["role"] if r not in ends]
                repairs.append(f"第 {c['n']} 步（{label}）不在码表找到的候选里，追问后也没改对；第 {'、'.join(str(o['n']) for o in ok)} 步"
                               f"已经标了{'、'.join(ends)}、位置核对过：去掉第 {c['n']} 步的{'、'.join(ends)}，一轮的结尾按核对过的那一步")
                continue
            to = min(opts, key=lambda o: (o["file"] != c["file"], abs(o["line"] - c["line"])))
        if to is None:
            tips.append(f"第 {c['n']} 步（{label}）不在码表找到的候选里，码表也换不了，请留意")
            continue
        old = f"{c['file']}:{c['line']}"
        src = proj.file(to["file"])
        c.update(file=to["file"], line=to["line"], code=src.lines[to["line"] - 1].strip(), func=structure.func_at(src, to["line"])[0] or c.get("func"))
        repairs.append(f"第 {c['n']} 步（{label}）从 {old} 换成候选 {to['id']} {to['file']}:{to['line']}")
    for key, kind, label in (("mode", "模式", "模式"), ("mode.protocol", "协议", "协议或通道")):
        d = mode if key == "mode" else (mode.get("protocol") if isinstance(mode, dict) else None)
        opts = of({kind})
        if not opts or not isinstance(mode, dict) or str(mode.get("what") or "").strip().lower().startswith(_NONE):
            continue
        if (not isinstance(d, dict) or not d.get("file")) and len(opts) == 1:  # 没写决定它的那一行：只有一个候选就由码表填上
            ask(f"{key} 没写决定{label}的那一行；码表找到的是 {listing(opts)}：对的话写进 {key} 的 cand")
            if apply:
                to = opts[0]
                src = proj.file(to["file"])
                nd = {**(d if isinstance(d, dict) else {}), "file": to["file"], "line": to["line"], "code": src.lines[to["line"] - 1].strip()}
                repairs.append(f"{label}没写，填上候选 {to['id']} {to['file']}:{to['line']}")
                mode = {**mode, **nd} if key == "mode" else {**mode, "protocol": nd}
            continue
        if not isinstance(d, dict) or not d.get("file") or not isinstance(d.get("line"), int):
            continue
        if _near_cand(proj, d["file"], d["line"], opts) or any(f"{key} 的" in f["text"] for f in fixes or []):
            continue
        ask(f"{key} 写的是 {d['file']} 第 {d['line']} 行，不在码表找到的{label}候选里：从这些候选里选一个写进 {key} 的 cand（{listing(opts)}）")
        if not apply:
            continue
        to = _fallback(proj, d["file"], d["line"], opts, True)
        if to is None:
            continue
        src = proj.file(to["file"])
        nd = {**d, "file": to["file"], "line": to["line"], "code": src.lines[to["line"] - 1].strip()}
        repairs.append(f"{label}从 {d['file']}:{d['line']} 换成候选 {to['id']} {to['file']}:{to['line']}")
        mode = {**mode, **nd} if key == "mode" else {**mode, "protocol": nd}
    return mode


def _else_if_head(src, line):
    """第 line 行是 else if：往上找这一串判断最前面那个 if 那一行（同样缩进）。找不到返回 None。"""
    raw = src.lines[line - 1]
    ind = len(raw) - len(raw.lstrip())
    for k in range(line - 1, 0, -1):
        r = src.lines[k - 1]
        c = src.clines[k - 1].strip()
        if not c or len(r) - len(r.lstrip()) != ind:
            continue
        if re.match(r"if\b", c):
            return k
        if not re.match(r"\}?\s*else\b", c) and c != "}":
            return None
    return None


def _mode_line_fixes(proj, mode, fixes, repairs):
    """模式、协议写在了 else if 那一行（几支判断里选其一的要写最前面那个 if）、模式写在了拿它和取值比较的判断上：
    能找到最前面那个 if 的，追问时给出来，还不改就由码表改；只是比较的，追问一次，不改就照旧（不挡）。"""
    def one(d, label, key):
        if not isinstance(d, dict) or not d.get("file") or not isinstance(d.get("line"), int):
            return d
        src = proj.file(d["file"])
        if src is None or not 1 <= d["line"] <= len(src.clines):
            return d
        c = src.clines[d["line"] - 1].strip()
        if re.match(r"\}?\s*else\s+if\b", c):
            k = _else_if_head(src, d["line"])
            if k:
                t = (f"{label}写的是 {d['file']} 第 {d['line']} 行「{src.lines[d['line'] - 1].strip()}」，这是 else if 那一支；几支判断里选其一的，"
                     f"写最前面那个 if：第 {k} 行「{src.lines[k - 1].strip()}」。{key} 的 line、code 改成它")
                if fixes is not None:
                    fixes.append({"bad": t, "text": t, "soft": True})
                if repairs is not None:
                    repairs.append(f"{label}从第 {d['line']} 行（else if）改成同一串判断最前面的第 {k} 行")
                    return {**d, "line": k, "code": src.lines[k - 1].strip()}
        elif key == "mode" and re.match(r"(?:if|while)\b", c) and re.search(r"[=!]=", c):
            t = (f"模式写的是 {d['file']} 第 {d['line']} 行「{src.lines[d['line'] - 1].strip()}」，这是在拿已经定下来的模式和某个取值比较，"
                 "不是决定它的那一句：mode 的 file、line、code 改成运行时给它赋值、或算出它的那一句")
            if fixes is not None:
                fixes.append({"bad": t, "text": t, "soft": True})
        return d
    mode = one(mode, "模式", "mode")
    if isinstance(mode, dict) and isinstance(mode.get("protocol"), dict):
        mode = {**mode, "protocol": one(mode["protocol"], "协议或通道", "mode.protocol")}
    return mode


def _locate(proj, chain, fixes, repairs, apply):
    """码表自己找全部放完和输出那一句，和模型写的对一对：不一样就追问一次（不挡），还不一样由码表改（apply）。
    找不准的不管，照旧用模型的。返回找准了的角色集合。"""
    got = set()
    out = next((c for c in chain if OUTPUT in c["role"]), None)
    prefer = None

    def text_of(f, k):
        src = proj.file(f)
        return src.lines[k - 1].strip() if src is not None else ""

    def move(c, role, at, why):
        tgt = next((x for x in chain if (x["file"], x["line"]) == at), None)
        if fixes is not None:
            if tgt is not None and tgt is not c:
                t = (f"第 {c['n']} 步标了{role}，写的是 {c['file']} 第 {c['line']} 行；{why}是第 {tgt['n']} 步（{at[0]} 第 {at[1]} 行「{text_of(*at)}」）："
                     f"把{role}标到第 {tgt['n']} 步，第 {c['n']} 步去掉这个标记")
            else:
                t = (f"第 {c['n']} 步标了{role}，写的是 {c['file']} 第 {c['line']} 行；{why}是 {at[0]} 第 {at[1]} 行「{text_of(*at)}」："
                     f"这一步的 file、line、code 改成它（role 照旧）")
            fixes.append({"bad": t, "text": t, "soft": True})
        if not apply:
            return
        if tgt is not None and tgt is not c:
            c["role"] = [r for r in c["role"] if r != role]
            if role not in tgt["role"]:
                tgt["role"].append(role)
            repairs.append(f"{role}从第 {c['n']} 步挪到第 {tgt['n']} 步（{at[0]}:{at[1]}，{why}）")
        else:
            src = proj.file(at[0])
            old = f"{c['file']}:{c['line']}"
            c.update(file=at[0], line=at[1], code=text_of(*at), func=structure.func_at(src, at[1])[0] or c.get("func"))
            repairs.append(f"第 {c['n']} 步（{role}）从 {old} 改成 {at[0]}:{at[1]}（{why}）")

    if out is not None:
        try:
            at = structure.locate_output(proj, out["file"], out["line"]) if out["repeat"] == "repeat" else None
        except Exception:
            at = None
        if at:
            got.add(OUTPUT)
            if at != (out["file"], out["line"]):
                move(out, OUTPUT, at, "顺着调用往外找，逐段循环里每段输出都经过的那一句")
                if apply:
                    out = next((c for c in chain if OUTPUT in c["role"]), out)
            for c in [x for x in chain if OUTPUT in x["role"] and (x["file"], x["line"]) != at]:
                if c is out and not apply:
                    continue  # 上面已经问过这一步
                try:
                    same = c["repeat"] == "repeat" and structure.locate_output(proj, c["file"], c["line"]) == at
                except Exception:
                    same = False
                if same:  # 别的步骤也标了输出，往外找落在同一句：是同一次输出的里层，去掉这个标记
                    t = f"第 {c['n']} 步也标了输出，它和 {at[0]} 第 {at[1]} 行是同一次输出（调用和被调用）：去掉这个标记"
                    if fixes is not None:
                        fixes.append({"bad": t, "text": t, "soft": True})
                    if apply:
                        c["role"] = [r for r in c["role"] if r != OUTPUT]
                        repairs.append(f"第 {c['n']} 步去掉输出（和 {at[0]}:{at[1]} 是同一次输出）")
        src = proj.file(at[0] if at else out["file"])
        prefer = (at or (out["file"], out["line"]))
        prefer = (prefer[0], structure.func_at(src, prefer[1])[0]) if src is not None else None
    dones = [c for c in chain if DONE in c["role"]]
    if dones:
        try:
            at = structure.locate_done(proj, dones[0]["file"], dones[0]["line"], prefer)
        except Exception:
            at = None
        if at:
            got.add(DONE)
            if at != (dones[0]["file"], dones[0]["line"]):
                move(dones[0], DONE, at, "顺着放空通知往回找，判断出放完的那一支里的那一句")
            for c in dones[1:]:  # 别的步骤也标了全部放完：多半是之后转发、处理这个通知的地方
                if (c["file"], c["line"]) == at:
                    continue
                try:
                    same = structure.locate_done(proj, c["file"], c["line"], prefer) == at
                except Exception:
                    same = False
                if same:
                    t = f"第 {c['n']} 步也标了全部放完，它是之后转发、处理放空通知的地方：去掉这个标记（属于下一轮的事就整步去掉）"
                    if fixes is not None:
                        fixes.append({"bad": t, "text": t, "soft": True})
                    if apply:
                        c["role"] = [r for r in c["role"] if r != DONE]
                        repairs.append(f"第 {c['n']} 步去掉全部放完（它是之后转发、处理放空通知的地方）")
    return got


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
        twin = next((x for x in steps if x["file"] and (x["file"], x["line"], x["where"]) == (step["file"], step["line"], step["where"])
                     and {str(x["once"]).lower(), str(step["once"]).lower()} != {"first", "last"}), None)  # 同一句的第一次和最后一次不算
        if twin:  # 和前面的点插在同一处：记到的时间一样，分不出先后
            step["sure"] = "低"
            step["doubt"] = f"和「{twin['name']}」插在同一行代码上，两点记到的时间一样，分不出先后" + (("；" + step["doubt"]) if step["doubt"] else "")
        steps.append(step)
    if len(steps) < 2:
        return _fail("bad_reply", "大模型只给出了不到 2 个步骤，量不出用时。点\"重试\"再找一次，或者换个说法写\"测从什么到什么\"。",
                     found=[s["name"] for s in steps])
    if strict and data.get("chain"):  # 两步识别的新结果：关键步骤上的点和主线的段由码表按流程放、连，再理顺（负段、先后说不准、没量到的最后结果）
        data["chain"] = [dict(c) if isinstance(c, dict) else c for c in data["chain"]]  # 下面会改行号：不动调用方的流程
        lnotes = lift_alt_branches(root, steps, data["chain"])
        steps, pnotes = place_role_points(root, steps, data["chain"])
        steps, snotes = drop_stream_points(steps, data["chain"])
        steps, fnotes = follow_start_points(steps, data["chain"])
        pnotes = lnotes + pnotes + snotes + fnotes + round_end_always(steps, data["chain"])
        side_add, onotes = side_for_one_impl(root, steps, data["chain"], data.get("segments"))
        if side_add:
            data["side_segments"] = [g for g in data.get("side_segments") or [] if isinstance(g, dict)] + side_add
            pnotes = pnotes + onotes
        built, bnotes = structure.build_segments(steps, data.get("segments"))
        data["segments"], notes = structure.repair_segments(root, steps, built, data["chain"])
        data["seg_repairs"] = pnotes + bnotes + notes
        data["rules"] = RULES_VER
        loops = [g for g in data.get("side_segments") or [] if isinstance(g, dict) and g.get("from") and g.get("from") == g.get("to")]
        if loops:  # 自己连到自己的段量不出东西：去掉
            data["side_segments"] = [g for g in data.get("side_segments") or [] if g not in loops]
            data["seg_repairs"] = list(data["seg_repairs"]) + [f"删掉「{g.get('name') or ''}」：两头是同一个点（{g.get('from')}），量不出东西" for g in loops]
    structure.check(root, steps, data.get("segments"), data.get("side_segments"), data.get("chain"), strict=strict)  # 位置在不在之外，再看插下去落在什么结构里
    return {"ok": True, **data, "steps": steps, "warn": warn}


POINT_ROLES = ROLES + (OUTPUT, DONE)
# 码表放点、连段的规则版本：存下的两步识别结果版本不同，沿用时用存下的大模型回答按现在的规则重排一遍（不调大模型）
# 2：去掉起点之前就一直在跑的流水线中间那几步；全部放完每次都记
# 3：起点和第一个结果之间的点跟着起点记（起点一轮报好几次时，每报一次重新记）
# 4：只在按设置选的某一支里（比如某个协议版本）、别的支也调用同一个函数的点，挪到整个判断前面；
#    一轮一次的主线点全被标成"不一定走"时，把最后结果那一步改回一定走（一轮结束要靠它）
RULES_VER = 4
RESULT_ROLES = ("首个结果", OUTPUT, "最后结果", DONE)  # 设备拿到了结果、开始往外给的那几步（不算上传）


def _want(roles, repeat):
    """这一步放哪几个点：一轮一次的放一个；反复执行的，首段末段都要的（输出、同时标了上传首包和末包的）放 first、last，
    只要开头的（起点、首个结果、上传首包）放 first，只要结尾的（最后结果、全部放完、上传末包）放 last。"""
    if OUTPUT in roles:  # 首段、末段输出：一定是同一句的第一次和最后一次
        return ["first", "last"]
    if not repeat:
        return ["once"]
    head = not roles or any(r in roles for r in ("首个结果", "上传首包"))
    tail = any(r in roles for r in ("最后结果", DONE, "上传末包"))
    if OUTPUT in roles or (head and tail):
        return ["first", "last"]
    return ["last"] if tail else ["first"]


def _snap_plain(src, c, pts, notes, code):
    """没标角色的步骤：模型的点不在那一步那一行所在的那一支里、或者插在判断开头、分号、大括号、半句话上，挪到那一步那一行前面；
    反复执行的一步写了 once 的改成 first（每圈都执行，只能记第一次）。在那一支里的真语句上的不动。"""
    for p in pts:
        ok_line = p.get("file") == c["file"] and isinstance(p.get("line"), int) and 1 <= p["line"] <= len(src.lines)
        if c.get("lifted"):  # 这一步挪到了整个判断前面（见 lift_alt_branches）：点只能在那一行前面
            good = ok_line and p["line"] == c["line"] and p.get("where") == "before"
        else:
            good = ok_line and (structure.real_stmt(src, p["line"]) or (structure.call_in_if(src.clines[p["line"] - 1]) and p.get("where") != "after")) \
                and structure.in_step_area(src, c["line"], src.pos(p["line"], p.get("where") or "before"))
        if not good:
            old = f"{p.get('file')}:{p.get('line')}"
            p.update(file=c["file"], line=c["line"], where="before", code=code, loc_ok=True)
            if c.get("lifted"):
                p["lifted"] = c["lifted"]
            notes.append(f"「{p.get('name')}」不在第 {c['n']} 步那一支里的一句真语句上（原来 {old}），挪到那一步那一行前面")
        if c.get("repeat") == "repeat" and structure._kind(p) == "once":
            p["once"] = "first"



def _merge_same_line(jobs, by_n, first_n):
    """几步写在同一文件同一行、又都是要 first/last 的（比如模型把首段输出、末段输出拆成两步）：同一句只放一对点，
    first 放在其中最早那一步，last 放在最晚那一步，中间的步骤不再放；不再每一步各放一对（同一行四个点、两两同时）。"""
    single = {g[0]: plan[g[0]] for g, plan in jobs}
    out, done = [], set()
    for g, plan in jobs:
        n = g[0]
        if n in done:
            continue
        same = sorted(m for m in structure._same_line_steps(by_n, n) if m in single and m not in done and m != first_n
                      and set(single[m]) <= {"first", "last"}) if n != first_n else []
        if len(same) < 2 or n not in same:
            out.append([g, plan])
            done.add(n)
            continue
        kinds = set().union(*(single[m] for m in same))
        merged = {m: [] for m in same}
        if "first" in kinds:
            merged[same[0]].append("first")
        if "last" in kinds:
            merged[same[-1]].append("last")
        out.append([same, merged])
        done |= set(same)
    return out



def side_for_one_impl(root, steps, chain, segments):
    """没标角色的一步落在"几种实现里的一种"里（比如几种连接方式各写了一份发送），而流程里另有一步就是调用这个方法的那一句：
    那一句不管走哪种实现都会执行，主线用它量；这一步上的点只在这种实现下才会触发，改成顺带那条线，从调用那一步的同类点量起。
    返回 (加的旁线段, [说明])。"""
    proj = structure.Project(root)
    by_n = {c["n"]: c for c in chain if isinstance(c, dict) and isinstance(c.get("n"), int)}
    if not by_n:
        return [], []
    first_n, added, notes = min(by_n), [], []
    model = [g for g in segments or [] if isinstance(g, dict)]
    for n, c in sorted(by_n.items()):
        roles = c.get("role") or []
        if n == first_n or any(r in (roles if isinstance(roles, list) else str(roles)) for r in POINT_ROLES):
            continue
        mine = [x for x in steps if x.get("chain_step") == n and x.get("line_of") in ("start", "main")]
        src = proj.file(c.get("file") or "")
        line = c.get("line")
        if not mine or src is None or not isinstance(line, int) or not 1 <= line <= len(src.lines):
            continue
        hit = structure.one_of_impls(proj, src, line)
        if not hit:
            continue
        cls, name, others = hit
        call = re.compile(rf"(?:->|\.)\s*{re.escape(name)}\s*\(")
        caller = None
        for m, d in sorted(by_n.items()):
            s2 = proj.file(d.get("file") or "") if m != n else None
            ln = d.get("line")
            if s2 is not None and isinstance(ln, int) and 1 <= ln <= len(s2.lines) and call.search(s2.clines[ln - 1]) \
                    and any(x.get("chain_step") == m and x.get("line_of") in ("start", "main") for x in steps):
                caller = m
                break
        if caller is None:
            continue
        base = [x for x in steps if x.get("chain_step") == caller and x.get("line_of") in ("start", "main")]
        for q in mine:
            q["line_of"] = "side"
            k = structure._kind(q)
            a = next((x for x in base if structure._kind(x) == k), None) or next((x for x in base if structure._kind(x) != "last"), base[0])
            g = next((g for g in model if g.get("to") == q.get("id")), None)
            added.append({"from": a.get("id"), "to": q.get("id"), "name": (g or {}).get("name") or f"{a.get('name')}到{q.get('name')}",
                          "who": (g or {}).get("who") or "", "outside": bool((g or {}).get("outside")), "added": True})
        notes.append(f"第 {n} 步在 {cls}::{name} 里，这只是几种实现中的一种（另有 {'、'.join(others[:3])}），换一种时走不到："
                     f"它的点改成顺带那条线，从第 {caller} 步调用 {name} 的那一句量起；主线用第 {caller} 步")
    return added, notes

def _roles_of(c):
    roles = c.get("role") or []
    return [r for r in POINT_ROLES if r in (roles if isinstance(roles, list) else str(roles))]


def drop_stream_points(steps, chain):
    """起点之后、第一个标了角色的步骤（比如上传首包、首个结果）之前，反复执行（逐帧、逐包）又没标角色的步骤：
    这几步是起点之前就一直在跑的流水线（输入还在源源不断进来，比如录音一帧帧切好、压缩、排队）。
    起点之后各自"第一次"处理的不是同一份数据（这一步的第一次可能是起点前那一帧，下一步的第一次是更早的那一帧），
    两点之间量出来的不是这份数据走了多久，先后也说不准、会是负的（真测：音频帧交编码比编码好的包入队还晚 0.15 秒）。
    这几步上的点去掉，前后两点直接连成一段量。返回 (点, [说明])。"""
    by_n = {c["n"]: c for c in chain if isinstance(c, dict) and isinstance(c.get("n"), int)}
    if not by_n:
        return steps, []
    first_n = min(by_n)
    roled = sorted(n for n, c in by_n.items() if n != first_n and _roles_of(c))
    if not roled:
        return steps, []
    stream = {n for n, c in by_n.items() if first_n < n < roled[0] and c.get("repeat") == "repeat" and not _roles_of(c)}
    out, notes = [], []
    for x in steps:
        if x.get("chain_step") in stream and x.get("line_of") in ("start", "main"):
            notes.append(f"去掉「{x.get('name')}」：这一步在起点之前就一直逐帧、逐包在跑（输入还在不断进来），"
                         f"起点之后它第一次处理的和前后的点不是同一份数据，两点之间量不准、会是负的；前后两点直接连成一段量")
            continue
        out.append(x)
    return out, notes


def follow_start_points(steps, chain):
    """起点那一步（比如"检出说话结束"）一轮常常报好几次：每停一下、每阵杂音都判一次，码表取第一个结果之前的最后一次当起点。
    起点和第一个结果（首个结果、输出、最后结果、全部放完）之间的点（比如同一处的"静音回调"、持续上传时的"上传首包"）
    如果只记第一次，记到的是第一次起点之后的那一下，和码表取的起点对不上（10-26 真测：10 轮里 9 轮这几段空着）。
    这些点标上 follow：计时点工具每报一次起点就让它们重新记（见 fw_stopwatch.h 的 FSW_FOLLOW_MASK），
    收数据时也只认码表取的那次起点之后的。返回 (点, [说明])。"""
    by_n = {c["n"]: c for c in chain if isinstance(c, dict) and isinstance(c.get("n"), int)}
    if not by_n:
        return steps, []
    first_n = min(by_n)
    res = sorted(n for n, c in by_n.items() if n != first_n and any(r in _roles_of(c) for r in RESULT_ROLES))
    if not res:
        return steps, []
    start = next((x for x in steps if x.get("line_of") == "start"), None)
    mine = [x for x in steps if x is not start and x.get("line_of") in ("start", "main")
            and isinstance(x.get("chain_step"), int) and first_n <= x["chain_step"] < res[0]]
    for x in mine:
        x["follow"] = "start"
    if not mine:
        return steps, []
    names = "」「".join(str(x.get("name")) for x in mine)
    return steps, [f"「{names}」跟着起点记：起点一轮会报好几次（每停一下、每阵杂音都判一次），码表取第一个结果之前的最后一次；"
                   f"这几步在起点和第一个结果之间，每报一次起点就重新记，量的是同一次之后的那一下"]


def lift_alt_branches(root, steps, chain):
    """流程里一步（或旁线上的一个点）写在"按设置选一支"的判断的某一支里，而别的每一支也调用同一个函数
    （比如按协议版本各解一种包、都交给同一个回调）：设备只走其中一支，设置不同就一次也记不到（10-26 真测：
    「首个结果到达」放在协议 3 那一支，设备用协议 1，一直等不到）。挪到整个判断前面（不管哪种设置都会经过）。
    最后那个默认的 else / default 不挪（什么都没设时走它，也是之前测通过的那种）。返回 [说明]。"""
    proj = structure.Project(root)
    notes = []
    for c in chain:
        if not isinstance(c, dict) or not isinstance(c.get("line"), int):
            continue
        src = proj.file(c.get("file") or "")
        alt = structure.alt_branch(src, c["line"]) if src is not None else None
        if not alt:
            continue
        old = c["line"]
        c["lifted_from"] = old
        c["lifted"] = structure.alt_text(src, old, alt, moved=True)
        c["line"] = alt["head"]
        for x in steps:
            if x.get("chain_step") == c.get("n") and x.get("file") == c.get("file") and x.get("line_of") != "side":
                x.update(line=alt["head"], where="before", lifted=c["lifted"], code=check_location(root, c["file"], alt["head"])[0], loc_ok=True)
        notes.append(f"第 {c.get('n')} 步：" + c["lifted"])
    for x in steps:  # 旁线上的点、没对应流程哪一步的点：直接看它自己那一句
        if x.get("lifted") or not isinstance(x.get("line"), int) or (x.get("line_of") != "side" and x.get("chain_step") is not None):
            continue
        src = proj.file(x.get("file") or "")
        alt = structure.alt_branch(src, x["line"]) if src is not None else None
        if not alt:
            continue
        old = x["line"]
        x.update(line=alt["head"], where="before", lifted=structure.alt_text(src, old, alt, moved=True),
                 code=check_location(root, x["file"], alt["head"])[0], loc_ok=True)
        notes.append(f"「{x.get('name')}」：" + x["lifted"])
    return notes


def round_end_always(steps, chain):
    """一轮结束要靠主线上最后一个一轮一次的点（码表在它后面清零、把"最后一次"的点报出来）。
    模型把这几个点全标成"不一定走"（always false）时，就没有一轮结束了：要最后一次的点一直报不出来（10-26 真测：上行末包一直等不到）。
    把标了最后结果的那一步（没有就取流程最后的那个）改回一定走。返回 [说明]。"""
    once = [x for x in steps if x.get("line_of") == "main" and structure._kind(x) == "once"]
    if not once or any(x.get("always", True) for x in once):
        return []
    by_n = {c.get("n"): c for c in chain if isinstance(c, dict)}
    ends = [x for x in once if "最后结果" in _roles_of(by_n.get(x.get("chain_step")) or {})]
    pick = (ends or sorted(once, key=lambda x: x.get("chain_step") if isinstance(x.get("chain_step"), int) else -1))[-1]
    pick["always"] = True
    pick["end_why"] = "一轮结束靠这个点"
    return [f"「{pick.get('name')}」改成每轮都走：一轮一次的点模型全标成了不一定走，一轮结束要靠这一个（在它后面清零、报出最后一次的点）；"
            f"设备如果真不走这一步（比如设成了别的模式），试一次时会提示"]


def place_role_points(root, steps, chain):
    """第 1 步和标了角色的步骤上的点由码表放：反复执行的放 first、last 两个，一轮一次的放一个，都插在那一步那一行前面。
    模型在这一步写的点留着名字、felt 这些，只改位置和 first/last/once；多出来的去掉，要两个却只放了一个的补上另一个。
    模型在这一步一个点都没放的不替它加（结构检查照旧会指出、追问）。
    那一步那一行不是一句真正的语句（也不是发送写在条件里的 if）就不动，照旧用模型的点。
    返回 (排好先后的点, [说明])：主线的点按流程第几步、first/once/last 排，顺带那条线的点留在原来的位置。"""
    proj = structure.Project(root)
    by_n = {c["n"]: c for c in chain if isinstance(c, dict) and isinstance(c.get("n"), int)}
    if not by_n:
        return steps, []
    first_n, notes = min(by_n), []
    used = {s["id"] for s in steps}
    keep = list(steps)
    ends = [n for n, c in by_n.items() if any(r in (c.get("role") or []) for r in ("最后结果", DONE))]
    if ends:  # 链写到最后一个结果为止：排在最后结果、全部放完之后、又没标角色的步骤是下一轮的事，上面的点不要
        for x in [x for x in keep if x.get("chain_step") in by_n and x["chain_step"] > max(ends) and x.get("line_of") != "side"
                  and not any(r in (by_n.get(x["chain_step"], {}).get("role") or []) for r in POINT_ROLES)]:
            keep.remove(x)
            notes.append(f"去掉「{x.get('name')}」：第 {x['chain_step']} 步排在最后结果、全部放完之后，是下一轮的事")
    jobs, plain = [], []
    for n, c in sorted(by_n.items()):
        roles = c.get("role") or []
        roles = [r for r in POINT_ROLES if r in (roles if isinstance(roles, list) else str(roles))]
        src = proj.file(c.get("file") or "")
        line = c.get("line")
        if src is None or not isinstance(line, int) or not 1 <= line <= len(src.lines) or not (
                c.get("lifted") or structure.real_stmt(src, line) or structure.call_in_if(src.clines[line - 1])):
            continue
        if n != first_n and not roles:
            plain.append((n, c, src, line))
            continue
        jobs.append([[n], {n: _want(roles, c.get("repeat") == "repeat")}])
    jobs = _merge_same_line(jobs, by_n, first_n)
    for n, c, src, line in plain:
        _snap_plain(src, c, [x for x in keep if x.get("chain_step") == n and x.get("line_of") != "side"], notes, check_location(root, c["file"], line)[0])
    for group, plan in jobs:
        mine = [x for x in keep if x.get("chain_step") in group]  # 标了角色的步骤是主线：写成旁线的也算这一步的点
        if not mine:  # 模型在这一步一个点都没放：不替它加（结构检查照旧会指出、追问）
            continue
        for n in group:
            c = by_n[n]
            line = c["line"]
            code, _p = check_location(root, c["file"], line)
            for k in plan.get(n) or []:
                p = (next((x for x in mine if x.get("chain_step") == n and structure._kind(x) == k), None)
                     or next((x for x in mine if structure._kind(x) == k), None)
                     or next((x for x in mine if x.get("chain_step") == n), None) or (mine[0] if mine else None))
                if p is not None:
                    mine.remove(p)
                    was = (p.get("file"), p.get("line"), structure._kind(p), p.get("chain_step"))
                else:
                    pid = next(f"T{n}{suffix}" for suffix in ("", "b", "c", "d") if f"T{n}{suffix}" not in used)
                    used.add(pid)
                    nm = re.split(r"[，,（(]", str(c.get("what") or ""))[0][:10] or f"第 {n} 步"
                    sib = next((x for x in keep if x.get("chain_step") in group and x.get("line_of") != "side"), None)
                    p = {"id": pid, "name": nm + ({"first": "（首次）", "last": "（末次）"}.get(k, "") if len(group) == 1 else ""),
                         "sure": "中", "why": "码表按流程放在这一步",
                         "doubt": "", "always": True, "felt": "last" if k == "last" and sib and sib.get("felt") else "",
                         "kind": (sib or {}).get("kind") or "" if k == "last" and sib and sib.get("felt") else "", "added": True}
                    keep.insert(keep.index(sib) + 1 if sib in keep else len(keep), p)
                    was = None
                p.update(file=c["file"], line=line, func=c.get("func") or p.get("func") or "", where="before", once=k, code=code, loc_ok=True,
                         chain_step=n, line_of="start" if n == first_n else "main")
                if c.get("lifted"):
                    p["lifted"] = c["lifted"]
                if k == "once" and n != first_n and DONE in _roles_of(c) and OUTPUT not in _roles_of(c):
                    # 全部放完（播放队列放空、动作做完）：一轮里可能放空好几次（回复分几句播，每句之间都会放空一下），
                    # 只记第一次就只量到第一句。每次都记，码表取这一轮最后那一次
                    p["once"], p["every_why"] = "every", "done"
                if was is None:
                    notes.append(f"补上第 {n} 步的点「{p['name']}」（{k}）")
                elif was != (c["file"], line, k, n):
                    notes.append(f"「{p['name']}」按流程放到第 {n} 步那一行（{c['file']}:{line}，{k}）")
                if p.get("doubt", "").startswith("和「"):  # 同一行的 first、last 两个点：本来就是同一句的第一次和最后一次
                    p["doubt"] = re.sub(r"^和「[^」]*」插在同一行代码上，两点记到的时间一样，分不出先后；?", "", p["doubt"])
        for x in mine:  # 这一步多出来的点
            keep.remove(x)
            notes.append(f"去掉「{x.get('name')}」：第 {x.get('chain_step')} 步的点由码表按流程放")
    roled = {n for g, _p in jobs for n in g} | {first_n}
    seen = {}
    for x in [x for x in keep if x.get("line_of") in ("start", "main") and x.get("file")]:
        key = (x.get("file"), x.get("line"), x.get("where") or "before", structure._kind(x))
        y = seen.get(key)
        if y is None:
            seen[key] = x
            continue
        drop = x if x.get("chain_step") not in roled else (y if y.get("chain_step") not in roled else None)
        if drop is None:
            continue
        keep.remove(drop)
        seen[key] = x if drop is y else y
        notes.append(f"去掉「{drop.get('name')}」：和「{seen[key].get('name')}」插在同一处、记的是同一次，两点时间一样")
    order = {"first": 0, "once": 1, "last": 2}
    main = iter(sorted((x for x in keep if x.get("line_of") in ("start", "main")),
                       key=lambda x: (x.get("chain_step") if isinstance(x.get("chain_step"), int) else 10 ** 6, order[structure._kind(x)])))
    return [next(main) if x.get("line_of") in ("start", "main") else x for x in keep], notes  # 顺带那条线的点留在原来的位置


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
              **({"felt": s["felt"], "kind": s.get("kind") or ""} if s.get("felt") else {}),
              **({"whole": True} if s.get("every_why") else {}),
              **({"follow": True} if s.get("follow") else {})} for s in r["steps"]]  # follow：跟着起点记（见 follow_start_points）  # whole：一轮里报好几次，取最后那次（全部放完）
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

    cands = []
    if flow is None:
        try:  # 码表先在源码里找各关键角色的候选行（不调用大模型），模型从里面挑
            cands = cands_mod.find(root, category)
        except Exception:
            cands = []
        report()
        try:
            chain_reply = llm.investigate(build_chain_prompt(what, chip, category, cands), root, on_text=on_text, on_tool=on_tool)
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
        fixes = []
        got, bad = check_chain(root, _json_block(chain_reply), category, fixes, fresh=True, cands=cands)
        soft = [f for f in fixes if f.get("soft")]  # 码表自己找到了更准的那一行：问一次，不挡
        hard = [f for f in fixes if not f.get("soft")]
        if ((bad and len(hard) == len(bad)) or (not bad and soft)) and not any(x["stage"] == STAGES["chain"] for x in followups):
            # 只差抄错的原文、首包末包标了多步这类码表能指出改法的：同一段对话里追问一次，只改这几处（每一步最多追问一次）
            t = time.time()
            try:
                again = llm.followup(FIX_AGAIN.format(items="\n".join(f"- {x['text']}" for x in fixes)))
            except LLMError as e:
                return failed(e, "chain")
            took_retries("chain")
            if again is not None:
                fixes2 = []
                got2, bad2 = check_chain(root, _json_block(again), category, fixes2, fresh=True, cands=cands)
                soft2 = [f["text"] for f in fixes2 if f.get("soft")]
                followups.append({"stage": STAGES["chain"], "kind": "fix", "ok": not bad2 and not soft2, "problems": bad + [f["text"] for f in soft],
                                  "problems_after": bad2 + soft2, "seconds": round(time.time() - t, 1)})
                print(f"[识别] 理清流程有 {len(bad) + len(soft)} 处可指出改法的问题，追问一次只改这几处：{'好了' if not bad2 and not soft2 else '还是不对'}",
                      file=sys.stderr, flush=True)
                if (len(bad2), len(soft2)) < (len(bad), len(soft)):  # 问题变少才用改过的那份（码表能自己改的少了也算）
                    chain_first = chain_first or chain_reply
                    chain_reply, got, bad = again, got2, bad2
        if bad:  # 不合格就停下说明原因，不自动重试
            more = f"等 {len(bad)} 处" if len(bad) > 3 else ""
            return _fail("bad_chain", f"理清流程这一步的结果没通过码表检查：{'；'.join(bad[:3])}{more}。点\"重试\"再理一次，也可以到设置页换一个大模型。",
                         found=[], stage="chain", problems=bad, chain_reply=chain_reply, retries=retries, followups=followups,
                         **({"chain_reply_first": chain_first} if chain_first else {}),
                         seconds=round(time.time() - t0, 1), calls=calls)
        flow = check_chain(root, _json_block(chain_reply), category, fresh=True, apply=True, cands=cands)[0]  # 还对不上的那几行由码表改
        for x in flow.get("repairs") or []:
            print(f"[识别] 码表补上：{x}", file=sys.stderr, flush=True)
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
    pick_fixes = structure.pick_fixes(root, r.get("steps") or [], flow["chain"]) if r.get("ok") else None
    if pick_fixes and not any(x["stage"] == STAGES["pick"] for x in followups):
        # 选点只差插在判断或循环开头、标了输出的那一步缺 first 或 last：同一段对话追问一次，只改这几个点（不读源码）
        t = time.time()
        try:
            again = llm.followup(PICK_FIX_AGAIN.format(items="\n".join(f"- {x}" for x in pick_fixes)))
        except LLMError as e:
            return failed(e, "pick")
        took_retries("pick")
        if again is not None:
            r2 = parse(again, root, chain=flow["chain"], strict=True)
            before = sum(1 for x in r["steps"] for c in x["chk"] if c["level"] == "fail")
            after = sum(1 for x in r2.get("steps") or [] for c in x["chk"] if c["level"] == "fail") if r2.get("ok") else None
            ok = after == 0
            followups.append({"stage": STAGES["pick"], "kind": "fix", "ok": ok, "problems": pick_fixes, "fails_before": before,
                              "fails_after": after, "seconds": round(time.time() - t, 1)})
            print(f"[识别] 选计时点有 {len(pick_fixes)} 个点能指出改法，追问一次只改这几个点：{'好了' if ok else '还是不对'}", file=sys.stderr, flush=True)
            if after is not None and after < before:  # 改好了或者少了，用新的；更糟就留原来那份
                reply_first = reply_first or reply
                r, reply = r2, again
    r.update(seconds=round(time.time() - t0, 1), reply=reply, flow=flow, calls=calls, stage="pick", chain_category=category or "",
             retries=retries, followups=followups, candidates=cands)
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


def _older_ok(root, r):
    """源码改过（指纹变了）以后，上一版源码的识别结果还能不能原样用：只认两步识别的新结果，并且
    - 理清的流程按现在的源码重新核对没有问题；
    - 流程里每一步、每个计时点那一行的代码和当时一模一样；
    - 码表在现在的源码里找到的候选和当时一样（候选是从整份源码里按结构找的：相关的代码变了，候选就会变）。
    有一样对不上返回原因，都对得上返回 None。"""
    if not (r.get("ok") and r.get("strict") is True and r.get("reply") and isinstance(r.get("chain"), list) and isinstance(r.get("flow"), dict)):
        return "不是两步识别的结果"
    try:
        _flow, bad = check_chain(root, json.loads(json.dumps(r["flow"])))
    except Exception as e:
        return f"流程核对出错（{type(e).__name__}）"
    if bad:
        return "流程对不上现在的源码：" + "；".join(map(str, bad[:2]))
    for x in list(r["chain"]) + list(r.get("steps") or []):
        if not isinstance(x, dict) or not x.get("file") or not isinstance(x.get("line"), int):
            continue
        ln = x.get("lifted_from") or x["line"]  # 流程里挪到判断前面的一步：存的是原来那一句
        code, problem = check_location(root, x["file"], ln)
        if problem or (x.get("code") and not _same_code(x["code"], code)):
            return f"{x['file']} 第 {ln} 行变了"
    try:
        proj = structure.Project(root)
        now = cands_mod.find(root, r.get("chain_category") or r.get("category") or "")
        saved = [c for c in r.get("candidates") or [] if isinstance(c, dict) and not structure.alt_branch(proj.file(c.get("file") or ""), c.get("line") or 0)]
    except Exception as e:
        return f"候选核对出错（{type(e).__name__}）"
    key = lambda L: [(c.get("role"), c.get("file"), c.get("line"), str(c.get("code") or "").strip()) for c in L]
    if key(now) != key(saved):
        return "码表在源码里找到的候选变了"
    return None


def reuse_older(project_dir, root, what, provider, fingerprint):
    """源码改过（指纹变了）、这一版还没识别过：上一版（项目记下的历史指纹，最新的先）同样要测的、同一个大模型、同一套提示词的结果，
    相关的代码都没变（见 _older_ok）就存成这一版的，不再调用大模型；对不上返回 (None, [原因])。"""
    try:
        hist = json.loads((Path(project_dir) / "project.json").read_text(encoding="utf-8")).get("history") or []
    except (OSError, ValueError, AttributeError):
        hist = []
    why = []
    for fp in reversed([h for h in hist if isinstance(h, str) and h != fingerprint]):
        f = _cache_file(project_dir, fp, what, provider)
        if not f.is_file():
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        bad = _older_ok(root, r)
        if bad:
            why.append(bad)
            continue
        r["reused_from"] = fp
        r["rules"] = None  # 下面按现在的规则重排一遍（不调大模型）
        out = _cache_file(project_dir, fingerprint, what, provider)
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        return out, why
    return None, why


def identify_cached(llm, root, what, project_dir, fingerprint, provider, reuse=True, reuse_flow=None, flow=None, **kw):
    """源码没变、要测的也没变时，直接用上次的识别结果（设置页的勾选），不再调用大模型。
    两次调用分别存：整份结果；理清的流程（reuse_flow，默认跟 reuse 一样；重试时码表传 True，第二次失败不用重跑第一次）。
    flow：调用方手里已有的理清的流程（没有项目目录、存不了文件时，网页重试把上次的带过来）。"""
    f = _cache_file(project_dir, fingerprint, what, provider) if project_dir and fingerprint else None
    older = None
    if reuse and f and not f.exists():  # 源码改过、这一版还没识别过：上一版的结果相关代码都没变就接着用
        try:
            older, _why = reuse_older(project_dir, root, what, provider, fingerprint)
        except Exception:
            older = None
    if reuse and f and f.exists():
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            if r.get("strict") is True and r.get("rules") != RULES_VER and r.get("reply") and isinstance(r.get("chain"), list):
                # 两步识别的结果：点和段是码表按规则放、连的。规则升级后（比如去掉起点前就一直在跑的流水线中间那几步、
                # 全部放完改成每次都记）用存下的大模型回答按现在的规则重排一遍，不再调用大模型
                again = parse(r["reply"], root, chain=r["chain"], strict=True)
                if again.get("ok"):
                    r = {**r, **{k: v for k, v in again.items() if k != "ok"}}
                    try:  # 重排过的存回去（页面数据一起）：码表别处读识别结果（插点时核对结束那一步）拿到的也是新的
                        f.write_text(json.dumps({**r, "page": to_page(r, what)}, ensure_ascii=False, indent=1), encoding="utf-8")
                    except OSError:
                        pass
            # 沿用的结果也按现在的源码重新做一遍结构检查（存下时可能还没有这层检查，或者是改完再测挪过行号的）
            structure.check(root, r.get("steps") or [], r.get("segments"), r.get("side_segments"), r.get("chain"),
                            strict=r.get("strict") is True)  # 旧缓存没有这一项：照旧宽松
            # 页面数据按存下的步骤重新算一遍：码表升级后页面要的新字段（比如"一轮记哪一次"）也有
            return {**r, "page": to_page(r, what), "reused": True, "seconds": 0, "calls": 0,
                    **({"reused_older": r.get("reused_from")} if older else {})}
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
