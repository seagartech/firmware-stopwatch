"""为什么、怎么改：问题从数据算出（任务 16 的标签，不由 AI 定），文字由 AI 读代码和这次的数据写。

每个要改的问题一张卡片：为什么慢（大白话）、对用户的影响、1～3 个有效方案（改法、改动大小、风险、效果（估算）、代价、
工作量大 / 中 / 小和依据、找谁、是不是建议），最后一句建议；技术细节（数据、原因、改法、大小、风险、怎么验证）；另写"不用改的"。
AI 只读源码（只读工具），码表只给建议，不改代码。回来的内容码表逐项核对，不对就整份不用；原话存一份。
"""

import json
import hashlib
import math
import os
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
    j = {**j, "scenario": scenario}
    # 主线和报告一样：先去掉看回答长短的线（报告不画、不判），剩下的最后一条（10-20 修：两边认的主线不同，卡片挂不上）
    keep = [l for l in j["lines"] if l.get("verdict") != "看回答长短"] or j["lines"]
    primary = next((l for l in scenario.get("lines", []) if l.get("first")), None) or next((l for l in scenario.get("lines", []) if l.get("measure") and any(x["name"] == l["name"] for x in keep)), None)
    main = primary["name"] if primary else keep[-1]["name"] if keep else None
    spiky = next((x["segment"] for x in j["labels"] if x["kind"] == "spiky" and not x.get("with")), None)
    merged = {}  # 同一个根因合并进来的"偶发特别慢"：问题 → [(段, 那几轮)]
    for x in j["labels"]:
        if x["kind"] == "spiky" and x.get("with"):
            merged.setdefault(x["with"], []).append(x)
    names = {g["k"]: g for g in j["segments"]}
    out = []
    for x in j["labels"]:
        if x["level"] in ("达标", "未判"):  # 未判：没判快慢，没有问题可写
            continue
        if x["kind"] == "line":
            key = "main" if x["line"] == main else f"line:{x['line']}"
            l = next(l for l in j["lines"] if l["name"] == x["line"])  # 大头的一段：这条线自己里中位数最大的
            segs = [g for g in j["segments"] if g["k"] in l["segments"]]
            seg = max(segs, key=lambda g: observed(j, "segment", g["k"])["average"] or 0)["k"] if segs else None
            rs = lambda k: "、".join(map(str, l.get(k) or []))
            slow = (f"；超过标准的是第 {rs('over_rounds')} 轮" if l.get("over_rounds") else "") + \
                   (f"，其中超过标准 1.5 倍的是第 {rs('big_rounds')} 轮" if l.get("big_rounds") else "")
            rc = {c["n"]: c for c in l.get("round_cause") or []}
            cause = "；".join(f"第 {n} 轮多在「{rc[n]['name']}」（{rc[n]['v']:.2f} 秒，一般 {rc[n]['med']:.2f} 秒）"
                             for n in l.get("over_rounds") or [] if n in rc and rc[n]["more"] >= 0.1)
            slow += f"；超标那几轮各自多出来的时间（码表算的，照着写）：{cause}" if cause else ""
            vs = l.get("vs")  # 整条线和上次比（某一段的上次不成立时，这条线的对比还在）
            was = f"；这条线上次一般 {vs['prev']:.2f} 秒，这次 {l['med']:.2f} 秒" if vs and vs["change"] != "持平" else ""
            out.append({"key": key, "seg": seg, "level": x["level"], "what": f"{x['line']}：{x['why']}{slow}{was}"})
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
                           f"（最慢 {names[m['segment']]['max']:.2f} 秒，平时 {names[m['segment']]['med']:.2f} 秒），两者时间区间可能重叠，未证明同一原因"
                           for m in merged.get(x["k"], []) if m["segment"] in names and m["segment"] != rule.get("segment"))
            seg = rule.get("segment") or next((m["segment"] for m in merged.get(x["k"], [])), None)
            out.append({"key": x["k"], "seg": seg, "level": x["level"], "rounds": x["rounds"],
                        "what": f"{x['name']}（第 {'、'.join(map(str, x['rounds']))} 轮）{also}"})
    return out


