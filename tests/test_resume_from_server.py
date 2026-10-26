"""10-25 真机：流程按接口走完（没有网页存过页面进度），新开网页停在首页、像回到了老界面，只好从头再走一遍。
后台自己报出流程走到哪一步（/api/ui 的 server），网页没有自己的那份（或是别的源码）时按它接上。只用假模型。"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fsw.llm import FakeLLM
from tests.identify_fixtures import DOOR_REPLY
from tests.test_identify_api import make, prepared, wait  # noqa: F401

JS = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")


def test_server_reports_flow_progress_without_any_page_saved(make, tmp_path):
    c, _ = make(FakeLLM(DOOR_REPLY))
    assert c.get("/api/ui").json()["server"] is None                     # 还没选源码
    src = prepared(c, tmp_path)
    s = c.get("/api/ui").json()["server"]
    assert s["stage"] == 0 and s["page"] == "home" and s["REAL"]["work"] == src["work"]
    c.post("/api/identify/start", json={"work": src["work"], "project_dir": src["project_dir"],
                                        "fingerprint": src["fingerprint"], "what": "从刷卡到开门"})
    wait(c)
    u = c.get("/api/ui").json()
    assert u["ui"] is None                                               # 网页一次都没存过
    s = u["server"]
    assert s["stage"] == 1 and s["page"] == "steps" and s["what"] == "从刷卡到开门"
    assert s["REAL"]["project_dir"] == src["project_dir"] and s["hSrc"] and s["REAL"]["label"] == s["hSrc"]
    assert c.get("/api/ui?key=door").json()["server"] is None            # 演示数据不掺和


def test_page_uses_server_progress_when_it_has_none_of_its_own():
    body = JS[JS.index("async function uiRestore(){"):JS.index("async function uiRestore(){") + 6000]
    assert "U.server" in body and "srvSnap(S,u)" in body
    assert "&&!own" in body                                              # 这个标签页自己存的那份（刷新）照旧优先
    assert "FRESH&&!fromSrv" in body                                     # 新开网页、流程不是从网页走的：也接上（首页给「接着看」）


@pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
def test_server_snapshot_has_the_fields_the_page_restores():
    fn = re.search(r"function srvSnap\(S,old\)\{.*?\n.*?\n.*?\n.*?autoT0:[^}]*\}\}", JS, re.S).group(0)
    S = {"work": "/w", "stage": 2, "page": "flash", "what": "从刷卡到开门", "fsw": "failed", "hSrc": "/src", "pName": "门禁",
         "hBoardQ": "b", "bg": True, "REAL": {"work": "/w", "project_dir": "/p"}}
    js = f"const SCENES={{door:1}};let scene='door';{fn};console.log(JSON.stringify(srvSnap({json.dumps(S)},null)))"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out["v"] == 1 and out["scene"] == "door" and out["flow"] == "first" and out["flowMax"] == 2 and out["page"] == "flash"
    assert out["hSay"] == "从刷卡到开门" and out["REAL"]["work"] == "/w" and out["bgSrc"] == "/src" and out["diffOk"] is True
    assert out["auto"] is True and out["autoHold"] == ""                 # 按后台接上的也是测量页（10-26）
    snap_keys = re.search(r"return \{v:1,scene,page,(.*?)\}\}", JS).group(1)
    for k in ["mode", "flow", "flowMax", "hStage", "hSrc", "hSay", "pName", "hBoardQ", "REAL", "bgSrc", "dropped", "added", "ed"]:
        assert k in snap_keys and k in out                                # 和 uiSnap 存的同样字段
