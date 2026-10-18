"""补上"吞字"：问题规则要看的计时点顺带记附加值（比如第一包声音到达时设备的状态），码表才判得了回答开头被吞。

模拟日志由验证 07 的日志改写成任务 14 真插的计时点编号（P1～P9），第 4、11、17 轮第一包声音到达时设备还在聆听（5），
其余是说话中（6）。全用假模型、假设备。
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw import advise as adv
from fsw import standards as stds
from fsw.llm import FakeLLM
from fsw.web.app import create_app
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_fsw_api import fake_build_output
from tests.test_instrument import NEED, PLAN, STEPS, plan_with_value, reply
from tests.identify_fixtures import door_source
from tests.test_standards import PAGE


@pytest.fixture(autouse=True)
def _xiaozhi_standard(monkeypatch):
    """这些测试按"用户放进了小智那份标准"来跑：10-18 起码表不自带任何设备的标准，小智那份只当测试素材。"""
    from fsw import standards as _stds
    monkeypatch.setattr(_stds, "HERE", Path(__file__).parent / "fixtures")


V07 = Path(__file__).resolve().parent / "fixtures/v07/mock_log.txt"
# 验证 07 的计时点（标准里的编号）→ 任务 14 真插的编号；真插里没有的（P3、P6、P8）丢掉
TO_REAL = {"P2": "P1", "P4": "P2", "P11": "P3", "P12": "P4", "P5": "P5", "P13": "P6", "P7": "P7", "P9": "P8", "P10": "P9"}
VALUES = {"P7": {"expr": "(int)GetDeviceState()", "expect": 6, "what": "设备当时的状态"}}


def real_log_with_values(keep_value=True):
    out = []
    for line in V07.read_text(encoding="utf-8").splitlines():
        if not line.startswith("FSW,"):
            out.append(line)
            continue
        _, p, us, v = line.split(",")
        if p == "RESET":
            out.append(line)
        elif p in TO_REAL:
            # 只有第一包声音到达那个点记设备状态；其他点任务 14 插的都是 0（P1 的附加值是"发生了几次"，留着）
            val = v if (p == "P7" and keep_value) or p == "P2" else "0"
            out.append(f"FSW,{TO_REAL[p]},{us},{val}")
    return out


def test_rewritten_log_has_the_device_state_on_the_first_audio_packet():
    log = real_log_with_values()
    states = [l.split(",")[3] for l in log if l.startswith("FSW,P7,")]
    assert len(states) == 20 and [i + 1 for i, s in enumerate(states) if s != "6"] == [4, 11, 17]


@pytest.fixture
def app(tmp_path):
    def _app(lines, values):
        proj = tmp_path / "data" / "xiaozhi-1"
        if values is not None:  # 插入记录：码表插计时点时存的，里面写了哪些计时点记了附加值
            (proj / "fsw-backup").mkdir(parents=True)
            (proj / "fsw-backup" / "record.json").write_text(json.dumps({"values": values, "steps": {}, "files": []}))
        c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                  cc_status=lambda: {"found": True, "logged_in": True},
                                  port_finder=lambda: ["/dev/cu.usbmodem1101"], device=FakeSerialDevice(lines),
                                  wait_step=0.01))
        c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(proj)})
        wait(c, lambda j: j["status"] == "done")
        return c
    return _app


def test_swallowed_rounds_found_when_the_state_is_recorded(app):
    r = app(real_log_with_values(), VALUES).get("/api/report").json()
    sw = next(x for x in r["labels"] if x["k"] == "problem:sw")
    assert sw["rounds"] == [4, 11, 17] and sw["level"] == "必须改" and sw["name"] == "回答开头被吞"
    assert [x["n"] for x in r["rounds"] if "sw" in x["probs"]] == [4, 11, 17]
    keys = [p["key"] for p in adv.problems(r, r["scenario"])]
    assert "problem:sw" in keys  # 为什么、怎么改：这个问题也要写卡片


def test_without_recorded_state_no_swallow_judged(app):
    r = app(real_log_with_values(keep_value=False), None).get("/api/report").json()
    assert not any(x["k"] == "problem:sw" for x in r["labels"])  # 没记附加值：不判，也不会误报


def test_report_keeps_the_value_rules(app):
    c = app(real_log_with_values(), VALUES)
    r = c.get("/api/report").json()
    assert next(x for x in r["labels"] if x["k"] == "problem:sw")["rounds"] == [4, 11, 17]


def test_insert_asks_ai_for_the_value_and_records_it(tmp_path, monkeypatch):
    """插计时点时：标准要附加值的计时点，提示里写上；AI 记上了就进插入记录。"""
    monkeypatch.setattr(stds, "needed_values", lambda steps, standard: NEED)
    made = []

    def factory(p, k):
        made.append(FakeLLM(reply(plan_with_value("(int)g_reader.mode", [{"step": "P2", "expect": 1}]))))
        return made[-1]

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=factory,
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: [],
                              bg_builder=builder, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": STEPS, "chip": "ESP32-S3"})
    from tests.test_fsw_api import wait as fwait
    j = fwait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "inserted", j
    assert "读卡器当时的模式" in made[-1].prompts[-1]
    rec = json.loads((Path(r["project_dir"]) / "fsw-backup" / "record.json").read_text(encoding="utf-8"))
    assert rec["values"]["P2"]["expect"] == 1
    assert any("读卡器当时的模式" in p["n"] for p in j["view"]["places"])


@pytest.mark.parametrize("demo", ["door", "dialog"])
def test_other_demo_sets_unaffected(demo):
    from tests.test_advise import demo as load, demo_rounds
    from fsw import judge as J
    d = load(demo)
    j = J.judge(d["scenario"], demo_rounds(d))
    assert j["labels"]  # 照常出标签
