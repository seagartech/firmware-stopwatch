"""为什么、怎么改：问题从数据算出（任务 16 的标签，不由 AI 定），文字由 AI 读代码和这次的数据写。

每个要改的问题一张卡片：为什么慢（大白话）、对用户的影响、2～3 个方案（改法、改动大小、风险、效果（估算）、代价、
工作量大 / 中 / 小和依据、找谁、是不是建议），最后一句建议；技术细节（数据、原因、改法、大小、风险、怎么验证）；另写"不用改的"。
AI 只读源码（只读工具），码表只给建议，不改代码。回来的内容码表逐项核对，不对就整份不用；原话存一份。
"""

import json
import re
import time
from pathlib import Path

from fsw.identify import _json_block
from fsw.llm import LLMError

WORK = {"大", "中", "小", "不确定"}
OPT_FIELDS = {"name": "方案名", "how": "改法", "size": "改动大小", "rk": "风险", "effect": "效果", "cost": "代价",
              "work": "工作量", "wn": "工作量的依据", "who": "找谁"}
DEV_FIELDS = ("data", "cause", "change", "size", "rk", "verify")


def problems(j, scenario):
    """要写卡片的问题：任务 16 判成"必须改""建议改"的，按标签的先后。key 和网页"要你拍板"里的一样。"""
    main = j["lines"][-1]["name"] if j["lines"] else None
    spiky = next((x["segment"] for x in j["labels"] if x["kind"] == "spiky" and not x.get("with")), None)
    merged = {}  # 同一个根因合并进来的"偶发特别慢"：问题 → [(段, 那几轮)]
    for x in j["labels"]:
        if x["kind"] == "spiky" and x.get("with"):
            merged.setdefault(x["with"], []).append(x)
    names = {g["k"]: g for g in j["segments"]}
    out = []
    for x in j["labels"]:
        if x["level"] in ("可以不改", "未判"):  # 未判：没判快慢，没有问题可写
            continue
        if x["kind"] == "line":
            key = "main" if x["line"] == main else f"line:{x['line']}"
            seg = j.get("biggest") if key == "main" else None
            if key != "main":
                l = next(l for l in j["lines"] if l["name"] == x["line"])
                segs = [g for g in j["segments"] if g["k"] in l["segments"]]
                seg = max(segs, key=lambda g: g["med"])["k"] if segs else None
            out.append({"key": key, "seg": seg, "level": x["level"], "what": f"{x['line']}：{x['why']}"})
        elif x["kind"] == "spiky":
            if x.get("with"):
                continue
            out.append({"key": x["k"], "seg": x["segment"], "level": x["level"], "rounds": x["rounds"],
                        "what": f"「{scenario['segments'][x['segment']]['name']}」偶尔特别慢（第 {'、'.join(map(str, x['rounds']))} 轮）"})
        elif x.get("order"):
            out.append({"key": "reversed", "seg": spiky, "level": x["level"], "rounds": x["rounds"],
                        "what": f"{x['name']}（第 {'、'.join(map(str, x['rounds']))} 轮）"})
        else:
            rule = next((p["rule"] for p in scenario.get("problems") or [] if f"problem:{p['key']}" == x["k"]), {})
            also = "".join(f"；同一轮「{scenario['segments'][m['segment']]['name']}」也特别慢"
                           f"（最慢 {names[m['segment']]['max']:.2f} 秒，平时 {names[m['segment']]['med']:.2f} 秒），是同一个问题"
                           for m in merged.get(x["k"], []) if m["segment"] in names and m["segment"] != rule.get("segment"))
            seg = rule.get("segment") or next((m["segment"] for m in merged.get(x["k"], [])), None)
            out.append({"key": x["k"], "seg": seg, "level": x["level"], "rounds": x["rounds"],
                        "what": f"{x['name']}（第 {'、'.join(map(str, x['rounds']))} 轮）{also}"})
    return out


