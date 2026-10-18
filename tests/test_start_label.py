"""用 Mac 录音核对码表（决策 135）：码表的起点是设备自己察觉的时刻（比如"设备听出你停了"），
比人真正做完晚（小智这台约 0.3 秒）。起点按计时点自己的名字叫，不再叫"你说完"；核对过的，报告写明人实际要早多少。"""
import json
from pathlib import Path

from tests.test_boss_short import JS, grab, node, run

SC = {"start": {"point": "P1"}}
P = {"measure": "你说完 → 设备开口", "steps": [{"p": "P1", "n": "设备听出你停了"}, {"p": "P9", "n": "喇叭出声"}]}


@node
def test_start_is_called_by_its_own_point_name():
    assert run(["startLabel"], f"startLabel({json.dumps(P)},{json.dumps(SC)})") == "设备听出你停了"
    assert run(["startLabel"], 'startLabel({"measure":"卡贴上去 → 门开"},{"start":{"point":"P1"}})') == "卡贴上去"  # 找不到计时点名字就用原来的


@node
def test_report_says_how_much_earlier_people_really_stop():
    lag = {"secs": 0.33, "how": "Mac 录音", "when": "2026-10-16", "rounds": 8}
    out = run(["htmlEsc", "startLagLine"], f'startLagLine("设备听出你停了",{json.dumps(lag)})')
    assert "设备听出你停了" in out and "约 0.3 秒" in out and "录音核对" in out
    out = run(["htmlEsc", "startLagLine"], 'startLagLine("读卡器读到卡",null)')
    assert "读卡器读到卡" in out and "没核对" in out


def test_report_uses_the_start_label_and_lag_line():
    assert "startLagLine(D.S.startShort" in JS and "startLabel(P,sc)" in JS
    assert "startLabel(P,COLJ&&COLJ.scenario||{start:{point:(P.main||[]).find(k=>!(P.steps||[]).some(x=>x.p===k&&x.opt))}})" in grab("realD")  # 多轮实测页"量几条线"也用这个名字


def test_beep_keeps_the_original_volume():
    assert "g.gain.value=.25" in grab("ding")  # 用户试听：调轻了太小，用原来的音量


def test_report_api_gives_start_lag_from_the_check(tmp_path):
    from tests.test_first_round_and_push import make
    from tests.test_collect_api import wait
    from tests.test_standards import PAGE
    lines = []
    for k in range(4):
        lines += [f"FSW,RESET,{k * 10_000_000 + 1_000_000},0"] + [f"FSW,P{i},{k * 10_000_000 + 3_000_000 + i * 100_000},0" for i in range(1, 10)]
    c = make(tmp_path, lines + ["FSW,RESET,99000000,0"])
    proj = tmp_path / "p"
    proj.mkdir()
    name = PAGE["steps"][0]["n"]
    (proj / "calib.json").write_text(json.dumps({"start_lag": {"point_name": name, "secs": 0.33, "how": "Mac 录音", "when": "2026-10-16", "rounds": 8}}, ensure_ascii=False))
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(proj)})
    wait(c, lambda j: j["status"] == "done")
    assert c.get("/api/report").json()["start_lag"]["secs"] == 0.33


def test_listen_button_reopens_the_sound_channel():
    """Safari 声音状态是开着、喇叭却没声音（2026-10-16 实测）：点"试听"先关掉重开声音通道（决策 136）。"""
    assert "actx.close()" in grab("resetAudio") and "actx=null" in grab("resetAudio")
    assert "[data-beeptest]')){resetAudio();unlockAudio();" in JS


def test_settings_say_what_to_do_when_the_beep_is_silent():
    """Safari 声音卡住（状态开着、没声音），退出重开就好了（2026-10-16 用户实测，决策 138）。"""
    assert "Command + Q" in JS