PROMPT = """你是通用固件测量工具的建议助手，只读源码，不改任何文件。适用于刷卡、按键、语音等任意固件，不按设备品牌写规则。
测量用途：{what}

## 完整输出约束
每张卡片必须同时含when、seg、analysis_kind、evidence_refs、why、impact、opts、rec、done、dev，dev必须含data/cause/change/size/rk/verify，不能遗漏。when取问题key，不是段编号或线路中文名。rec在卡片级是下一步字符串，在opts项内才是1/0。

## 程序核对的证据（JSON，秒数均为真实记录的算术平均；中位数只作明确标注的补充）
{lines}
{segs}
计时位置及原样源码：{points}
需处理的问题：{probs}

## 规则
问题等级是报告内部处理优先级，不等于国标认证，也不证明根因。参考来源若要求平均值，只比较平均值；逐次超过参考线及极端值比例是独立的内部波动规则。
所有数值和轮次由上面的JSON提供，网页会直接展示，why/impact/rec不要重复秒数、轮次清单，轮次证据由程序自动附上。源码只允许引用提供摘录里确实出现的消息类型、函数和字段；没有看到服务端序号、会话标识、Run或调度队列实现时，不能假定存在。不能把分段中位数相加、不能把耗时最长当根因。尚未看到实际执行位置时，不得宣称两端处于不同线程或执行顺序不固定。设备外区间可能包括网络与本地异步调度，没有日志/源码证据不能断言服务端故障。
模拟数据只能检验规则与展示，人工延迟不能证明代码缺陷；不得要求外部调查不存在的历史请求。
why最多两句，写已知现象或有引用依据的原因；impact一句，写对使用者的实际影响，不能拿“缺少观测”冒充用户影响。
每个问题一张卡片，可合并确有共同处理依据的问题；seg 只写一段（主要涉及的段）；when只能填真正讨论、实际覆盖的编号，不因相同秒数断言同一原因。
opts允许1～3个有效方案，默认只给一个，不凑数。只有存在实质取舍时列备选；互相依赖的步骤写在一个how内；“如第一项结果怎样再做”的后续步骤必须并入第一项，不是备选方案。定位时只观察已经存在的源码边界，不复述未知的协议分支。每张卡片必须由你明确推荐一个rec=1，程序不替你默认选择。
analysis_kind只能是investigate（先定位原因）或change（有源码依据的修改思路，尚未验证）。change必须给evidence_refs，引用提供的计时位置编号；不是证明修复有效。
定位方案必须写具体新增哪个边界、需要什么观测、怎样区分可能性；不能只说“对比正常数据”。观察点不与现有计时位置重复。每个定位方案必须用observations选择程序提供的未测位置候选ID，例如[{{"candidate":"N1"}}]；只能选择available_observations中现有的ID，不能自行填写at、point或relation。程序会填入真实文件、行号和前后关系。how中的调用前/后必须与observations完全一致，已有时间戳已足够的执行处直接复用。新增位置不能与point_boundaries已有at位置重复；已有点直接复用，不再加相同日志。
每个方案的anchors填实际引用的源码片段，例如[{{"point":"某计时位置编号","code":"摘录中连续出现的一小段原文"}}]；code必须逐字存在于该位置ctx内，新增日志可以描述为计划，但不把计划写成已有代码。若how使用新增变量，new_symbols列出这些新变量。不得引用摘录不存在的消息类型、状态名、主循环分支。
方案name用不超过24个汉字的动作短名，who另写负责人。具体代码位置只由anchors和observations提供，网页会显示文件、行号、调用前后及原文。how只写怎样按轮次对齐、怎样区分结果，不再复写代码位置、函数或变量名；dev.change也只写处理目标，不再重复新增位置。已有执行时间戳直接复用，不能在同一执行处另加日志。why/impact/rec、name/effect/cost/wn不写文件、行号、函数、变量，技术信息放how/dev。尤其wn只写“涉及几个边界、需哪些配合”，绝不能出现文件名。
effect写这个方案能确认什么或改变什么；无实测不承诺提速、不编收益秒数。done与dev.verify必须同一验收目标；定位方案验收记录完整与边界可区分，不要求加日志就提速。跨端未同步时钟不能直接相减。
效果可以写有依据的估算，未测不得承诺。工作量只能写大、中、小、不确定；小是一处局部边界或配置，跨多个计时位置或多方配合至少为中，wn一句具体依据；size/rk不知道写“—”，不编代码行数。
rec只写推荐的下一步一句；ask是发给who的简短请求，非固件接收方不带源码和内部编号。done不能包含代码。dev.data解释源码与观测的关系，不重复平均值或秒数，数值由程序直接附上。dev保留完整技术信息，不能写“与done同一目标”等字段引用。
没有需要处理的问题不生成卡片；不得为展示类别制造问题。不用改的说明可写fine，不据此宣布全部正常。

输出合法JSON对象或一个```json代码块，不添加其他说明：
{{"fixes":[{{"when":["问题编号"],"seg":"涉及段编号","analysis_kind":"investigate","evidence_refs":[],"why":"现象或有依据的解释","impact":"实际影响","opts":[{{"name":"短方案名","anchors":[{{"point":"位置编号","code":"原文片段"}}],"new_symbols":[],"observations":[{{"candidate":"未测位置ID"}}],"how":"具体做法","size":"—","rk":"—","effect":"可确认的结果或有依据的收益","cost":"代价","work":"小","wn":"工作量依据","who":"固件研发","rec":1,"ask":"请求"}}],"rec":"下一步","done":"验收目标","dev":{{"data":"分析依据，不重复整表","cause":"已知与未知原因","change":"具体改法","size":"—","rk":"—","verify":"验收办法"}}}}],"fine":""}}
"""