PROMPT = """你是"固件码表"工具里的改进建议助手。固件源码就在当前目录，只能读，不能改；码表只给建议，不改任何文件。
用户是不写代码的硬件负责人，和他手下的固件研发、服务器方等。

## 这次测的是
{what}
{lines}

## 每一段（多轮实测 {n} 轮，单位秒；"设备外"表示在服务器或网络上，设备看不见里面）
{segs}

## 计时点在代码里的位置
{points}

## 码表按固定规则算出来要处理的问题（不是你判断的，照着写）
{probs}

## 你的任务
读相关代码（看计时点附近、这几段经过的代码），给上面每个问题写一张卡片；同一个原因的几个问题可以合成一张（when 里写几个编号）。
when 里只写这张卡片真正讨论的问题编号：没在 why 里讲到的问题不要写进来，每个"偶尔特别慢"的段是单独一个编号；
写进 when 的问题，要在 why 或 dev.data 里写出它是第几轮（比如"第 1、6、16 轮"），码表按这个核对。
- why：为什么慢（大白话，带上数据），对设备外的段只说码表看到了什么、看不到什么，不要编服务器里面的原因。
- impact：对用户的影响。
- opts：2～3 个方案。每个方案写 name、how（具体改法，固件改动写到文件和行号附近）、size（改动大小，比如"约 20 行，只加不改"）、
  rk（风险）、effect（效果，一律是估算，写"约多少秒"）、cost（代价）、work（工作量，只能写 大、中、小、不确定 之一）、
  wn（工作量的依据，一句话）、who（找谁：固件研发 / 服务器方 / 后端研发 / 产品 等）、rec（建议做的写 1，不建议就不写）。
- rec：最后一句建议。
- dev：给研发看的技术细节：data（相关数据）、cause（原因）、change（改法）、size（改动大小）、rk（风险）、verify（改完怎么用码表验证）。
- 另外写一句 fine："不用改的"是哪些（设备自己哪几段正常）。
中文、大白话。拿不准的写"推断"，不要编。why 写 2～4 句。
说明文字里写段和计时点的中文名（比如"服务器想回答"），不要写 P2-P5 这样的编号；代码位置写文件名和行号。
why、impact、rec、方案的 name / effect / cost / wn 是给负责人看的：不能写文件名、行号、函数名、变量名；代码位置只写在 how 和 dev 里。

## 最后输出
只输出一个 ```json 代码块：
```json
{{"fixes": [{{"when": ["问题编号"], "seg": "主要涉及的段编号（seg 只写一段；讲了几段就写最主要的那段）", "why": "…", "impact": "…",
  "opts": [{{"name": "…", "how": "…", "size": "…", "rk": "…", "effect": "约 … 秒（估算）", "cost": "…", "work": "小", "wn": "…", "who": "固件研发", "rec": 1}}],
  "rec": "…", "dev": {{"data": "…", "cause": "…", "change": "…", "size": "…", "rk": "…", "verify": "…"}}}}],
 "fine": "…"}}
```
"""


def _fmt_vs(x):
    """和上次比的一句（第一次测没有上次）。"""
    vs = x.get("vs")
    if not vs:
        return "第一次测，没有上次可比"
    if vs["change"] == "持平":
        return f"上次 {vs['prev']:.3f}，和上次持平"
    return f"上次 {vs['prev']:.3f}，比上次{vs['change']} {abs(vs['d']):.3f}"


def build_prompt(j, scenario, steps=None, what=""):
    seg_lines = []
    for g in j["segments"]:
        sd = scenario["segments"][g["k"]]
        seg_lines.append(f"- {g['k']}：{g['name']}（{sd['from']}→{sd['to']}，{g['owner']}{'，设备外' if g['outside'] else ''}）"
                         f"一般 {g['med']:.3f}，10 次最慢 {g['p90']:.3f}，最慢 {g['max']:.3f}；{_fmt_vs(g)}"
                         f"{'（偶发特别慢）' if g['spiky'] else ''}")
    line_txt = "\n".join(f"- 线「{l['name']}」：一般 {l['med']:.2f} 秒，10 次最慢 {l['p90']:.2f}，最慢 {l['max']:.2f}；{_fmt_vs(l)}"
                         f"{'；体验标准判' + l['exp'] if l['exp'] else ''}" for l in j["lines"])
    pts = "\n".join(f"- {s['p']} {s['n']}：{s.get('path') or s.get('f')} 第 {s['l']} 行" for s in steps or []) or "（没有）"
    probs = "\n".join(f"- 编号 {p['key']}（{p['level']}）：{p['what']}" + (f"；主要是段 {p['seg']}" if p.get("seg") else "")
                      for p in problems(j, scenario))
    return PROMPT.format(what=what or scenario.get("what") or scenario.get("name", ""), lines=line_txt, n=j["n"],
                         segs="\n".join(seg_lines), points=pts, probs=probs)


