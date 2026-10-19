"""重新找步骤后编号重排、线改了名："和上次比"按计时点在代码里的位置对段和线，不按 P 编号比错（10-19 实测发现）；
体验标准按库挂到要测的那条线；没有体验标准、也没有上次可比的线明说"未判"。合理范围清单 10-19 去掉了。
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
    segments = {f"{p[a]}-{p[b]}": {"name": n, "from": p[a], "to": p[b]} for n, a, b in segs}
    return {"format": "fsw-scenario/1", "name": "真步骤", "start": {"point": p["刷卡"], "name": "刷卡"},
            "segments": segments, "lines": [{"name": line, "end": "锁到位", "segments": list(segments)}],
            "experiences": [], "problems": [], "points": {p[n]: {"n": n, "at": AT[n]} for n in order}}


def rounds(sc, vals=(0.40, 0.05, 0.10), n=10):
    ks = list(sc["segments"])
    return [{"n": i + 1, "segs": dict(zip(ks, (v + i * 0.001 for v in vals))), "ends": [sum(vals) + i * 0.003], "probs": []}
            for i in range(n)]


def test_renumbered_steps_still_pair_with_last_time():
    a = door()
    b = door(order=("开锁", "刷卡", "校验回来", "锁到位"))                  # 重新找步骤：编号重排
    pairs = rl.pair_segments(a["segments"], rl.at_now(a), b["segments"], rl.at_now(b))
    assert pairs == {"P1-P2": "P2-P3", "P2-P3": "P3-P1", "P3-P4": "P1-P4"}   # 同一步，换了编号也认得出


def test_same_number_but_different_step_is_not_compared():
    a, b = door(), door()
    b["points"]["P3"]["at"] = "main/lock.c|before|relay_on();"            # 编号还是 P2-P3、P3-P4，但计时点换了地方
    pairs = rl.pair_segments(a["segments"], rl.at_now(a), b["segments"], rl.at_now(b))
    assert pairs == {"P1-P2": "P1-P2"}


def test_without_locations_needs_same_name():
    a, b = door(), door()
    for x in (a, b):
        x.pop("points")                                                    # 老数据：没记位置
    b["segments"]["P2-P3"]["name"] = "服务器合成声音"                      # 编号一样、意思变了
    assert rl.pair_segments(a["segments"], {}, b["segments"], {}) == {"P1-P2": "P1-P2", "P3-P4": "P3-P4"}


def test_renamed_line_still_pairs_by_its_start_and_end():
    a = door()
    b = door(order=("开锁", "刷卡", "校验回来", "锁到位"), line="到锁到位")    # 线改了名、编号也变了，起点终点没变
    sa, sb = rl.line_sig(a, a["lines"][0], rl.at_now(a)), rl.line_sig(b, b["lines"][0], rl.at_now(b))
    assert rl.pair_lines(["开门线"], [sa], ["到锁到位"], [sb]) == {"开门线": "到锁到位"}


def test_default_standard_goes_on_target_line_even_if_another_line_has_one():
    sc = {"lines": [{"name": "反应线", "segments": ["P1-P2"]}, {"name": "开门线", "segments": ["P1-P3"], "first": True}],
          "segments": {"P1-P2": {"to": "P2"}, "P1-P3": {"to": "P3"}}, "point_kinds": {"P2": "画面", "P3": "动作"},
          "category": "门禁", "experiences": [{"line": "反应线", "fair": 0.5, "source": "手动"}]}
    out = std.with_default_experience(sc)
    door_std = next(e for e in out["experiences"] if e["line"] == "开门线")
    assert door_std["fair"] == 1.0 and "门禁" in door_std["source"]      # 按类别从标准库挂，出处带着
    assert {"line": "反应线", "fair": 0.5, "source": "手动"} in out["experiences"]


def test_line_without_standard_or_last_time_says_not_judged():
    a = door()
    lab = next(x for x in J.judge(a, rounds(a))["labels"] if x["kind"] == "line")
    assert lab["level"] == "未判" and "只记用时" in lab["why"]
    a["experiences"] = [{"line": "开门线", "fair": 1.0, "source": "x"}]    # 有体验标准就照常判
    assert next(x for x in J.judge(a, rounds(a))["labels"] if x["kind"] == "line")["level"] == "可以不改"


def test_range_list_is_gone_from_the_page_and_the_server():
    from fsw.web import app as web
    from tests.test_boss_short import JS
    assert "rangelist" not in JS and 'data-go="range"' not in JS and "rl-fair" not in JS and "用户设定" not in JS
    src = open(web.__file__, encoding="utf-8").read()
    assert "/api/rangelist" not in src and "合理范围清单.md" not in src
