"""一次测量测完存下收全的每一轮；"和上次比"、首页"以前测过的"读这份，不重读原始日志重算（10-19：重算时认不出
试一次带过来的那一轮，22:01 那次 10 轮算成 9 轮，22:29 就跳过它去和 21:09 比了）。"""

import json
import shutil
import time

from fastapi.testclient import TestClient

from fsw import measure as meas
from fsw import standards as stds
from fsw.web.app import create_app
from tests.test_advise_api import real_log
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_standards import PAGE


def door_spec():
    sc = {"format": "fsw-scenario/1", "name": "门禁", "start": {"point": "P1", "name": "刷卡"},
          "segments": {"P1-P2": {"name": "读卡号并校验", "from": "P1", "to": "P2"}, "P2-P3": {"name": "电磁锁动作", "from": "P2", "to": "P3"}},
          "lines": [{"name": "开门线", "end": "开门", "segments": ["P1-P2", "P2-P3"]}], "experiences": [], "problems": []}
    return meas.Spec.from_scenario(sc)


def door_log(n, first_carried=False):
    out = []
    for r in range(n):
        b = 10_000_000 * (r + 1)
        if not (first_carried and r == 0):        # 试一次带过来的那一轮：结束标记在换调试口时过去了，日志里没有
            out.append(f"FSW,RESET,{b},0")
        out += [f"FSW,P1,{b + 100_000},0", f"FSW,P2,{b + 700_000},0", f"FSW,P3,{b + 900_000},0"]
    return out


def test_saved_rounds_are_read_back_instead_of_recounting(tmp_path):
    (tmp_path / "raw.log").write_text("\n".join(door_log(10, first_carried=True)), encoding="utf-8")
    spec = door_spec()
    recount = meas.run_rounds(tmp_path, spec)                                  # 没存：退回重算（老数据）
    assert recount and all(r["ok"] for r in recount)
    measured = [{"ok": True, "n": i + 1, "segs": {"P1-P2": 0.6, "P2-P3": 0.2}, "ends": [0.8]} for i in range(10)]
    meas.save_rounds(tmp_path, measured + [{"ok": False, "n": 11, "segs": {}, "ends": []}])  # 没收全的不存
    back = meas.run_rounds(tmp_path, spec)
    assert len(back) == 10 and back[0] == {"n": 1, "segs": {"P1-P2": 0.6, "P2-P3": 0.2}, "ends": [0.8], "ok": True}
    (tmp_path / meas.ROUNDS_FILE).write_text("坏了", encoding="utf-8")         # 文件坏了：退回重算，不出错
    assert meas.run_rounds(tmp_path, spec) == recount


def _app(tmp_path, std_dir):
    return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                 cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.usbmodem1101"],
                                 device=FakeSerialDevice(real_log()), wait_step=0.01, opener=lambda *a: None))


def test_compare_with_last_run_uses_what_it_saved(tmp_path, monkeypatch):
    std_dir = tmp_path / "standards"
    shutil.copytree(stds.HERE, std_dir)
    monkeypatch.setattr(stds, "HERE", std_dir)
    proj = tmp_path / "data" / "xiaozhi-1"
    c = _app(tmp_path, std_dir)
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(proj)})
    first = wait(c, lambda j: j["status"] == "done")
    c.get("/api/report")
    run1 = proj / "runs" / sorted(x.name for x in (proj / "runs").iterdir())[0]
    saved = json.loads((run1 / meas.ROUNDS_FILE).read_text(encoding="utf-8"))["rounds"]
    assert len(saved) == len([r for r in first["rounds"] if r["ok"]]) >= 10
    (run1 / "raw.log").write_text("\n".join(real_log()[:5]), encoding="utf-8")  # 原始日志重算只剩不到 10 轮
    time.sleep(1.1)                                                            # 第二次的结果文件夹按秒起名
    c2 = _app(tmp_path, std_dir)
    c2.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(proj)})
    wait(c2, lambda j: j["status"] == "done")
    r2 = c2.get("/api/report").json()
    prev = r2["prev"]
    assert prev["run"] == run1.name and prev["n"] == len(saved)               # 照样和上一次比，轮数是那次测的轮数
    # 合理范围清单去掉了（10-19）：第二次起每段、每条线和上次比，同一份日志测两次就是持平
    assert {g["verdict"] for g in r2["segments"]} <= {"持平", "看回答长短"} and not r2["scenario"]["first_run"]
    assert all("持平" in x["why"] or x["why"] == "取决于回答长短，不判快慢" for x in r2["labels"] if x["kind"] == "line")