def _fail(kind, message, **extra):
    return {"ok": False, "kind": kind, "message": message, **extra}


def _norm_seg(f, scenario):
    """一张卡片讲了几段（AI 写成"P1-P2, P2-P3"）：每段都对得上就收下，段记第一个，全部另存在 segs（决策 119）。"""
    seg = f.get("seg")
    if isinstance(seg, list):
        seg = ", ".join(map(str, seg))
    if isinstance(seg, str) and seg not in scenario["segments"]:
        parts = [x.strip() for x in re.split(r"[,，、/;；\s]+", seg) if x.strip()]
        if len(parts) > 1 and all(x in scenario["segments"] for x in parts):
            f["seg"], f["segs"] = parts[0], parts


def check(data, j, scenario):
    """逐项核对 AI 的卡片；返回 (卡片, 问题列表)。"""
    if not isinstance(data, dict) or not isinstance(data.get("fixes"), list):
        return None, ["格式不对，码表看不懂"]
    probs = problems(j, scenario)
    keys = {p["key"] for p in probs}
    bad, fixes = [], []
    for i, f in enumerate(data["fixes"], 1):
        where = f"第 {i} 张卡片"
        if not isinstance(f, dict):
            bad.append(f"{where}格式不对")
            continue
        when = f.get("when") or []
        if not when or any(k not in keys for k in when):
            bad.append(f"{where}的问题编号对不上：{when}")
        _norm_seg(f, scenario)
        if f.get("seg") is not None and f.get("seg") not in scenario["segments"]:
            bad.append(f"{where}说的段没有这一段：{f.get('seg')}")
        for k, name in (("why", "为什么"), ("impact", "对用户的影响"), ("rec", "建议")):
            if not str(f.get(k) or "").strip():
                bad.append(f"{where}没写{name}")
        opts = f.get("opts")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 3:
            bad.append(f"{where}要写 2～3 个方案")
            opts = opts if isinstance(opts, list) else []
        for oi, o in enumerate(opts, 1):
            for k, name in OPT_FIELDS.items():
                if not str((o or {}).get(k) or "").strip():
                    bad.append(f"{where}方案 {oi} 没写{name}")
            if (o or {}).get("work") and o["work"] not in WORK:
                bad.append(f"{where}方案 {oi} 的工作量只能写大、中、小、不确定")
        for k in MANAGER_FIELDS:
            if (hit := _code_in(f.get(k))):
                bad.append(f"{where}给负责人看的文字里写了代码（{hit}），代码位置只能写在改法和技术细节里")
        for oi, o in enumerate(opts, 1):
            for k in MANAGER_OPT_FIELDS:
                if isinstance(o, dict) and (hit := _code_in(o.get(k))):
                    bad.append(f"{where}方案 {oi} 给负责人看的文字里写了代码（{hit}）")
        dev = f.get("dev")
        if not isinstance(dev, dict) or any(not str(dev.get(k) or "").strip() for k in DEV_FIELDS):
            bad.append(f"{where}的技术细节（数据、原因、改法、大小、风险、怎么验证）没写全")
        if opts and not any(o.get("rec") for o in opts if isinstance(o, dict)):
            opts[0]["rec"] = 1  # 一个都没标建议：把第一个当建议（默认勾上）
        fixes.append(f)
    rounds = {p["key"]: p.get("rounds") or [] for p in probs}
    for i, f in enumerate(fixes, 1):
        if not isinstance(f, dict):
            continue
        dev = f.get("dev") if isinstance(f.get("dev"), dict) else {}
        said = _rounds_said(" ".join(str(x or "") for x in (f.get("why"), f.get("impact"), dev.get("data"), dev.get("cause"))))
        for k in f.get("when") or []:
            miss = [n for n in rounds.get(k, []) if n not in said]
            if miss:
                bad.append(f"第 {i} 张卡片挂了「{next(p['what'] for p in probs if p['key'] == k)}」，但没写这个问题是第几轮"
                           f"（缺第 {'、'.join(map(str, miss))} 轮），可能不是在讲它")
    covered = {k for f in fixes if isinstance(f, dict) for k in (f.get("when") or [])}
    segs = {f.get("seg") for f in fixes if isinstance(f, dict)}
    missing = [p["what"] for p in probs if p["key"] not in covered and not (p.get("seg") and p["seg"] in segs)]
    if missing:
        bad.append("这些问题没写卡片：" + "；".join(missing))
    return fixes, bad


