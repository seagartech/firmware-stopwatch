"""10-26 8783（fdf81d5）识别：第 12 步（主循环里转发"播放放空"通知那一支，application.cc 第 217 行）标了最后结果，
不在码表找到的候选里，追问一次也没改过来，码表只提醒了一句"换不了，请留意"就带着往下走了，后来清零就放在了那里。
改成通用的：一轮结尾那一步（最后结果、全部放完）不在候选里、追问也没改过来——
别的步骤已经标了这个角色、位置核对过的：去掉它的这个标记，一轮的结尾按核对过的那一步；没有：换成候选里离它最近的（同一个文件优先）。
重放存下的回答（不调用大模型），源码用 src/。"""

import json
from pathlib import Path

from fsw import identify as idf

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"
IDENT = json.loads((HERE / "identify-fdf81d5.json").read_text(encoding="utf-8"))


# ---------- 3. 识别时结尾那一步不在候选里、追问也没改过来 ----------
def test_unfixable_end_step_drops_its_end_role_when_a_checked_one_exists():
    flow, bad = idf.check_chain(SRC, json.loads(json.dumps(IDENT["chain"])), IDENT["category"], fresh=True, apply=True,
                                cands=IDENT["candidates"])
    assert bad == []
    c12 = next(c for c in flow["chain"] if c["n"] == 12)
    assert c12["role"] == []
    assert any("去掉第 12 步的最后结果" in r for r in flow["repairs"])
    assert not any("码表也换不了" in t for t in flow["tips"])
    assert "最后结果" in next(c for c in flow["chain"] if c["n"] == 10)["role"]


def test_unfixable_end_step_moves_to_the_nearest_candidate_when_nothing_else_is_checked():
    chain = json.loads(json.dumps(IDENT["chain"]))
    for c in chain["chain"]:
        if c["n"] in (10, 11):
            c["role"] = [r for r in str(c.get("role") or "").replace("，", "、").split("、") if r and r not in ("最后结果",)]
            c["role"] = "、".join(c["role"])
    flow, _ = idf.check_chain(SRC, chain, IDENT["category"], fresh=True, apply=True, cands=IDENT["candidates"])
    c12 = next(c for c in flow["chain"] if c["n"] == 12)
    assert "最后结果" in c12["role"] and c12["line"] != 217   # 换成了候选（同一个文件里离得最近的）
    assert c12["file"] == "main/application.cc"