def _fmt_vs(x):
    """和上次比的一句（第一次测没有上次）。"""
    vs = x.get("vs")
    if not vs:
        return "第一次测，没有上次可比"
    if vs["change"] == "持平":
        return f"上次 {vs['prev']:.3f}，和上次持平"
    return f"上次 {vs['prev']:.3f}，比上次{vs['change']} {abs(vs['d']):.3f}"


def observed(j, kind, key):
    """One numeric source for prompts and cards. Never relabel stored medians as averages."""
    if kind == "line":
        i = next((i for i, l in enumerate(j.get("scenario", {}).get("lines", [])) if l["name"] == key), None)
        vals = [r.get("ends", [])[i] for r in j.get("rounds", []) if i is not None and i < len(r.get("ends", []))]
    else:
        vals = [r.get("segs", {}).get(key) for r in j.get("rounds", [])]
    if not j.get("rounds"):
        old = next((x for x in j.get("lines" if kind == "line" else "segments", []) if x.get("name" if kind == "line" else "k") == key), {})
        return {"average": old.get("avg"), "count": old.get("count", 0), "total": j.get("n", 0)}
    vals = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0]
    return {"average": sum(vals) / len(vals) if vals else None, "count": len(vals), "total": len(j.get("rounds", []))}


def evidence(j, scenario):
    data = {**j, "scenario": scenario}
    out = {}
    primary = next((l for l in scenario.get("lines", []) if l.get("first")), None) or next((l for l in scenario.get("lines", []) if l.get("measure")), None)
    keep = [l for l in j["lines"] if l.get("verdict") != "看回答长短"] or j["lines"]
    main = primary["name"] if primary else keep[-1]["name"] if keep else None
    for prob in problems(data, scenario):
        line_name = main if prob["key"] == "main" else prob["key"][5:] if prob["key"].startswith("line:") else None
        line = next((l for l in j["lines"] if l["name"] == line_name), None)
        facts = {"priority": prob["level"], "priority_basis": "报告内部处理规则", "segment": prob.get("seg")}
        if line:
            facts.update(observed(data, "line", line_name), line=line_name, over_rounds=line.get("over_rounds", []), reference=line.get("exp_std"), rule=line.get("name") and next((x["why"] for x in j["labels"] if x.get("line") == line_name), ""))
        else:
            facts.update(observed(data, "segment", prob.get("seg")), rounds=prob.get("rounds", []))
        out[prob["key"]] = facts
    return out


