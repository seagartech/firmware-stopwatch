"""码表自己记每段用时（决策 99）。

每段记开始、结束时间（存进项目文件夹的 timeline.json，测完也放一份进这次的结果文件夹），
按"码表流程用时标准"的四笔账分类，对照标准标"好 / 能忍 / 超了"：
- 码表耗时：码表自己干活（找关键步骤、插计时点、编译、烧录、写"为什么、怎么改"、导出、还原）；
- 被测设备：设备反应、放完回答（从多轮实测的计时点算）；
- 人配合：选源码、核对步骤、确认改动、试一次、多轮实测里说话和轮间空档；另记说了几句、点了几次、补测几轮、误弹几次；
- 等人：等插设备、等你说"准备好了"、拔线暂停。
"""

import json
import time
from pathlib import Path

# 段：(键, 名字, 记在哪笔账)
STAGES = [("source", "选源码、板子、写测什么", "human"), ("identify", "找关键步骤", "tool"), ("check", "核对步骤", "human"),
          ("insert", "插计时点", "tool"), ("confirm", "看改动、确认", "human"), ("build", "编译计时版", "tool"),
          ("flash", "烧录", "tool"), ("try", "试一次", "human"), ("collect", "多轮实测", "collect"),
          ("advise", "写\"为什么、怎么改\"", "tool"), ("export", "导出报告", "tool"), ("restore", "还原成原版", "tool")]
BOOKS = {"tool": "码表耗时", "device": "被测设备的时间", "human": "人配合", "wait": "等人"}

# 用时标准（秒）：(好, 能忍)；分第一次测、再测（能用上次结果）
STD = {
    "source": {"first": (120, 300), "again": (60, 120)},
    "identify": {"first": (120, 300), "again": (5, 10)},
    "insert": {"first": (90, 180), "again": (5, 10)},
    "build": {"first": (60, 180), "again": (60, 120)},
    "flash": (30, 60),
    "try": (60, 120),
    "collect": (25, 36),  # 每轮（20 轮好 ≤8 分钟、能忍 ≤12 分钟）
    "advise": (60, 180),
    "restore": (60, 120),
}
PLAY_MAX = 30  # 设备放一句回答不会超过这么久；两轮之间多出来的算等人
TOTAL = {"tool": {"first": (480, 900), "again": (180, 360)}, "human": (720, None)}


class Timeline:
    def __init__(self, path=None, clock=time.time):
        self.path, self.clock, self.copies = Path(path) if path else None, clock, []
        self.data = {"stages": {}, "waits": [], "counts": {}}

    def _now(self, at):
        return at if at is not None else round(self.clock(), 1)

    def begin(self, name, at=None, **extra):
        self.data["stages"][name] = {"start": self._now(at), **extra}
        self.save()

    def end(self, name, at=None, **extra):
        s = self.data["stages"].setdefault(name, {"start": None})
        s.update(end=self._now(at), **extra)
        self.save()

    def wait(self, stage, secs, why, at=None):
        if secs and secs > 0:
            self.data["waits"].append({"stage": stage, "secs": round(secs, 1), "why": why, "at": self._now(at)})
            self.save()

    def count(self, key, n=1):
        self.data["counts"][key] = self.data["counts"].get(key, 0) + n
        self.save()

    def save(self):
        for p in [self.path] + self.copies:
            if p:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
                except OSError:
                    pass


def split_rounds(rounds):
    """多轮实测按设备时刻拆开：说话（开始聆听 → 你说完）、设备反应（你说完 → 最后一个计时点）、
    放完回答（这一轮最后一个点 → 下一轮开始聆听；最后一轮不知道放到哪，不算）。"""
    ok = [r for r in rounds if r.get("ok") and r.get("t")]
    speak = sum(r["t"][1] - r["t"][0] for r in ok)
    react = sum(r["t"][2] - r["t"][1] for r in ok)
    gaps = [b["t"][0] - a["t"][2] for a, b in zip(ok, ok[1:]) if b["t"][0] > a["t"][2]]
    play = sum(min(g, PLAY_MAX) for g in gaps)
    idle = sum(g - PLAY_MAX for g in gaps if g > PLAY_MAX)  # 两轮之间太久：人在等、或者有一轮没算上，算等人
    return {"speak": round(speak, 3), "react": round(react, 3), "play": round(play, 3), "idle": round(idle, 3),
            "ok": len([r for r in rounds if r.get("ok")]), "bad": len([r for r in rounds if not r.get("ok")])}


def _judge(secs, std):
    if secs is None or not std:
        return None
    good, ok = std
    return "好" if secs <= good else "能忍" if ok is None or secs <= ok else "超了"


def account(data, device=None):
    """四笔账、每段用时和判断。device：split_rounds 的结果，再加 hints（误弹几次）。没记下的写"没记下"，不编。"""
    st, waits = data.get("stages", {}), data.get("waits", [])
    first = not (st.get("identify") or {}).get("reused", False)
    wait_of = {}
    for w in waits:
        wait_of[w["stage"]] = wait_of.get(w["stage"], 0) + w["secs"]
    books = {"tool": 0.0, "human": 0.0, "device": None, "wait": round(sum(w["secs"] for w in waits), 1)}
    rows = []
    for key, name, book in STAGES:
        s = st.get(key) or {}
        secs = round(s["end"] - s["start"], 1) if s.get("start") is not None and s.get("end") is not None else None
        idle = (device or {}).get("idle", 0) if key == "collect" else 0
        own = round(secs - wait_of.get(key, 0) - idle, 1) if secs is not None else None
        row = {"key": key, "name": name, "secs": own, "book": book, "note": "" if secs is not None else "没记下"}
        if key in ("identify", "insert") and s:
            row["note"] = "用上次结果，没调用大模型" if s.get("reused") else ("用核对时提前准备好的插法" if s.get("prepared") else "")
        std = STD.get(key)
        if isinstance(std, dict):
            std = std["first" if first else "again"]
        if key == "collect":
            n = (device or {}).get("ok", 0) + (device or {}).get("bad", 0)
            per = round(own / n, 1) if own is not None and n else None
            row.update(per_round=per, verdict=_judge(per, std), std=std)
            if own is not None and device:
                dev = device["react"] + device["play"]
                books["device"] = round(dev, 1)
                books["human"] += max(0.0, own - dev)
            elif own is not None:
                books["human"] += own
        else:
            row.update(verdict=_judge(own, std), std=std)
            if own is not None and book in books and books[book] is not None:
                books[book] += own
        rows.append(row)
    if (device or {}).get("idle"):
        books["wait"] += device["idle"]
        waits = waits + [{"stage": "collect", "secs": round(device["idle"], 1), "why": f"多轮实测两轮之间超过 {PLAY_MAX} 秒的部分（人在等、或者有一轮没算上）"}]
    books = {k: (round(v, 1) if v is not None else None) for k, v in books.items()}
    c = data.get("counts", {})
    counts = {"said": c.get("try_said", 0) + (device or {}).get("ok", 0) + (device or {}).get("bad", 0),
              "redo": (device or {}).get("bad", 0), "false_hints": (device or {}).get("hints", 0), "clicks": c.get("clicks", 0),
              "llm_calls": c.get("llm_calls", 0)}
    tot = TOTAL["tool"]["first" if first else "again"]
    return {"first": first, "stages": rows, "books": books, "names": BOOKS, "counts": counts,
            "waits": waits, "verdict": {"tool": _judge(books["tool"], tot), "human": _judge(books["human"], TOTAL["human"])},
            "std_total": {"tool": tot, "human": TOTAL["human"]}}
