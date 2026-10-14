import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fsw.web.app import create_app

WEB = Path(__file__).resolve().parent.parent / "fsw" / "web"


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css"])
def test_page_and_its_files_open(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert len(r.text) > 100


def test_page_has_only_name_home_and_settings_on_top(client):
    html = client.get("/").text
    top = re.search(r'<header class="apphd".*?</header>', html, re.S).group(0)
    assert "固件码表" in top
    assert "首页" in top and "设置" in top
    assert top.count("<button") == 3


def test_every_page_the_flow_needs_has_a_renderer():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    for page in ["home", "settings", "diff", "identify", "probes", "measure", "report", "compare"]:
        assert re.search(rf"\b{page}\s*:\s*\(?", js), f"app.js 里没有 {page} 页"


def test_two_demo_scenarios_are_listed(client):
    demos = client.get("/api/demo").json()
    assert {d["id"]: d["label"] for d in demos} == {"voice": "语音对话", "door": "门禁刷卡"}


@pytest.mark.parametrize("sid", ["voice", "door"])
def test_demo_data_fits_the_common_shape(client, sid):
    d = client.get(f"/api/demo/{sid}").json()
    assert d["start"]["name"]
    assert 1 <= len(d["lines"]) <= 4
    for line in d["lines"]:
        assert line["end"] and line["segments"]
        for key in line["segments"]:
            seg = d["segments"][key]
            assert seg["name"] and seg["owner"]
            assert len(seg["range"]) == 2
            assert len(seg["values"]) == 20


def test_unknown_demo_is_404(client):
    assert client.get("/api/demo/nope").status_code == 404


def test_flow_api_follows_the_step_rules(client):
    s = client.post("/api/flow/start", json={"kind": "first", "demo": "door"}).json()
    assert s["demo"] == "door"
    assert s["viewing"] == "identify"

    s = client.post("/api/flow/complete", json={"step": "identify"}).json()
    s = client.post("/api/flow/view", json={"step": "identify"}).json()
    assert [x["status"] for x in s["steps"]] == ["done", "doing", "todo", "todo"]
    assert s["current"] == "probes"

    s = client.post("/api/flow/change", json={"step": "identify"}).json()
    assert s["notice"]
    assert [x["status"] for x in s["steps"]] == ["doing", "todo", "todo", "todo"]

    s = client.post("/api/flow/fail", json={"step": "identify", "message": "大模型没回应"}).json()
    assert s["steps"][0]["status"] == "error"
    assert s["error"] == "大模型没回应"


def test_flow_api_refuses_invalid_moves_in_plain_words(client):
    client.post("/api/flow/start", json={"kind": "first", "demo": "voice"})
    r = client.post("/api/flow/view", json={"step": "report"})
    assert r.status_code == 409
    assert "还没做到" in r.json()["detail"]


def test_state_without_flow_is_empty(client):
    assert client.get("/api/state").json()["flow"] is None


def test_device_output_streams_live(client):
    with client.stream("GET", "/api/device/stream?demo=door&rounds=2&interval=0") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    lines = [l[6:] for l in body.splitlines() if l.startswith("data: ")]
    assert lines.count("FSW,RESET") == 2
    assert any(re.fullmatch(r"FSW,P\d+,\d+,\d+", l) for l in lines)
    assert lines[-1] == "END"


SCENE_WORDS = re.compile(r"小灵|小智|xiaozhi|CoreS3|门禁|刷卡|door|voice-demo|语音对话")


def test_code_has_no_scenario_specific_names():
    files = [p for p in WEB.rglob("*") if p.suffix in {".py", ".js", ".html", ".css"}]
    hits = [
        f"{p.name}: {m.group(0)}"
        for p in files
        for m in SCENE_WORDS.finditer(p.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_device_output_can_continue_from_a_later_round(client):
    def first_round(start):
        with client.stream("GET", f"/api/device/stream?demo=voice&rounds=1&start={start}&interval=0") as r:
            body = "".join(r.iter_text())
        return [l[6:] for l in body.splitlines() if l.startswith("data: FSW,P")]

    assert first_round(5) != first_round(0)
    assert first_round(5)[-1].split(",")[2] != first_round(0)[-1].split(",")[2]