def observation_candidates(j, scenario):
    """Untimed source positions, derived from actual problem endpoints, never device names."""
    norm = lambda value: re.sub(r"\s+", "", str(value))
    timed = set()
    for point in (scenario.get("points") or {}).values():
        parts = str(point.get("at") or "").split("|", 2)
        if len(parts) == 3:
            timed.add((parts[0], parts[1], norm(parts[2])))
    relevant = {p for problem in problems(j, scenario) for p in ((scenario.get("segments") or {}).get(problem.get("seg"), {}).get("from"), (scenario.get("segments") or {}).get(problem.get("seg"), {}).get("to")) if p}
    candidates, seen = {}, set()
    for point, code in (j.get("code") or {}).items():
        if relevant and point not in relevant:
            continue
        for line, text in code.get("ctx", []):
            text = str(text).strip()
            if len(text) < 4 or text.startswith(("//", "/*", "*", "#")) or not (text.endswith(";") or (re.match(r"(?:[A-Za-z_]\w*(?:\.|->|::))*[A-Za-z_]\w*\s*\(", text) and not re.match(r"(?:if|while|for|switch|catch)\b", text))):
                continue
            for relation in ("before", "after"):
                key = (code.get("path"), relation, norm(text))
                identity = (code.get("path"), line, relation)
                if key in timed or identity in seen:
                    continue
                seen.add(identity)
                candidates["N" + str(len(candidates) + 1)] = {"point": point, "path": code.get("path"), "line": line, "at": text, "relation": relation}
    return candidates


def build_prompt(j, scenario, steps=None, what=""):
    data = {**j, "scenario": scenario}
    lines = [{"name": l["name"], **observed(data, "line", l["name"]), "median_supplement": l.get("med"), "reference": l.get("exp_std"), "previous_median_supplement": l.get("vs")} for l in j["lines"]]
    segs = [{"key": g["k"], "name": g["name"], **observed(data, "segment", g["k"]), "median_supplement": g.get("med"), "owner": g.get("owner"), "outside": g.get("outside")} for g in j["segments"]]
    return PROMPT.format(what=what or scenario.get("what") or scenario.get("name", ""), lines=json.dumps(lines, ensure_ascii=False), segs=json.dumps(segs, ensure_ascii=False), points=json.dumps({"steps": steps or [], "source": j.get("code") or {}, "point_boundaries": scenario.get("points") or {}, "available_observations": observation_candidates(j, scenario)}, ensure_ascii=False), probs=json.dumps([{"key": key, **facts} for key, facts in evidence(data, scenario).items()], ensure_ascii=False))+"\nwhen只允许从这些问题key中选择："+json.dumps([p["key"] for p in problems(data, scenario)], ensure_ascii=False)+"\n输出前逐项自查完整字段和源码实际消息类型；不确定的边界只能写下一次新增观测，不能编造已有标识或主循环实现。"


