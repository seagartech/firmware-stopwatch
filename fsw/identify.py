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
import time
from pathlib import Path

from fsw.llm import LLMError

PROMPT = """你是"固件码表"工具里的固件分析助手。用户是不写代码的硬件负责人。

## 用户的要求
固件源码就在当前目录（源码根目录），只能读，不能改。
用户想测：{what}
{chip}
## 你的任务
只读源码，不改任何文件。找出"{what}"这条线在固件里经过的关键步骤，每一步建议一个"计时点"（插一句记录当前时间的语句，不改原有逻辑）。
- 起点：设备最早知道这件事发生的那一刻；终点：用户感受到结果的那一刻。
- 如果读代码时发现另一条用户可能也关心的线（比如设备在开口前先换表情、开门前先亮灯），也列出来，作为"顺带发现的线"。
- 同一件事有"收到"和"真正生效"两个时刻的（比如收到表情指令、表情真正换上），两个都列。
{known}
## 边找边报
每确认一个关键步骤，单独输出一行：找到：<中文名>（<文件>:<行号>）

## 最后输出
最后只输出一个 ```json 代码块，格式如下（中文、大白话，写给不写代码的人看）：
```json
{{
 "summary": "一段话结论：链路有几步，有没有用户需要知道的重要情况（比如某一段不在设备上发生）",
 "start_found": true, "end_found": true,
 "missing": "起点或终点找不到时说清是哪一头、为什么；都找到就空着",
 "start": {{"name": "起点的短名字，6 个字以内，比如：你说完", "note": "起点之前设备看不见的那段，没有就空"}},
 "main": {{"name": "要测的这条线的名字，比如：开口这条", "end": "终点的短名字，6 个字以内，比如：设备开口"}},
 "side": null 或 {{"name": "顺带发现的线，比如：表情这条", "end": "比如：换上表情", "found": "发现了什么，一句话，比如：设备开口说话之前，会先换一个表情", "why": "为什么推荐也测，一句话"}},
 "steps": [
  {{"id": "P1", "name": "中文名，8 个字以内，比如：收到听写文字", "file": "相对源码根目录的路径，比如 main/application.cc", "line": 123,
    "func": "函数名", "where": "before 或 after（插在这行之前还是之后）", "once": "first / last / every（一轮里记第一次、最后一次还是每次）",
    "line_of": "start（起点）/ main（要测的这条）/ side（顺带发现的那条）", "sure": "高 / 中 / 低",
    "why": "为什么放这里（大白话）", "doubt": "拿不准的地方，没有就空"}}
 ],
 "segments": [{{"from": "P1", "to": "P2", "name": "这段在做什么", "who": "固件 / 硬件 / 网络 / 服务器，可以组合", "outside": false}}],
 "side_from": "顺带那条线从哪个计时点分出去，没有就空",
 "side_segments": [{{"from": "P1", "to": "P5", "name": "…", "who": "…", "outside": false}}],
 "outside_note": "设备外那段在做什么，12 个字以内，比如：服务器：听写、想回答；没有就空",
 "doubts": [{{"text": "读代码时发现的可能变慢或出错的地方", "sure": "高 / 中 / 低"}}]
}}
```
steps 按发生的先后顺序排；segments 是要测的这条线从起点到终点的每一段，outside=true 表示这段在设备外面（服务器、网络）。

## 规则
- 每个位置必须是你亲自打开文件核对过的真实行号。
- "收到"类的计时点放在任何条件判断（比如"状态不对就丢掉"）之前，被丢掉的也要能记到。
- "切换完成""生效"类的计时点放在这次切换引起的动作（比如清空缓存、关掉录音）都做完之后。
- 拿不准就标"低"或"中"，并在 doubt 里写为什么，不要编。
- 说明文字里不要写 P1、P2 这样的编号，写步骤的中文名。
- 起点或终点在源码里找不到，就把 start_found / end_found 写 false，并在 missing 里说清楚。
"""

KNOWN = "\n上次读到一半断了，已经找到这些步骤：{names}。请核对它们，然后接着找剩下的，最后照样给出完整结果。\n"
FOUND = re.compile(r"找到[：:]\s*([^\n（(]+)(?:[（(]\s*([^）)\n]+?)\s*[）)])?")
SURE = {"高", "中", "低"}
LINE_OF = {"start": "start", "main": "talk", "side": "face"}


def build_prompt(what, chip="", known=None):
    return PROMPT.format(what=what, chip=f"芯片和板子：{chip}\n" if chip else "",
                         known=KNOWN.format(names="、".join(known)) if known else "")


