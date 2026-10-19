"""重新找步骤后编号重排、线改了名：合理范围和体验标准跟着计时点在代码里的位置走，不按 P 编号套错（10-19 实测发现）。
用门禁数据：码表不知道它是门禁，语音对话也一样。"""

from fsw import judge as J
from fsw import rangelist as rl
from fsw import standards as std

# 门禁固件里的四个计时点：文件 + 插在哪行前后 + 那行代码
AT = {"刷卡": "main/card.c|before|uid = rc522_read();", "校验回来": "main/net.c|after|resp = http_post(url, uid);",
      "开锁": "main/lock.c|before|gpio_set(LOCK, 1);", "锁到位": "main/lock.c|after|wait_sensor(DOOR_OPEN);"}


def door(order=("刷卡", "校验回来", "开锁", "锁到位"), line="开门线"):
    """order 决定编号：重新找步骤后同样的计时点可能排成别的 P 编号。"""
    p = {n: f"P{i + 1}" for i, n in enumerate(order)}
    segs = [("读卡号并校验", "刷卡", "校验回来"), ("驱动电磁锁", "校验回来", "开锁"), ("锁舌到位", "开锁", "锁到位")]
    segments = {f"{p[a]}-{p[b]}": {"name": n, "from": p[a], "to": p[b], "range": None, "slow": None} for n, a, b in segs}
    return {"format": "fsw-scenario/1", "name": "真步骤", "start": {"point": p["刷卡"], "name": "刷卡"},
            "segments": segments, "lines": [{"name": line, "end": "锁到位", "segments": list(segments), "range": None, "slow": None}],
            "experiences": [], "problems": [], "points": {p[n]: {"n": n, "at": AT[n]} for n in order}}


def rounds(sc, vals=(0.40, 0.05, 0.10), n=10):
    ks = list(sc["segments"])
    return [{"n": i + 1, "segs": dict(zip(ks, (v + i * 0.001 for v in vals))), "ends": [sum(vals) + i * 0.003], "probs": []}
            for i in range(n)]


def test_renumbered_steps_keep_their_own_ranges():
    a = door()
    lst, _ = rl.fill(None, a, rounds(a))
    rl.edit_segment(lst, "P2-P3", [0.04, 0.06], 0.08, "手动")             # 驱动电磁锁：人定的范围
    lst = rl.loads(rl.dumps(lst))                                          # 位置跟着文件存下来
    b = door(order=("开锁", "刷卡", "校验回来", "锁到位"))                  # 重新找步骤：编号重排
    s = rl.apply(b, lst)["segments"]
    assert s["P3-P1"]["range"] == [0.04, 0.06]                             # 同一步，换了编号也认得出
    assert s["P1-P4"]["range"] == lst["segments"]["P3-P4"]["range"]


def test_same_number_but_different_step_is_not_judged_with_old_range():
    a = door()
    lst, _ = rl.fill(None, a, rounds(a))
    lst = rl.loads(rl.dumps(lst))
    b = door()
    b["points"]["P3"]["at"] = "main/lock.c|before|relay_on();"            # 编号还是 P2-P3、P3-P4，但计时点换了地方
    s = rl.apply(b, lst)["segments"]
    assert s["P2-P3"]["range"] is None and s["P3-P4"]["range"] is None
    assert s["P1-P2"]["range"] == lst["segments"]["P1-P2"]["range"]
    lst2, done = rl.fill(lst, b, rounds(b))                                # 测满 10 轮：换了的两段重新填，旧的去掉
    assert set(done) == {"驱动电磁锁", "锁舌到位"} and set(lst2["segments"]) == {"P1-P2", "P2-P3", "P3-P4"}


def test_old_list_without_locations_needs_same_name():
    a = door()
    lst, _ = rl.fill(None, a, rounds(a))
    lst.pop("at"), lst.pop("line_at")                                      # 老清单：没记位置
    b = door()
    b["segments"]["P2-P3"]["name"] = "服务器合成声音"                      # 编号一样、意思变了
    s = rl.apply(b, lst)["segments"]
    assert s["P2-P3"]["range"] is None and s["P1-P2"]["range"] is not None


def test_renamed_line_keeps_range_and_experience_standard():
    a = door()
    lst, _ = rl.fill(None, a, rounds(a))
    rl.edit_line(lst, "开门线", lst["lines"][0]["range"], lst["lines"][0]["slow"], 1.0, "省级门禁指南")
    lst = rl.loads(rl.dumps(lst))
    b = door(order=("开锁", "刷卡", "校验回来", "锁到位"), line="到锁到位")    # 线改了名、编号也变了，起点终点没变
    s = rl.apply(b, lst)
    assert s["lines"][0]["range"] == lst["lines"][0]["range"]
    assert s["experiences"] == [{"line": "到锁到位", "fair": 1.0, "source": "省级门禁指南"}]


def test_empty_source_is_not_called_user_set():
    a = door()
    lst, _ = rl.fill(None, a, rounds(a))
    lst["lines"][0].update(fair=1.0, source="")                            # 文件里有数、出处空着
    assert rl.apply(a, lst)["experiences"][0]["source"] != "用户设定"


def test_meta_line_survives_code_with_comment_end():
    a = door()
    a["points"]["P1"]["at"] = "main/card.c|before|while (n-->0) read();"
    lst, _ = rl.fill(None, a, rounds(a), project="门禁")
    back = rl.loads(rl.dumps(lst))
    assert back["at"]["P1"] == "main/card.c|before|while (n-->0) read();" and back == lst


def test_default_standard_goes_on_target_line_even_if_another_line_has_one():
    sc = {"lines": [{"name": "反应线", "segments": ["P1-P2"]}, {"name": "开门线", "segments": ["P1-P3"], "first": True}],
          "segments": {"P1-P2": {"to": "P2"}, "P1-P3": {"to": "P3"}}, "point_kinds": {"P2": "画面", "P3": "动作"},
          "category": "门禁", "experiences": [{"line": "反应线", "fair": 0.5, "source": "手动"}]}
    out = std.with_default_experience(sc)
    door_std = next(e for e in out["experiences"] if e["line"] == "开门线")
    assert door_std["fair"] == 1.0 and "门禁" in door_std["source"]      # 按类别从标准库挂，出处带着
    assert {"line": "反应线", "fair": 0.5, "source": "手动"} in out["experiences"]


def test_line_without_range_or_standard_says_not_judged():
    a = door()
    j = J.judge(a, rounds(a))
    lab = next(x for x in j["labels"] if x["kind"] == "line")
    assert lab["level"] == "未判" and "没判" in lab["why"]
    a["experiences"] = [{"line": "开门线", "fair": 1.0, "source": "x"}]    # 有体验标准就照常判
    assert next(x for x in J.judge(a, rounds(a))["labels"] if x["kind"] == "line")["level"] == "可以不改"
