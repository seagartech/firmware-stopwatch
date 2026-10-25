"""试一次：一轮有了起点、还没走到结束那一步就读到清零，清零后（下一个起点之前）又来了这一轮后面的点——
直接说"每轮清零放错了，在「某一步」之后被清零"，不只列缺哪几个点；重插时把这句话告诉 AI。
（8771：清零在每次换状态都走的地方，回复一开始就清零，后面四个点都算不进这一轮，只显示 6/10。）"""

from tests.test_fsw_api import FakeDevice, run_to_try, setup, wait  # noqa: F401
from tests.test_instrument import STEPS

STEPS_END = [*STEPS[:2], {**STEPS[2], "once": "once"}]  # 最后一步一轮只经过一次：它就是一轮的结束


class Lines(FakeDevice):
    def __init__(self, lines):
        super().__init__(lines)


def insert(c, r):
    c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": STEPS_END, "chip": "ESP32-S3"})
    return wait(c, lambda j: j["status"] in ("inserted", "failed"))


def test_reset_mid_round_is_named(setup):
    c, r, *_ = setup(device=Lines(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,RESET,30,0", "FSW,P5,3000040,0", "FSW,RESET,3000050,0"]))
    assert insert(c, r)["status"] == "inserted"
    j = run_to_try(c)
    assert j["status"] == "failed"
    m = j["err"]["message"]
    assert "每轮清零那一句放错了" in m and "在「卡号读完」之后就被清零" in m and "「发出开锁」" in m
    assert "重新插一次" in m


def test_incomplete_round_then_next_round_is_not_called_a_cut(setup):
    c, r, *_ = setup(device=Lines(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,RESET,100,0", "FSW,P1,110,1", "FSW,P5,130,0"]))
    assert insert(c, r)["status"] == "inserted"
    j = run_to_try(c)
    assert j["status"] == "failed" and "清零那一句放错了" not in j["err"]["message"]


def test_full_round_still_passes(setup):
    c, r, *_ = setup(device=Lines(["FSW,RESET,1,0", "FSW,P1,10,1", "FSW,P2,20,0", "FSW,P5,30,0", "FSW,RESET,50,0"]))
    assert insert(c, r)["status"] == "inserted"
    assert run_to_try(c)["status"] == "done"