def found_names(text, with_at=False):
    """从边找边报的文字里数出已找到的步骤名（和"文件:行号"）。"""
    out, at = [], []
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


def parse(text, root):
    """读大模型的回答：取最后的 json，逐个核对位置。"""
    data = _json_block(text)
    if not isinstance(data, dict):
        return _fail("bad_reply", "大模型读完了代码，但回答的格式不对，码表看不懂。点\"重试\"再找一次。", found=found_names(text))
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
                "why": s.get("why") or "", "doubt": s.get("doubt") or "", "code": code, "loc_ok": problem is None}
        if problem:
            step["sure"], step["doubt"] = "低", problem + (("；" + step["doubt"]) if step["doubt"] else "")
        steps.append(step)
    if len(steps) < 2:
        return _fail("bad_reply", "大模型只给出了不到 2 个步骤，量不出用时。点\"重试\"再找一次，或者换个说法写\"测从什么到什么\"。",
                     found=[s["name"] for s in steps])
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
              **({"note": s["doubt"]} if s["doubt"] else {})} for s in r["steps"]]
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
        "server": r.get("outside_note") or "", "mapWhat": what,
        "mapSel": next((s["p"] for s in steps if s["sure"] != "高"), steps[0]["p"]),
        "measure": f"{sname} → {mend}", "mainTitle": f"{mname}：{sname} → {mend}",
        "summary": r.get("summary") or "", "doubts": r.get("doubts") or [], "warn": r.get("warn"), "startNote": start.get("note") or "",
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


def identify(llm, root, what, chip="", known=None, on_progress=None):
    """真识别一次。on_progress 收到 {seconds, found, read, last}；出错时已找到的留着，重试时告诉大模型接着找。"""
    t0, text, state = time.time(), [], {"found": list(known or []), "at": [""] * len(known or []), "read": 0, "last": ""}

    def report():
        on_progress and on_progress({"seconds": round(time.time() - t0, 1), **state,
                                     "found": list(state["found"]), "at": list(state["at"])})

    def on_text(piece):
        text.append(piece)
        for n, a in zip(*found_names("".join(text), with_at=True)):
            if n not in state["found"]:
                state["found"].append(n)
                state["at"].append(a)
        report()

    def on_tool(desc):
        state["read"] += 1
        state["last"] = desc
        report()

    try:
        reply = llm.investigate(build_prompt(what, chip, known), root, on_text=on_text, on_tool=on_tool)
    except LLMError as e:
        return _fail(e.kind, f"{e.message}<br>已找到的 {len(state['found'])} 个留着，点\"重试\"接着找，也可以到设置页换一个大模型。",
                     found=state["found"], seconds=round(time.time() - t0, 1))
    r = parse(reply, root)
    r["seconds"] = round(time.time() - t0, 1)
    r["reply"] = reply
    if r["ok"]:
        r["page"] = to_page(r, what)
        r["found"] = [s["name"] for s in r["steps"]]
    else:
        r["found"] = r.get("found") or state["found"]
    return r


def _cache_file(project_dir, fingerprint, what, provider):
    key = hashlib.sha1(f"{fingerprint}\0{what.strip()}\0{provider}".encode()).hexdigest()[:12]
    return Path(project_dir) / "identify" / f"{key}.json"


def identify_cached(llm, root, what, project_dir, fingerprint, provider, reuse=True, **kw):
    """源码没变、要测的也没变时，直接用上次的识别结果（设置页的勾选），不再调用大模型。"""
    f = _cache_file(project_dir, fingerprint, what, provider) if project_dir and fingerprint else None
    if reuse and f and f.exists():
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            # 页面数据按存下的步骤重新算一遍：码表升级后页面要的新字段（比如"一轮记哪一次"）也有
            return {**r, "page": to_page(r, what), "reused": True, "seconds": 0}
        except (OSError, ValueError):
            pass
    r = identify(llm, root, what, **kw)
    if r["ok"] and f:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({**r, "what": what, "provider": provider}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**r, "reused": False}


SUGGEST = """你是"固件码表"工具里的助手。下面是一个单片机固件项目的概况。请推荐 3 个用户最可能想测的"从什么到什么"（用户感受得到的一段等待），
每个不超过 15 个字，写成"从……到……"，按推荐程度排序。只输出一个 JSON 数组，比如 ["从说完话到开口", "从按键到屏幕亮", "从开机到连上网"]。

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
