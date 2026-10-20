"""app.js 整个文件能被 node 解析：pytest 只抠函数跑，整份文件有语法错（比如删函数漏删一行）时网页会打不开，这里先拦下（2026-10-20）。"""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "fsw/web/static/app.js"


@pytest.mark.skipif(not shutil.which("node"), reason="没有 node")
def test_app_js_parses():
    r = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
