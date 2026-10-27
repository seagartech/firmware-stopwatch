"""读合理范围（第一版：码表自带的标准文件，fsw/standards/*.json）。

标准文件就是一个场景（fsw-scenario/1 的段、线、合理范围、体验标准、问题规则），另外写了每个计时点在代码里的位置。
真找到的步骤按代码位置对上标准里的计时点（同一个文件、行号相差不超过几行），对上的段和线就用标准里的范围：
- 标准里正好有这一段：直接用；
- 标准里是连着的几段：范围、算慢线都加起来；含推算的段，整段标"推算"；
- 对不上的段：没有范围，只记用时、不判快慢。
问题规则里看线先后的照用；看附加值的（比如"第一包声音到达时设备的状态"）要这个计时点真记了附加值才用：
插计时点时码表按 needed_values 让 AI 顺带记上（只读已有的变量），插入记录里有了才判，没记上的不判。
多轮实测和报告按"这次实际插进去的计时点"算（inserted_page）：删掉的、不测的顺带线、编译出错撤掉的不要，你自己加的要。
"""

import json
import re
from pathlib import Path

from fsw.scenario import experiences, names_too_close

HERE = Path(__file__).parent / "standards"
REF_FILE = HERE / "reference.json"  # 通用参考值固定在码表里；HERE 可以换成别的标准文件夹（测试用）
NEAR = 8  # 行号相差几行以内算同一个位置


def digest(standard):
    """标准的指纹：合理范围、算慢线、体验标准有任何改动都会变。"""
    import hashlib
    if not standard:
        return None
    return hashlib.sha1(json.dumps(standard, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]


def changed_at(standard):
    """标准文件最后改的时间（给提示条用）。"""
    import time
    for p in HERE.glob("*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("name") == (standard or {}).get("name"):
                return time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime))
        except (OSError, ValueError):
            continue
    return "最近"


def load_all():
    """码表自带的设备标准。10-18 起不再自带任何设备的（原来那份小智专用的挪到测试素材里）：通用代码不迁就某一个固件；
    以后用户自己放进来的标准照样能对上。"""
    out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(HERE.glob("*.json"))]
    return [d for d in out if d.get("segments")]


