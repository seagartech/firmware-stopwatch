"""同一个人做同一件事出现在两张卡片里（2026-10-20 真测：「思考中」表情两张卡片都有）：待办只算一件，标上解决哪几个问题。"""

import json
import shutil
import subprocess

import pytest

from tests.test_timeline_tip import grab

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")


def test_same_option_in_two_cards_counts_once():
    FX = [{"opts": [{"who": "服务器方", "name": "查合成"}, {"who": "固件研发", "name": "先换思考中表情"}]},
          {"opts": [{"who": "服务器方", "name": "查听写"}, {"who": "固件研发", "name": "先换思考中表情"}]}]
    js = (f"let fixPick=new Set(['0-0','0-1','1-0','1-1']);{grab('pickedOpts')}\n{grab('pNos')}\n"
          f"const r=pickedOpts({json.dumps(FX, ensure_ascii=False)});"
          "console.log(JSON.stringify(r.map(o=>[o.who,o.name,pNos(o)])))")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out == [["服务器方", "查合成", "问题 1"], ["固件研发", "先换思考中表情", "问题 1、2"], ["服务器方", "查听写", "问题 2"]]