def parse_reply(text):
    try:
        return json.loads(text.strip())
    except (ValueError, AttributeError):
        return _json_block(text or "")


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
        if not isinstance(opts, list) or not 1 <= len(opts) <= 3:
            bad.append(f"{where}要写 1～3 个有效方案")
            opts = opts if isinstance(opts, list) else []
        for oi, o in enumerate(opts, 1):
            for k, name in OPT_FIELDS.items():
                if not str((o or {}).get(k) or "").strip():
                    bad.append(f"{where}方案 {oi} 没写{name}")
            if (o or {}).get("work") and o["work"] not in WORK:
                bad.append(f"{where}方案 {oi} 的效果可以写有依据的估算，未测不得承诺。工作量只能写大、中、小、不确定；小是一处局部边界或配置，跨多个计时位置或多方配合至少为中")
        for d, k, name in ((f, "done", "怎么算解决"), *((o, "ask", f"方案 {oi} 的请求") for oi, o in enumerate(opts, 1) if isinstance(o, dict) and "固件" not in str(o.get("who")))):  # 固件研发是自己人，可以写代码位置
            t = str(d.get(k) or "")   # 原样发给对方（可能是别家公司）：不带代码、不用码表的内部说法
            if (hit := _code_in(t) or next((w for w in OUTSIDE_BAN if w in t), None)):
                bad.append(f"{where}{name}会发给对方，不能写「{hit}」")
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
        recs = [o for o in opts if isinstance(o, dict) and o.get("rec")]
        if len(recs) != 1:
            bad.append(f"{where}必须明确推荐一个方案，不能由码表代选")
        kind = f.get("analysis_kind", "legacy")
        if kind not in ("legacy", "investigate", "change"):
            bad.append(f"{where}的建议性质不明确")
        if kind == "change":
            refs = f.get("evidence_refs") or []
            known = set(scenario.get("point_names") or {}) | set(j.get("code") or {}) | set(scenario.get("points") or {})
            if not refs or any(r not in known for r in refs):
                bad.append(f"{where}的修改思路缺少有效源码引用")
        f["facts"] = {k: v for k, v in evidence(j, scenario).items() if k in when}
        if kind in ("investigate", "change"):
            source = j.get("code") or {}
            all_code = "\n".join(str(line) for c in source.values() for _, line in c.get("ctx", []))
            if _code_in((f.get("dev") or {}).get("change", "")):
                bad.append(f"{where}dev.change重复源码位置，具体位置由结构化observations提供，已有执行点直接复用")
            for oi, o in enumerate(opts, 1):
                if _code_in(o.get("how", "")):
                    bad.append(f"{where}方案{oi}how重复源码位置或标识，位置只写在observations，how写对齐与判断方法，不能重复新增已有执行点")
                if source:
                    anchors = o.get("anchors") or []
                    if not anchors or any(not isinstance(a, dict) or a.get("point") not in source or not isinstance(a.get("code"), str) or len(a["code"].strip()) < 4 or a["code"] not in "\n".join(str(line) for _, line in source[a["point"]].get("ctx", [])) for a in anchors):
                        bad.append(f"{where}方案{oi}缺少逐字可核对的源码引用")
                    if oi > 1 and re.search(r"(?:若|如|如果).{0,20}(?:首条|第一|第1|上一|前一)", str(o.get("how", ""))):
                        bad.append(f"{where}方案{oi}依赖第一项结果，应合并为同一方案的后续步骤")
                    if kind == "investigate":
                        observations = o.get("observations") or []
                        if not observations:
                            bad.append(f"{where}方案{oi}未列出新增观测位置observations")
                        norm = lambda text: re.sub(r"\s+", "", str(text))
                        candidates = observation_candidates(j, scenario)
                        resolved = []
                        for item in observations:
                            candidate = item.get("candidate") if isinstance(item, dict) else None
                            if candidate not in candidates:
                                bad.append(f"{where}方案{oi}必须从available_observations选择未测候选，不能自行填写或重复已有位置")
                            else:
                                resolved.append({"candidate": candidate, **candidates[candidate]})
                        o["observations"] = resolved
                        if len({(x["path"], x["line"], x["relation"]) for x in resolved}) > 1 and o.get("work") == "小":
                            o["model_work"], o["model_wn"] = o["work"], o.get("wn")
                            o["work"], o["wn"] = "中", "涉及多处新增观测，按报告工作量规则至少为中"
                        for obs in resolved:
                            p = obs.get("point") if isinstance(obs, dict) else None
                            at = obs.get("at", "") if isinstance(obs, dict) else ""
                            rel = obs.get("relation") if isinstance(obs, dict) else None
                            ctx = "\n".join(str(line) for _, line in source.get(p, {}).get("ctx", []))
                            if p not in source or len(at.strip()) < 4 or at not in ctx or rel not in ("before", "after"):
                                bad.append(f"{where}方案{oi}的新增观测位置不能与源码对齐")
                            else:
                                functions = re.findall(r"([A-Za-z_]\w*)\s*\(", at)
                                if functions:
                                    fn = re.escape(functions[0])
                                    opposite = r"(?:之前|前)" if rel == "after" else r"(?:之后|后)"
                                    if re.search(fn + r".{0,10}" + opposite, str(o.get("how", ""))):
                                        bad.append(f"{where}方案{oi}正文与结构化新增位置的前后关系不一致")
                                original = (scenario.get("points", {}).get(p, {}).get("at") or "").split("|", 2)
                                if len(original) == 3 and original[1] == rel and norm(original[2]) == norm(at):
                                    bad.append(f"{where}方案{oi}新增观测与已有{p}位置重复，应复用已有记录")
                    terms = re.findall(r"\b(?:[A-Z][A-Z0-9]*_[A-Z0-9_]+|[a-zA-Z]\w*_[a-zA-Z0-9_]+|[A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]*)+)\b", str(o.get("how", "")))
                    unknown = [t for t in terms if t not in all_code and t not in (o.get("new_symbols") or [])]
                    if unknown:
                        bad.append(f"{where}方案{oi}引用了未提供的源码符号：{'、'.join(dict.fromkeys(unknown))}")
            # Presentation numbers belong to the program, not a second model-written table.
            if re.search(r"\d+(?:\.\d+)?\s*(?:秒|毫秒)", str((f.get("dev") or {}).get("data", ""))):
                bad.append(f"{where}dev.data重复了时间数值，数据由程序直接展示")
            for key in MANAGER_FIELDS:
                if re.search(r"\d+(?:\.\d+)?\s*(?:秒|毫秒)", str(f.get(key, ""))):
                    bad.append(f"{where}{key}重复了时间数值，数据由程序直接展示")
        for k in MANAGER_FIELDS:
            if "0.00 秒" in str(f.get(k) or ""):
                bad.append(f"{where}写了「0.00 秒」这种没意义的数")
        _tidy(f, opts)
        _named(f, opts, scenario)
        fixes.append(f)
    rounds = {p["key"]: p.get("rounds") or [] for p in probs}
    for i, f in enumerate(fixes, 1):
        if not isinstance(f, dict):
            continue
        dev = f.get("dev") if isinstance(f.get("dev"), dict) else {}
        said = _rounds_said(" ".join(str(x or "") for x in (f.get("why"), f.get("impact"), dev.get("data"), dev.get("cause"))))
        for k in f.get("when") or []:
            if f.get("analysis_kind") in ("investigate", "change"):
                continue
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