def _library():
    try:
        return json.loads(REF_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def categories():
    """体验标准库里的设备类别（10-18：按类别挑垂直标准，对不上用尼尔森）。"""
    return _library().get("categories") or []


def reference(category=None):
    """这一类设备的体验标准；没有这一类（或 AI 写了"其他"）就用尼尔森三档。返回里带 category 说明按哪一类判的。"""
    lib = _library()
    hit = next((c for c in lib.get("categories") or [] if c.get("key") == (category or "").strip()), None)
    ref = hit or lib.get("fallback")
    if not ref:
        return None
    return {**{k: v for k, v in ref.items() if k in ("fair", "good", "source", "short", "start_gap")}, "category": ref.get("key")}


def _at(s):
    f, _, line = s.rpartition(":")
    return f, int(line)


def _line(s):
    """步骤的行号；不是整数（人改过、老的识别结果里留着 "655-660" 这类）就当没有，不对标准，不出错。"""
    try:
        return int(s.get("l"))
    except (TypeError, ValueError):
        return None


def match_points(steps, standard):
    """真步骤的计时点 → 标准里的计时点（一对一，取最近的）。"""
    cands = []
    for s in steps:
        mine = _line(s)
        if mine is None:
            continue
        for sp, info in standard.get("points", {}).items():
            f, line = _at(info["at"])
            if (s.get("path") or s.get("f")) == f and abs(mine - line) <= NEAR:
                cands.append((abs(mine - line), s["p"], sp))
    out, used = {}, set()
    for _, mine, theirs in sorted(cands):
        if mine not in out and theirs not in used:
            out[mine] = theirs
            used.add(theirs)
    return out


def find(steps, pool=None):
    """挑对得上最多计时点的标准；少于 2 个对上就算没有。pool 不给就用码表自带的（现在没有）。"""
    best, n = None, 1
    for s in (load_all() if pool is None else pool):
        k = len(match_points(steps, s))
        if k > n:
            best, n = s, k
    return best


def _chain(standard, a, b):
    """标准里从计时点 a 连到 b 的几段（直接一段优先）。"""
    edges = {}
    for k, g in standard["segments"].items():
        edges.setdefault(g["from"], []).append((g["to"], k))

    def walk(p, seen):
        if p == b:
            return []
        for to, k in sorted(edges.get(p, []), key=lambda x: x[0] != b):
            if to not in seen:
                rest = walk(to, seen | {to})
                if rest is not None:
                    return [k] + rest
        return None
    return walk(a, {a}) if a != b else None


def _sum_range(standard, keys):
    gs = [standard["segments"][k] for k in keys]
    if any(g.get("range") is None for g in gs):
        return None, None, "measured"
    lo = round(sum(g["range"][0] for g in gs), 6)
    hi = None if any(g["range"][1] is None for g in gs) else round(sum(g["range"][1] for g in gs), 6)
    slow = None if any(g.get("slow") is None for g in gs) else round(sum(g["slow"] for g in gs), 6)
    basis = "estimated" if any(g.get("basis") == "estimated" for g in gs) else "measured"
    return [lo, hi], slow, basis


def _value_rules(rule):
    if rule.get("type") == "any":
        return [x for r in rule.get("rules", []) for x in _value_rules(r)]
    return [rule] if rule.get("type") == "value_not" else []


def needed_values(steps, standard):
    """真步骤里哪几个计时点要顺带记附加值：标准里看附加值的问题规则，它的计时点对上了真插的哪一个。"""
    if not standard:
        return {}
    back = {theirs: mine for mine, theirs in match_points(steps, standard).items()}
    out = {}
    for p in standard.get("problems") or []:
        for r in _value_rules(p["rule"]):
            mine = back.get(r["point"])
            if mine:
                vo = r.get("value_of") or {}
                x = out.setdefault(mine, {"what": vo.get("what", "附加值"), "ok": vo.get("ok", ""), "how": vo.get("how", ""),
                                          "value": r["value"], "for": []})
                x["for"].append(p.get("name", p["key"]))
    return out


def _translate(rule, back, values):
    """标准里的问题规则换成真计时点的编号；换不过来的（点没插、附加值没记）返回 None。"""
    t = rule.get("type")
    if t == "any":
        rs = [x for x in (_translate(r, back, values) for r in rule.get("rules", [])) if x]
        return None if not rs else rs[0] if len(rs) == 1 else {**rule, "rules": rs}
    if t == "value_not":
        mine = back.get(rule["point"])
        if not mine or mine not in values:
            return None
        expect = values[mine].get("expect")
        return {**rule, "point": mine, "value": rule["value"] if expect is None else expect}
    if t == "point_before":
        a, b = back.get(rule["a"]), back.get(rule["b"])
        return {**rule, "a": a, "b": b} if a and b else None
    return None


def point_at(step):
    """计时点在代码里的位置：文件 + 插在哪行代码前后 + 那行代码（不看行号，改了别处的代码行号会变）。
    重新找步骤后编号（P7、P8……）会重排，靠它认出两次是不是同一个计时点。没有代码的（你自己加的步骤）返回 None。"""
    code = " ".join((step.get("code") or "").split())
    path = step.get("path") or step.get("f")
    return f"{path}|{step.get('where') or ''}|{code}" if code and path else None


# 一段两头在同一个函数里、但分别交给不同的回调（各自收到一条服务器消息才执行）：结构检查写进终点 chk 的那句话里有这个标记
# （新存档另有 split=True）。见 structure.check_segments
SPLIT_MARK = "两处分别在被调度或回调时执行"


def split_recv(page, steps, a, b):
    """这一段是不是"在等服务器的下一条消息"：起点本身是收到服务器消息的那一步（它是某个设备外的段的终点），
    终点在另一个回调里（收到另一条消息才执行）。这种段两头都在设备上记，但中间是在等网络、服务器，不是固件在忙。
    老存档没有这个检查结果就返回 False（照原来的写法）。"""
    sp = {x.get("p"): x for x in steps}
    sa, sb = sp.get(a), sp.get(b)
    if not sa or not sb:
        return False
    na, nb = f"「{sa.get('n')}」", f"「{sb.get('n')}」"
    hit = any((c.get("split") or SPLIT_MARK in str(c.get("text") or "")) and na in str(c.get("text") or "") and nb in str(c.get("text") or "")
              for c in sb.get("chk") or [] if isinstance(c, dict))
    if not hit:
        return False
    recv = {g.get("b") for g in list(page.get("segs") or []) + list(page.get("fsegs") or []) if g.get("out")}
    return a in recv


# 设备外那段的大白话：技术词换成普通说法（通用词表，不认具体固件）；只换词，不编内容
GLOSS = [(r"(?i)(?<![a-z])TTS(?![a-z])", "语音合成"), (r"(?i)(?<![a-z])(?:STT|ASR)(?![a-z])", "语音识别"), (r"(?i)(?<![a-z])LLM(?![a-z])", "大模型"),
         (r"(?i)(?<![a-z])OTA(?![a-z])", "升级"),
         ("信令", "通知"), ("首包", "第一包"), ("首句", "第一句"), ("首帧", "第一帧"), ("末包", "最后一包"), ("文本", "文字"),
         ("回传", "传回设备"), ("下发", "发到设备"), ("上行", "上传"), ("下行", "下载"), ("回复音频", "回复语音"), ("音频", "声音")]
# 拿"服务器负责什么"（识别时的 outside_note）去对段名时不算数的两个字：太常见，到处都有
_STOP2 = {"回复", "语音", "服务", "务器", "设备", "声音", "音频", "消息", "数据", "一个", "第一", "网络", "云端", "结果", "开始"}


def _note_items(note):
    note = re.sub(r"^(?:服务器|云端|服务端)[：:]\s*", "", str(note or "").strip())
    return [x.strip() for x in re.split(r"[、，,；;]|和|及", note) if x.strip()]


def gloss(text):
    t = str(text or "")
    for a, b in GLOSS:
        t = re.sub(a, b, t)
    return t


def plain_cloud(name, end_name, note, split=False):
    """设备外那一段在等什么（报告结论版"等云端（……）"括号里那句）：
    - 在等服务器的下一条消息（split_recv）：写"再发一条消息，收到后才{终点}"；
    - 段名里说到了"服务器负责什么"里的某几件（比如段名有"识别"、负责的有"语音识别"）：只写那几件；
    - 都没说到：用这段自己的名字换成大白话（TTS → 语音合成、首包 → 第一包、下发 → 发到设备……）。
    没有段名就退回"服务器负责什么"整句（老写法）。"""
    if split and end_name:
        return f"再发一条消息，收到后才{end_name}"
    nm = gloss(str(name or "").strip())
    if not nm:
        return "、".join(_note_items(note))
    hit = [x for x in _note_items(note) if any(x[i:i + 2] in nm and x[i:i + 2] not in _STOP2 for i in range(len(x) - 1))]
    if hit:
        return "、".join(hit)
    t = re.sub(r"^(?:等|服务器|云端|服务端)+", "", nm).strip()
    return t or nm


def short_name(name, to_name=""):
    """计时图色块里写的短名字（10-27 用户："首包回复音频到达＋音频包排队后被解码＋解码帧等待播放并开功放 1.12"太长）：
    段名换成大白话后不长（不超过 14 个字）就照写；几步拼起来的写"第一步→这段的终点"；还长就写"到「终点」"。全名、细节写在图下面。"""
    nm = gloss(str(name or "").strip())
    if len(nm) <= 14:
        return nm
    parts = [x.strip() for x in re.split(r"[＋+]", nm) if x.strip()]
    if len(parts) > 1 and to_name:
        c = f"{parts[0]}→{to_name}"
        if len(c) <= 16:
            return c
    if to_name:
        return f"到{to_name}"
    return nm[:12] + "…"


def _line_trigger(line, segments, pn, main_keys):
    """一条顺带的线是被什么带起来的：从要测的这条线分出去的第一段在设备外（收到服务器的消息才走）→ 跟服务器消息走；
    在设备上 → 跟设备自己走（比如设备状态一变就走）。"""
    br = next((k for k in line.get("segments") or [] if k not in main_keys), None)
    if br is None:
        return ""
    g = segments.get(br) or {}
    return "跟服务器消息走" if g.get("side") == "outside" else "跟设备自己走"


def name_lines(lines, segments, steps, side_names, main_keys, face_end="", face_dropped=False):
    """顺带的线按实际量到的点起名、太像的分开（10-26 用户：「换表情」「换上表情」只差一个字，其实是两件事）：
    - 大模型写的那条顺带的线有点没插上（face_dropped：识别时这条线的点比实际插上的多）、剩下的最后一个点又不是用户能感觉到的
      那一下（没标 felt）：原来想量的终点被去掉了，线名、终点按实际量到的点写，不再沿用识别时起的（比如"换表情"，实际量到的是
      "切到说话状态"）；点都插上了、或老存档没有这些信息的不改；
    - 几条线名字或终点名太像（names_too_close）：顺带的线后面写上被什么带起来的（跟设备自己走 / 跟服务器消息走），
      还分不开就写从哪一步分出去。返回 {旧名: 新名}。"""
    sp = {x.get("p"): x for x in steps}
    pn = {x.get("p"): x.get("n") for x in steps}
    for l in lines:  # 大模型自己写的那条顺带的线：终点名在识别结果里（网页按它写），线里只存了线名
        if l["name"] in side_names and not l.get("sl") and face_end and l.get("end") == l["name"]:
            l["_end"] = face_end
    end_of = lambda l: l.get("_end") or l.get("end")
    ren = {}
    for l in lines:
        if l["name"] not in side_names or not l.get("segments"):
            continue
        last = l["segments"][-1].split("-", 1)[-1]
        if face_dropped and not l.get("sl") and last in sp and not sp[last].get("felt") and pn.get(last) and pn[last] != end_of(l):
            new = f"到{pn[last]}"
            if new not in {x["name"] for x in lines}:
                ren[l["name"]] = new
                l.update(name=new, end=pn[last], actual=True, was=end_of(l) or "")
                l.pop("_end", None)
    taken = {x["name"] for x in lines}
    sides = [l for l in lines if l["name"] in side_names or l["name"] in ren.values()]

    def trig(l):
        t = _line_trigger(l, segments, pn, main_keys)
        twin = [o for o in sides if o is not l and _line_trigger(o, segments, pn, main_keys) == t
                and (names_too_close(l["name"], o["name"]) or names_too_close(end_of(l), end_of(o)))]
        if t and twin:  # 和它像的那条也是同样被带起来的：依次试从哪一步分出去、终点在哪个文件、哪一行，取第一个分得开的
            def keys(x):
                br = next((k for k in x["segments"] if k not in main_keys), "")
                a = br.split("-", 1)[0] if br else ""
                e = sp.get(x["segments"][-1].split("-", 1)[-1]) or {}
                f = e.get("f") or Path(e.get("path") or "").name
                return [f"从「{pn[a]}」分出去" if pn.get(a) else "", f"在 {f} 里" if f else "", f"{f} 第 {e.get('l')} 行" if f and e.get("l") else ""]
            mine = keys(l)
            for i, c in enumerate(mine):
                if c and all(keys(o)[i] != c for o in twin):
                    return c
            return ""
        return t
    tags = {}
    for l in sides:
        if any(o is not l and (names_too_close(l["name"], o["name"]) or names_too_close(end_of(l), end_of(o))) for o in lines):
            tags[id(l)] = trig(l)
    for l in sides:
        t = tags.get(id(l))
        if not t:
            continue
        old, new = l["name"], f"{_strip_note(l['name'])}（{t}）"
        if new in taken:
            continue
        taken.add(new)
        ren[next((k for k, v in ren.items() if v == old), old)] = new
        l.update(name=new, end=f"{_strip_note(end_of(l) or '')}（{t}）", actual=True)
    for l in lines:
        l.pop("_end", None)
    return ren


def _strip_note(s):
    return re.sub(r"（[^）]*）$", "", str(s or "")).strip()


def real_scenario(page, standard, values=None):
    """真找完的步骤（结果页数据）+ 标准 → 判快慢用的场景。没有标准就只有段和线、没有范围。"""
    steps = page.get("steps") or []
    mp = match_points(steps, standard) if standard else {}
    main = [(g["a"], g["b"], g) for g in page.get("segs") or []]
    on_main = {a for a, _, _ in main} | {b for _, b, _ in main}
    ln = page.get("ln") or {}
    sides = {}  # 顺带的线可以好几条（码表从候选里补的带 sl）：各自从起点算
    for g in page.get("fsegs") or []:
        sides.setdefault(g.get("sl") or "", []).append((g["a"], g["b"], g))
    for k, items in sides.items():
        if items[0][0] in on_main:  # 顺带那条线从起点算：先走要测的这条，到分叉的地方再拐过去
            # 分叉点不在要测的这条上（比如发生在起点之前的"按下变色"）：从它自己的第一个点单独量，不和主线拼
            # 只接从起点走到分叉点的那一串（主线的段里还有同时在走的，不接进来）
            by = {f"{a}-{b}": (a, b, g) for a, b, g in main}
            sides[k] = [by[x] for x in _path_keys(main, items[0][0]) if x in by] + items
    # 两条顺带的线从同一个点量到同一个点（10-27：问"要不要一起测"用的是码表补的那条，大模型那支的点又是同一个，报告里两条
    # 「表情变化这条」都到「换上表情」、每轮一模一样）：只留一条，留经过的点多、分得细的那条；另一条的名字记下，体验标准、先后规则跟着改
    same, kept = {}, []
    for k in sorted(sides, key=lambda x: -len(sides[x])):  # 点多的先留（排序稳定：一样多的留先写的那条）
        twin = next((o for o in kept if sides[k] and sides[o][0][0] == sides[k][0][0] and sides[o][-1][1] == sides[k][-1][1]), None)
        if twin is None:
            kept.append(k)
        else:
            same[k] = twin
    face_name = lambda k: k or ln.get("face", "顺带的那条")
    merged = {face_name(k): face_name(v) for k, v in same.items()}
    for k in same:
        sides.pop(k)
    side = [x for items in sides.values() for x in items]
    segments = {}
    pn0 = {x["p"]: x["n"] for x in steps}
    extra = [(g["a"], g["b"], g) for g in page.get("xsegs") or []]  # 不在哪条线上的段（你自己加的步骤）：只记用时
    main, extra, mend = _measure_end(page, steps, main, extra)
    for a, b, g in main + side + extra:
        k = f"{a}-{b}"
        if k in segments:
            continue  # 几条顺带的线共用的前缀段
        rng, slow, basis = None, None, "measured"
        if standard and a in mp and b in mp:
            ch = _chain(standard, mp[a], mp[b])
            if ch:
                rng, slow, basis = _sum_range(standard, ch)
        split = not g.get("out") and split_recv(page, steps, a, b)  # 在等服务器的下一条消息：算设备外（10-26 "到换上表情 属于设备"）
        out_ = bool(g.get("out")) or split
        segments[k] = {"name": g.get("t") or k, "what": g.get("t") or "", "owner": "网络、服务器" if split else g.get("who", ""),
                       "side": "outside" if out_ else "device", "from": a, "to": b,
                       "range": rng, "slow": slow, "basis": basis, **({"to_any": g["any"]} if g.get("any") else {}),
                       **({"split": True} if split else {}),
                       **({"plain": plain_cloud(g.get("t"), pn0.get(b), page.get("server"), split)} if out_ else {}),
                       "short": short_name(g.get("t") or "", pn0.get(b))}

    def std_line(a, b):
        if not standard or a not in mp or b not in mp:
            return None
        for l in standard["lines"]:
            segs = [standard["segments"][k] for k in l["segments"]]
            if segs and segs[0]["from"] == mp[a] and segs[-1]["to"] == mp[b]:
                return l
        return None

    # 要测的这条线按一轮真正结束的那一步（结果页主线最后一个点，比如回到聆听）收尾：从同一个点分出去的几段（放完、回到聆听）
    # 原来按段的先后取最后一段，总的那条线叫"到播放全部放完"，量的不是一轮的结尾（10-26 真测）
    # 插计时点时码表定的一轮结束那一步（放清零的那一步，roundEnd）优先：报告的总时长和清零按同一步算
    tail_end = page.get("roundEnd") or (page.get("main") or [None])[-1]
    if mend:  # 量的终点是人感觉到的那一下（回复放完）：全程这条量到它，roundEnd 只管清零
        tail_end = mend["p"]
    if tail_end and main and main[-1][1] != tail_end and any(b == tail_end for _, b, _ in main[1:]):
        k = max(i for i, (_, b, _) in enumerate(main) if b == tail_end)
        main = main[:k] + main[k + 1:] + [main[k]]
    lines = []
    ends = {x.get("name"): x.get("end") for x in page.get("sides") or []}
    for name, items in [(k or ln.get("face", "顺带的那条"), v) for k, v in sides.items()] + [(ln.get("talk", "要测的这条"), main)]:
        if not items:
            continue
        sl = std_line(items[0][0], items[-1][1])
        lines.append({"name": name, "end": ends.get(name) or name, **({"sl": True} if name in ends else {}), "segments": [f"{a}-{b}" for a, b, _ in items],
                      "range": sl["range"] if sl else None, "slow": sl["slow"] if sl else None, "std": sl["name"] if sl else None})
    # 要测的这条线中途有"第一下感觉到的结果"（AI 标的 felt=first，比如开始出声、锁开始动、识别文字上屏）、终点在它之后：
    # 每一个都另记一条到那一步的线，互不顶替。主指标（标题、首屏、计时图、体验标准都挂它，first=True）是设备往外给出结果的第一下：
    # 识别时标了"输出"的那一步（声音、动作、灯、显示）；没标的按先后取第一个，但 AI 自己说用户看不到的（比如字幕被藏起来）不当主指标。
    # 其余的（比如识别文字到达这类中间结果）是次要的线（also_first），排在主指标前面（10-26 8784：主指标原来是看不到的识别文字）。
    # 到最后一下的长短常常取决于内容多少（回答多长、门多重）
    pn = {x["p"]: x["n"] for x in steps}
    sp = {x["p"]: x for x in steps}
    firsts = []
    for _, b, _ in main[:-1]:
        if (sp.get(b) or {}).get("felt") == "first" and b not in firsts:
            firsts.append(b)
    primary = primary_first(firsts, sp)
    if mend and not firsts and lines:
        lines[-1].update(name=mend["line"], end=mend["n"], measure=True)
    if firsts:
        lines[-1].update(name=f"到{pn[main[-1][1]]}", end=pn[main[-1][1]])  # 几条都按终点叫，免得"开口这条"其实量到全部放完
        if mend:
            lines[-1].update(name=mend["line"], end=mend["n"], measure=True)
        for b in [x for x in firsts if x != primary] + [primary]:
            ks = _path_keys(main, b)
            lines.insert(len(lines) - 1, {"name": f"到{pn[b]}", "end": pn[b], **({"first": True} if b == primary else {"also_first": True}),
                                          "range": None, "slow": None, "std": None, "segments": ks})
    # 每条线只放从起点走到它终点的那一串段（图、表、合计都按它）；从中间分出去、同时在走的段（比如一直在发的上传、
    # 从同一步量起的几段）不进哪条线，单独列"并行的段"，照样有数（10-26：图和表把它们当先后的几段加起来，合计对不上）
    side_names = {k or ln.get("face", "顺带的那条") for k in sides}
    for l in lines:
        if l["name"] not in side_names:
            l["segments"] = _path_keys(main, None, l["segments"])
    on_line = {k for l in lines for k in l["segments"]}
    parallel = [f"{a}-{b}" for a, b, _ in main if f"{a}-{b}" not in on_line]
    exps = []  # 体验标准每条线一条：标准里那条线对上了真线，就挂到真线上
    for e in experiences(standard or {}):
        hit = [l for l in lines if l["std"] == e.get("line")]
        if hit and e.get("fair") is not None:
            exps.append({**e, "line": hit[0]["name"]})
    problems = []
    names = {l["std"]: l["name"] for l in lines if l["std"]}
    back = {theirs: mine for mine, theirs in mp.items()}
    for p in (standard or {}).get("problems") or []:
        r = p["rule"]
        if r["type"] == "line_order":
            if r["first"] in names and r["second"] in names:
                problems.append({**p, "rule": {**r, "first": names[r["first"]], "second": names[r["second"]]}})
        elif t := _translate(r, back, values or {}):
            problems.append({**p, "rule": t})
    # 顺带的线按实际量到的点起名、太像的分开；改了名的线，体验标准、先后规则跟着改
    main_keys = {f"{a}-{b}" for a, b, _ in main}
    side_nodes = {n for x in page.get("sides") or [] for n in x.get("nodes") or []}
    face0 = [n for n in (page.get("branch") or {}).get("nodes") or [] if n not in side_nodes]
    face1 = [x["p"] for x in steps if x.get("line") == "face" and x["p"] not in side_nodes]
    ren = name_lines(lines, segments, steps, side_names, main_keys, str((page.get("sideAsk") or {}).get("q") or ""),
                     face_dropped=len(face1) < len(face0))
    ren = {**{a: ren.get(b, b) for a, b in merged.items() if a != ren.get(b, b)}, **ren}
    if ren:
        exps = [{**e, "line": ren.get(e.get("line"), e.get("line"))} for e in exps]
        problems = [{**p, "rule": {**p["rule"], **{k: ren.get(p["rule"][k], p["rule"][k]) for k in ("first", "second") if k in p["rule"]}}}
                    if p.get("rule", {}).get("type") == "line_order" else p for p in problems]
    start = main[0][0] if main else None
    sname = next((x["n"] for x in steps if x["p"] == start), steps[0]["n"] if steps else "起点")
    return {"format": "fsw-scenario/1", "name": "真步骤", "start": {"point": start, "name": sname},
            "segments": segments, "lines": [{k: v for k, v in l.items() if k != "std"} for l in lines],
            "experiences": exps, "problems": problems, "parallel": parallel, "standard": (standard or {}).get("name"),
            "point_names": {x["p"]: x["n"] for x in steps},
            "point_kinds": {x["p"]: x.get("kind") or "" for x in steps if x.get("felt")},
            "category": page.get("category") or "",
            # 一轮取最后一次的点：感觉到的最后一下、插成"记最后一次"的、码表有意设成每次都记的全部放完（回复分几句播，每句之间都放空一次）
            "last_points": [x["p"] for x in steps if x.get("felt") == "last" or x.get("once") == "last" or x.get("whole")],
            "start_late": bool(page.get("startLate")), "start_note": page.get("startNote") or "",
            "optional": [x["p"] for x in steps if x.get("opt")],
            # 跟着起点记的点（见 identify.follow_start_points）：只认码表取的那次起点之后的
            "follow": [x["p"] for x in steps if x.get("follow")],
            "points": {x["p"]: {"n": x["n"], "f": x.get("f") or Path(x.get("path") or "").name, "l": x.get("l"),
                                **({"at": a} if (a := point_at(x)) else {})} for x in steps},
            "matched": mp}


UNSEEN = re.compile(r"看不到|看不见|不显示|不会显示|显示不出|藏起来|被隐藏|隐藏了|听不到|听不见|不可见|用户感觉不到|感觉不到")


def unseen(step):
    """AI 在这一步的说明里自己说了用户感觉不到（比如字幕被藏起来、屏幕上实际看不到）。"""
    return bool(UNSEEN.search(" ".join(str(step.get(k) or "") for k in ("note", "why", "doubt"))))


def primary_first(firsts, sp):
    """几个"第一下感觉到的结果"里当主指标的那个：设备往外给出结果的第一下（识别时标了输出的那一步，out=True）；
    没标的取第一个用户感觉得到的（AI 说明里没说看不到）；都说看不到就取第一个。"""
    if not firsts:
        return None
    seen = [p for p in firsts if not unseen(sp.get(p) or {})]
    return next((p for p in seen if (sp.get(p) or {}).get("out")), None) or next((p for p in firsts if (sp.get(p) or {}).get("out")), None) \
        or (seen or firsts)[0]


def _path_keys(main, end, keys=None):
    """从起点走到 end（不给就是 keys 里最后一段的终点）经过的那几段的名字（和 measure._path 一样往回接）；接不回起点就原样。"""
    segs = [(a, b) for a, b, _ in main] if keys is None else [tuple(k.split("-", 1)) for k in keys]
    if not segs:
        return []
    start = main[0][0] if main else segs[0][0]
    x = end or segs[-1][1]
    if keys is None:
        k = max((i for i, (_, b) in enumerate(segs) if b == x), default=None)
        if k is None:
            return []
    path = []
    while x != start:
        prev = next((g for g in reversed(segs) if g[1] == x and g not in path), None)
        if prev is None:
            return [f"{a}-{b}" for a, b in segs] if keys is not None else [f"{a}-{b}" for a, b in segs[:k + 1]]
        path.insert(0, prev)
        x = prev[0]
    return [f"{a}-{b}" for a, b in path]


def _measure_end(page, steps, main, extra):
    """量的终点（结果页的 measure，比如"说话结束 → 回复放完"）是人感觉到的最后一下（felt=last：最后一段声音、播放放空），
    而码表定的一轮结束（roundEnd，放清零的那一步，比如服务器说"说完了"切回聆听）只是协议上的结束、和它谁先谁后说不准时：
    要测的这条线量到"这几个里每轮最晚的那个"（一段 to_any），名字按 measure 叫"全程：……"；
    从同一个点分出去、各量到其中一个的几段（尾部放完、整段播放）合成这一段，不再并排列两行差不多的；
    从起点直接到这个终点的记录段（回复播放放空）就是这条线，不再另列。没有 felt=last 的点、或终点就是 roundEnd：原样。"""
    sp = {x["p"]: x for x in steps}
    order = list(page.get("main") or [])
    cands = [p for p in order if (sp.get(p) or {}).get("felt") == "last"]
    cands += [b for _, b, _ in extra if (sp.get(b) or {}).get("felt") == "last" and b not in cands]
    rend = page.get("roundEnd")
    if not cands or not main:
        return main, extra, None
    meas = (page.get("measure") or "").strip()
    endn = meas.split("→")[-1].strip() if "→" in meas else ""

    def info(p):
        n = endn or (sp.get(p) or {}).get("n", p)
        return {"p": p, "n": n, "line": f"全程：{meas}" if endn else f"全程：到{n}", "any": [p]}
    named = [p for p in cands + ([rend] if rend else []) if endn and (sp.get(p) or {}).get("n")
             and ((sp.get(p) or {}).get("n") == endn or endn in sp[p]["n"] or sp[p]["n"] in endn)]
    if not rend or (rend in cands and len(cands) == 1) or (named and named[0] == rend):
        # 一轮结束那一步本身就是人感觉到的最后一下（比如回到聆听），或码表没定一轮结束：线不动，只按 measure 起名
        # 一轮结束那一步也是"感觉到的最后一下"之一、但还有别的（比如结束消息和播放放空、上报放空）：照样合成一段取最晚的
        # （8783：清零挪到结束消息那一步后，全程原来量到结束消息、不是 measure 说的播放结束）
        return main, extra, info(rend or (named[0] if named else main[-1][1]))
    rend_felt = rend in cands
    if not rend_felt and any(b == rend for _, b, _ in main):
        cands.append(rend)  # 协议上的结束和人感觉到的结束谁先谁后说不准：取晚的那个
    m = (named[0] if named else None) or next((p for p in cands if (sp.get(p) or {}).get("whole")), None) \
        or next(p for p in cands if p != rend)
    sinks = [(a, b, g) for a, b, g in main if b in cands]
    if not sinks:
        return main, extra, None
    idx = {p: i for i, p in enumerate(order)}
    br = max((a for a, _, _ in sinks), key=lambda p: idx.get(p, -1))
    if any(a != br for a, _, _ in sinks) or any(a in cands for a, _, _ in main):
        return main, extra, None  # 终点之间还串着别的段：不合，原样
    first = (m, rend) if rend_felt else (rend, m)  # 一轮结束本身也是感觉到的最后一下：段名按量到的那一步叫，不叫"到结束消息"
    base = next((g for a, b, g in sinks if b == first[0]), None) or next((g for a, b, g in sinks if b == first[1]), None) or sinks[-1][2]
    # 谁负责按量到的那一段写（10-26：几段的负责方全拼在一起成了"固件 + 硬件 + 网络 + 服务器"，看不出是谁）
    seg = {**base, "a": br, "b": m, "who": base.get("who", ""), "any": cands}
    at = min(i for i, (a, b, g) in enumerate(main) if b in cands)
    main = [x for x in main[:at] if x[1] not in cands] + [(br, m, seg)] + [x for x in main[at:] if x[1] not in cands]
    start = main[0][0]
    extra = [x for x in extra if not (x[0] == start and x[1] == m)]
    return main, extra, {**info(m), "any": cands}


def _merge(parts):
    """几段接成一段（中间的计时点没插）：名字连起来，都是外部的才算外部。"""
    if len(parts) == 1:
        return dict(parts[0])
    who = []
    for g in parts:
        if g.get("who") and g["who"] not in who:
            who.append(g["who"])
    out = {"a": parts[0]["a"], "b": parts[-1]["b"], "t": "＋".join(g.get("t") or f"{g['a']}-{g['b']}" for g in parts),
           "who": "、".join(who)}
    if any(g.get("sl") for g in parts):
        out["sl"] = next(g["sl"] for g in parts if g.get("sl"))
    if all(g.get("out") for g in parts):
        out["out"] = True
    return out


def _contract(segs, keep):
    """一条线上的段，去掉没插的计时点：经过它的前后两段接成一段；线头、线尾没插的那段不要。"""
    out, acc = [], []
    for g in segs:
        if acc and g["a"] != acc[-1]["b"]:  # 接不上（不连续）：前面没收住的不要
            acc = []
        if not acc and g["a"] not in keep:
            continue
        acc.append(g)
        if g["b"] in keep:
            out.append(_merge(acc))
            acc = []
    return out


def inserted_page(page, steps=None, inserted=None):
    """按这次实际插进去的计时点改写结果页数据，多轮实测、报告、"以前测过的"重算都用它（不用 AI 最初找到的那份）。
    steps：插计时点时用的步骤（核对后的，含你自己加的 U1、U2……）；不给就用结果页的步骤。
    inserted：插入记录里真插上的计时点编号（P3、P21……）；不给就当 steps 全插上了。
    - 编号统一换成计时点编号（你加的 U1 → P21），和设备打印的一致；
    - 没插的计时点（删掉的、不测的顺带线、编译出错撤掉的）：经过它的前后两段接成一段，线头线尾的那段不要；
    - 结果页的段里没有的计时点（你自己加的）：记一段"从起点到这一步"，不放进哪条线。"""
    from fsw.instrument import point_id
    steps = list(steps if steps is not None else page.get("steps") or [])
    orig = {s["p"]: s for s in page.get("steps") or []}

    ren, kept = {}, []
    for s in steps:
        k = point_id(s, steps)
        if inserted is not None and k not in inserted:
            continue
        ren[s["p"]] = k
        o = {**orig.get(s["p"], {}), **{f: s[f] for f in ("n", "path", "l", "line") if s.get(f) is not None}}
        kept.append({**o, "p": k, "f": o.get("f") or Path(o.get("path") or "").name})
    keep = set(ren)
    # 要测的这条线上只在某种触发方式才走的计时点（opt，比如点屏幕开始）不放进线里：只记"从起点到这一步"。
    # 顺带那条线（比如表情）的点不一定每轮都有也照样留在线上：哪一轮没收到，只是那一轮这条线算不出来
    keep_line = keep - {s["p"] for s in steps if (orig.get(s["p"]) or s).get("opt")
                        and (orig.get(s["p"]) or s).get("line") != "face"}
    main = _contract(page.get("segs") or [], keep_line)
    groups = {}
    for g in page.get("fsegs") or []:  # 顺带的线可以好几条（sl 分开），各自接、各自收
        groups.setdefault(g.get("sl") or "", []).append(g)
    side = []
    for sl, side1 in groups.items():
        while side1 and side1[0]["a"] not in keep:  # 顺带的线从一个没插的点分出去：往前接到要测的这条线上插了的那个点
            prev = next((g for g in page.get("segs") or [] if g["b"] == side1[0]["a"]), None)
            if not prev:
                break
            side1.insert(0, {**prev, **({"sl": sl} if sl else {})})
        side1 = _contract(side1, keep_line)
        if side1 and side1[-1]["b"] in {x for g in main for x in (g["a"], g["b"])}:
            side1 = []  # 顺带那条线自己的点一个都没插（不测这条线）：只剩和要测的这条重合的那段，不算一条线
        side += side1

    def re_id(g):
        return {**g, "a": ren[g["a"]], "b": ren[g["b"]]}
    main, side = [re_id(g) for g in main], [re_id(g) for g in side]
    used = {x for g in main + side for x in (g["a"], g["b"])}
    start = main[0]["a"] if main else None
    xsegs = [{"a": start, "b": s["p"], "t": s["n"], "who": ""} for s in kept if start and s["p"] not in used]
    return {**page, "steps": kept, "segs": main, "fsegs": side, "xsegs": xsegs,
            "main": [s["p"] for s in kept if s.get("line") != "face"]}




def with_default_experience(sc):
    """到"第一下感觉到的结果"那条线还没有体验标准时，按设备类别从体验标准库挑一条挂上（出处写在库里），
    对不上任何类别用尼尔森三档。已经有体验标准的不动。"""
    from fsw.scenario import experience_of
    lines = sc.get("lines") or []
    kinds = sc.get("point_kinds") or {}
    # 套在终点是"第一下感觉到的结果"的线上：有"到第一下"那条就用它；没有就用要测的这条（它的终点就是唯一那一下）
    line = next((l for l in lines if l.get("first")), None) or (lines[-1] if lines else None)
    if not line or experience_of(sc, line["name"]):  # 只看这条线自己：别的线（比如表情）有体验标准不算
        return sc
    end = ((sc.get("segments") or {}).get((line.get("segments") or [""])[-1]) or {}).get("to")
    if end not in kinds:  # 这条线的终点不是 AI 标的"感觉到的结果"（老的识别结果没标）：不知道人在等什么，不套
        return sc
    if sc.get("start_late"):  # 起点比人做完晚一截（比如要等服务器回话才知道）：体验标准从人做完算起，拿少算的用时去比会判轻，不比
        why = sc.get("start_note") or "起点之前还有一段设备看不见"
        return {**sc, "exp_skip": f"起点比你做完晚一截（{why.rstrip('。')}），测出的用时少算了这段，所以不拿体验标准比"}
    ref = reference(sc.get("category"))  # 按 AI 认出的设备类别挑；对不上用尼尔森三档（10-18 用户定）
    if not ref or ref.get("fair") is None:
        return sc
    return {**sc, "experiences": list(sc.get("experiences") or []) + [{**ref, "line": line["name"]}]}
