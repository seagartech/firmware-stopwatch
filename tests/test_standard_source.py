"""体验标准写清出处和分量（决策 137）：1.5 秒来自一家语音 AI 测试公司的文章，不是国家或行业标准，报告不能写得像权威标准。
计时图上 1.5 秒那条竖线不能压住文字。"""
import json
from pathlib import Path

from tests.test_boss_short import JS, grab

STD = json.loads((Path(__file__).resolve().parents[1] / "fsw/standards/xiaozhi-c0fe42a.json").read_text(encoding="utf-8"))


def test_experience_source_says_what_it_is_and_is_not():
    src = STD["experience"]["source"]
    assert "Hamming AI" in src and "不是国家或行业标准" in src
    assert STD["experience"]["short"] == "参考值"


def test_chart_uses_the_short_tag_and_cards_give_the_full_source():
    assert "sc.experience.short||'参考值'" in JS
    assert "D.S.exp.source" in grab("lineCards")


def test_chart_labels_sit_on_a_background_so_the_line_does_not_cover_them():
    t = grab("timeline")
    assert t.count('halo"') >= 2  # 每条线终点的字、"早几秒"的字