def _sec(m):
    """1.164 秒 → 1.2 秒；不到 1 秒的留两位（0.03 秒和 0.08 秒要分得出，刷卡、亮灯这类整条线就几十毫秒）；
    比 0.01 秒还小的写"不到 0.01 秒"（前面的空格一起去掉，免得写成"多 不到"）。"""
    v = float(m.group(2))
    if v < 0.005:
        return "不到 0.01 秒"
    return f"{m.group(1)}{v:.1f} 秒" if v >= 1 else f"{m.group(1)}{v:.2f} 秒"


def _sec_sub(text):
    return re.sub(r"(?<![\d.])(\s?)(\d+\.\d{2,})\s*秒", _sec, text)


def _sec2_sub(text):
    """研发看的（怎么做、技术细节）：秒数留两位，和报告标题一致。"""
    return re.sub(r"(?<![\d.])(\d+\.\d{3,})\s*秒", lambda m: f"{float(m.group(1)):.2f} 秒", text)


def _tidy(f, opts):
    """Keep model text and numerical precision intact; numeric summaries use structured facts."""
    return None


SEG_RE = re.compile(r" ?(?<![A-Za-z0-9_])(P\d+)\s*(?:-|→|–|—|~|～|->)\s*(P\d+)(?![0-9A-Za-z_]) ?")  # 前后的空格一起去掉：中文里「」两边不留空格
PT_RE = re.compile(r" ?(?<![A-Za-z0-9_])(P\d+)(?![0-9A-Za-z_]) ?")


def _named(f, opts, scenario):
    """文字里不出现计时点编号（10-21 用户定：P1→P2 这种谁也看不懂）：段编号换成段名，单个点换成点名。
    AI 被要求写名字，但 dev 里常常还是写编号；这里统一换掉，不靠 AI 自觉。"""
    segs, pts = scenario.get("segments") or {}, scenario.get("point_names") or {}

    def seg(m):
        g = segs.get(f"{m.group(1)}-{m.group(2)}")
        if g:
            return f"「{g['name']}」"
        a, b = pts.get(m.group(1)), pts.get(m.group(2))
        return f"「{a}」到「{b}」" if a and b else m.group(0)

    def sub(t):
        t = SEG_RE.sub(seg, t)
        return PT_RE.sub(lambda m: f"「{pts[m.group(1)]}」" if m.group(1) in pts else m.group(0), t)

    dev = f.get("dev") if isinstance(f.get("dev"), dict) else {}
    for d, ks in ((f, ("why", "impact", "rec", "done")), (dev, DEV_FIELDS),
                  *((o, tuple(OPT_FIELDS) + ("ask",)) for o in opts if isinstance(o, dict))):
        for k in ks:
            if isinstance(d.get(k), str):
                d[k] = sub(d[k])


MANAGER_FIELDS = ("why", "impact", "rec")
OUTSIDE_BAN = ("码表", "达标率", "计时点")
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


# 写法要求改了就加一，旧的建议不再沿用
PROMPT_VER = 19


