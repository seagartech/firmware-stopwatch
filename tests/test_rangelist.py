"""合理范围清单（减重版）：两张表；测满 10 轮按实测自动填；报告按它判；手动改；文件写坏了说第几行。门禁、对话都适用。"""

import pytest

from fsw import judge as J
from fsw import rangelist as rl


def door():
    """门禁：刷卡到开门，三段一条线（只是示例数据，码表不知道它是门禁）。"""
    segs = {"read": ("读卡号", "P1", "P2"), "check": ("服务器校验", "P2", "P3"), "lock": ("电磁锁动作", "P3", "P4")}
    return {"format": "fsw-scenario/1", "name": "门禁", "start": {"point": "P1", "name": "刷卡"},
            "segments": {k: {"name": n, "from": a, "to": b, "range": None, "slow": None} for k, (n, a, b) in segs.items()},
            "lines": [{"name": "开门线", "end": "开门", "segments": list(segs), "range": None, "slow": None}],
            "experiences": [], "problems": []}


def rounds(n, read=0.05, check=0.4, lock=0.1):
    return [{"n": i + 1, "segs": {"read": read, "check": check + i * 0.001, "lock": lock}, "ends": [read + check + i * 0.001 + lock],
             "probs": []} for i in range(n)]


def test_fill_needs_10_rounds_and_uses_measured_numbers():
    assert rl.fill(None, door(), rounds(9)) == (None, [])                   # 跑流程的几轮不算数
    lst, done = rl.fill(None, door(), rounds(20), project="门禁")
    assert set(done) == {"读卡号", "服务器校验", "电磁锁动作", "开门线"}
    g = lst["segments"]["check"]
    assert g["range"][0] <= 0.4 and g["range"][1] >= 0.419 and g["slow"] >= g["range"][1] and "实测 20 轮" in g["note"]
    assert rl.loads(rl.dumps(lst)) == lst                                     # 写出去的能原样读回来


def test_fill_only_fills_what_is_empty():
    lst, _ = rl.fill(None, door(), rounds(20))
    rl.edit_segment(lst, "check", [0.2, 0.3], 0.35, "厂家说 0.3 秒内")
    lst["segments"]["lock"].update(range=None, slow=None)                     # 有人把这段清空了
    lst, done = rl.fill(lst, door(), rounds(20, check=0.5))
    assert done == ["电磁锁动作"] and lst["segments"]["check"]["range"] == [0.2, 0.3]   # 有数的不动


def test_backfill_never_negative():
    rng, slow = rl.backfill([-0.023, 0.026, 0.0695])                          # 两个计时点几乎同时，个别轮先后反了
    assert rng[0] == 0.0 and rng[1] >= 0.0695 and slow >= rng[1]


def test_apply_and_judge_three_verdicts():
    lst, _ = rl.fill(None, door(), rounds(20))
    rl.edit_segment(lst, "read", [0.01, 0.04], 0.08)                          # 0.05 偏慢
    rl.edit_segment(lst, "lock", [0.01, 0.05], 0.08)                          # 0.1 超过慢阈值
    rl.edit_line(lst, "开门线", None, None, 0.5, "")                          # 体验标准用户设定 0.5 秒
    j = J.judge(rl.apply(door(), lst), rounds(20))
    v = {g["k"]: g["verdict"] for g in j["segments"]}
    assert v == {"read": "偏慢", "check": "正常", "lock": "超过慢阈值"}
    line = j["lines"][0]
    assert line["exp"] == "差" and line["exp_std"]["fair"] == 0.5 and line["exp_std"]["source"] == "用户设定"
    assert j["range_list"]["file"] == rl.FILENAME


def test_apply_matches_by_points_when_keys_differ():
    lst, _ = rl.fill(None, door(), rounds(20))
    sc = door()
    sc["segments"]["P2-P3"] = sc["segments"].pop("check")                     # 这次分段编号不一样，计时点一样
    assert rl.apply(sc, lst)["segments"]["P2-P3"]["range"] == lst["segments"]["check"]["range"]


@pytest.mark.parametrize("rng,slow,msg", [
    ([0.3, 0.2], 0.5, "写反"), ([0.1, 0.3], 0.2, "比合理范围上限"), (None, 0.5, "没有合理范围"), ([-0.1, 0.3], 0.5, "负数"),
])
def test_edit_checks_numbers(rng, slow, msg):
    lst, _ = rl.fill(None, door(), rounds(20))
    with pytest.raises(rl.ListRuleError, match=msg):
        rl.edit_segment(lst, "read", rng, slow)


def test_broken_file_says_which_line(tmp_path):
    lst, _ = rl.fill(None, door(), rounds(20), project="门禁")
    text = rl.dumps(lst).replace("| read | 读卡号 | P1→P2 |", "| read | 读卡号 | P1→P2 | 多一列 |")
    p = rl.path_of(tmp_path)
    p.write_text(text.replace("～", "到", 1), encoding="utf-8")
    with pytest.raises(rl.ListFormatError) as e:
        rl.read(tmp_path)
    assert all(n > 0 for n, _ in e.value.errors) and "第" in str(e.value)


def test_editing_numbers_replaces_the_auto_fill_note():
    lst, _ = rl.fill(None, door(), rounds(20))
    auto = lst["segments"]["read"]["note"]
    rl.edit_segment(lst, "read", [0.01, 0.04], 0.08, auto)                 # 网页上备注框带着原来的字，没改
    assert lst["segments"]["read"]["note"] == "手动改过"
    rl.edit_segment(lst, "check", lst["segments"]["check"]["range"], lst["segments"]["check"]["slow"], "厂家说的")
    assert lst["segments"]["check"]["note"] == "厂家说的"                     # 只改备注照写