MANAGER_FIELDS = ("why", "impact", "rec")
MANAGER_OPT_FIELDS = ("name", "effect", "cost", "wn")
CODE_RE = re.compile(r"[\w/.-]+\.(?:c|cc|cpp|cxx|h|hpp|py|js)\b|第\s*\d+\s*行|[A-Za-z_]\w*\(\)|\b[a-z]+[A-Z]\w*|\b[A-Za-z]+_\w+")


def _code_in(text):
    """负责人看的文字里有没有代码（文件名、行号、函数名、变量名）。"""
    m = CODE_RE.search(str(text or ""))
    return m.group(0) if m else None


def _rounds_said(text):
    """文字里写到的轮次：认"第 1、6、16 轮""第 4 轮和第 11 轮""第 1～3 轮"这类写法。"""
    out = set()
    for m in re.finditer(r"第\s*([0-9０-９][0-9０-９、,，\s和及～~\-—至到]*)\s*轮", text):
        part = m.group(1)
        for a, b in re.findall(r"(\d+)\s*[～~\-—至到]\s*(\d+)", part):
            out.update(range(int(a), int(b) + 1))
        out.update(int(x) for x in re.findall(r"\d+", part))
    return out


def signature(j, scenario):
    """要写卡片的问题：哪几个（哪条线、哪一段、哪种问题）、什么级别。和上次写建议时一样，就沿用上次的，不再调用大模型。
    不看具体是哪几轮、几秒：改完再测时这些每次都不一样，问题本身没变（10-17 用户定）。"""
    return json.dumps([[p["key"], p.get("seg"), p["level"]] for p in problems(j, scenario)], ensure_ascii=False)


def saved_for(save_to, j, scenario):
    """上次存下的建议，且是按同样的问题写的：直接用（比如只是重开了报告，要改的问题没变）。"""
    try:
        out = json.loads((Path(save_to) / "advice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return out if out.get("ok") and out.get("for") == signature(j, scenario) else None


def advise(llm, root, j, scenario, steps=None, what="", save_to=None, on_progress=None):
    """调用一次大模型（只读）写卡片。没有要改的问题就不调用；要改的问题和上次存下的一样，用上次的。"""
    if not problems(j, scenario):
        return {"ok": True, "fixes": [], "fine": "", "seconds": 0, "called": False}
    if save_to and (old := saved_for(save_to, j, scenario)):
        return {**old, "seconds": 0, "called": False}
    t0 = time.time()
    save = Path(save_to) if save_to else None
    if save:
        save.mkdir(parents=True, exist_ok=True)
    try:
        text = llm.investigate(build_prompt(j, scenario, steps, what), root,
                               on_tool=lambda d: on_progress and on_progress({"read": d}))
    except LLMError as e:
        return _fail(e.kind, f"{e.message}<br>点\"重试\"再写一次。", seconds=round(time.time() - t0, 1))
    if save:
        (save / "reply.txt").write_text(text, encoding="utf-8")  # 原话留一份，出错时好查
    data = _json_block(text)
    fixes, bad = check(data, j, scenario)
    secs = round(time.time() - t0, 1)
    if bad:
        return _fail("bad_reply", "AI 写的建议格式不对，码表没有用：" + "；".join(bad[:4]) + "。点\"重试\"再写一次。", seconds=secs)
    out = {"ok": True, "fixes": fixes, "fine": str(data.get("fine") or ""), "seconds": secs, "called": True,
           "for": signature(j, scenario), "at": time.strftime("%Y-%m-%d %H:%M")}
    if save:
        (save / "advice.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