def source_revision(root, steps=None):
    """Local content digest only; never send paths or source contents through the cache key."""
    base = Path(root).resolve()
    h = hashlib.sha256()
    if not base.is_dir():
        return "unavailable"
    skip = {".git", ".venv", "node_modules", "build", "dist", "advice", "__pycache__", ".idf"}
    suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".py", ".js", ".ts", ".rs", ".ino", ".md", ".yaml", ".yml", ".toml", ".cmake"}
    for folder, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_symlink() or (path.suffix not in suffixes and not name.startswith("sdkconfig") and name not in {"CMakeLists.txt", "Kconfig", "Makefile"}):
                continue
            try:
                h.update(str(path.relative_to(base)).encode()); h.update(b"\0"); h.update(path.read_bytes())
            except OSError:
                return "unavailable"
    return h.hexdigest()


def signature(j, scenario, source=None):
    """Bind advice to exact measurement, rules, code context and source revision."""
    payload = {"version": PROMPT_VER, "scenario": scenario, "rounds": j.get("rounds"), "measured_at": j.get("measured_at"), "measurement_id": j.get("measurement_id"), "measurement_digest": j.get("measurement_digest"), "simulation": j.get("simulation"), "labels": j.get("labels"), "prev": j.get("prev"), "code": j.get("code"), "source": source}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def saved_for(save_to, j, scenario, source=None):
    """上次存下的建议，仅当测量、规则和源码指纹完全相同才复用。"""
    try:
        out = json.loads((Path(save_to) / "advice.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return out if out.get("ok") and out.get("for") == signature(j, scenario, source) else None


def advise(llm, root, j, scenario, steps=None, what="", save_to=None, on_progress=None):
    """调用一次大模型（只读）写卡片。没有要改的问题就不调用；要改的问题和上次存下的一样，用上次的。"""
    if not problems(j, scenario):
        return {"ok": True, "fixes": [], "fine": "", "seconds": 0, "called": False}
    revision = source_revision(root, steps)
    if save_to and revision != "unavailable" and (old := saved_for(save_to, j, scenario, revision)):
        return {**old, "seconds": 0, "called": False}
    t0 = time.time()
    save = Path(save_to) if save_to else None
    if save:
        save.mkdir(parents=True, exist_ok=True)
    investigate = getattr(llm, "investigate_json", llm.investigate)
    try:
        text = investigate(build_prompt(j, scenario, steps, what), root,
                               on_tool=lambda d: on_progress and on_progress({"read": d}))
    except LLMError as e:
        return _fail(e.kind, f"{e.message}<br>点\"重试\"再写一次。", seconds=round(time.time() - t0, 1))
    if save:
        (save / "reply.txt").write_text(text, encoding="utf-8")  # 原话留一份，出错时好查
    data = parse_reply(text)
    fixes, bad = check(data, j, scenario)
    # One bounded repair with concrete validation errors; never silently edit model advice.
    current = isinstance(data, dict) and any(isinstance(f, dict) and f.get("analysis_kind") in ("investigate", "change") for f in data.get("fixes", []))
    if bad and (current or data is None):
        if save:
            (save / "reply-initial.txt").write_text(text, encoding="utf-8")
            (save / "validation-initial.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        feedback = "\n你上一份回答未通过程序核对：" + json.dumps(bad, ensure_ascii=False) + "\n只允许修正一次。不要再猜未提供的消息类型或回调函数名，不把源码里未见到的符号当作已有；直接引用已提供的文件行号和anchors中的原文即可。没有证据时只写边界观测，不下服务端根因结论。按完整JSON格式返回，不省略任何字段。上一份回答：\n" + text
        try:
            text = investigate(build_prompt(j, scenario, steps, what) + feedback, root,
                                   on_tool=lambda d: on_progress and on_progress({"read": d}))
        except LLMError as e:
            return _fail(e.kind, e.message, seconds=round(time.time() - t0, 1))
        if save:
            (save / "reply.txt").write_text(text, encoding="utf-8")
        data = parse_reply(text)
        fixes, bad = check(data, j, scenario)
    secs = round(time.time() - t0, 1)
    if bad:
        return _fail("bad_reply", "AI 写的建议格式不对，码表没有用：" + "；".join(bad[:4]) + "。点\"重试\"再写一次。", seconds=secs)
    out = {"ok": True, "fixes": fixes, "fine": str(data.get("fine") or ""), "seconds": secs, "called": True,
           "for": signature(j, scenario, revision), "source_revision": revision, "at": time.strftime("%Y-%m-%d %H:%M")}
    if save:
        (save / "advice.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
