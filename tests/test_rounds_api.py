"""任务 19：真流程的多轮实测页不再借演示数据的说法。每轮做什么、清单、提示语按你写的"测什么"选模板；
线和问题种类按这次真的场景（后台多轮实测状态里带上）。"""

import json

import pytest
from fastapi.testclient import TestClient

from fsw.web.app import create_app
from tests.test_advise_api import real_log
from tests.test_collect_api import FakeSerialDevice, wait
from tests.test_standards import PAGE
from tests.test_web import SAY20


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", idf_finder=lambda: [],
                                 cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/x"],
                                 device=FakeSerialDevice(real_log()), wait_step=0.01)), tmp_path


def test_talk_measurement_gets_the_fixed_20_sentences(client):
    c, _ = client
    t = c.get("/api/rounds", params={"what": "从我说完话到设备回话", "start": "你说完"}).json()
    assert t["items"] == SAY20 and "固定清单" in t["itemsFrom"] and t["act"] == "说话"


def test_other_measurement_uses_your_own_start_words(client):
    c, _ = client
    t = c.get("/api/rounds", params={"what": "从刷卡到开门", "start": "刷卡"}).json()
    assert t["cnt"] == "刷卡" and t["items"] == ["刷卡"] and t["act"] == "做" and "说" not in t["who"]
    assert "麦克风" not in t["beepNote"]


def test_collect_status_carries_the_real_lines_and_problem_names(client):
    c, tmp_path = client
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 20, "project_dir": str(tmp_path / "p")})
    j = wait(c, lambda j: j["status"] == "done")
    assert [l["name"] for l in j["lines"]] == ["表情这条", "开口这条"]
    assert j["lines"][1]["range"] == [0.7, 2.75]
    assert {"key": "rev", "name": "表情比开口还晚", "level": "warn"} in j["problems"]
