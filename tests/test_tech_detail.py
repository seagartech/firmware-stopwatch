"""技术细节页（10-21 用户定）：不写计时点编号、每轮慢在哪段由码表算、代码位置带上下文。"""
import json

from fsw import advise as advs
from fsw import judge as jdg


def test_point_codes_in_ai_text_become_names():
    """AI 在技术细节里常写 P7-P8、P8：统一换成段名、点名，换别的设备一样（不认具体名字）。"""
    sc = {"segments": {"P7-P8": {"name": "等第一包回复音频到达"}}, "point_names": {"P2": "收到识别结果", "P4": "收到表情指令", "P8": "收到首包音频"}}
    f = {"why": "x", "dev": {"data": "P7-P8 一般 1.16 s；P2→P4 约 0.79", "cause": "P8 记在回调入口", "change": "", "size": "", "rk": "", "verify": ""},
         "seg": "P7-P8", "when": ["main"]}
    opts = [{"name": "查 P7→P8", "how": "看 P8"}]
    advs._named(f, opts, sc)
    assert f["dev"]["data"].startswith("「等第一包回复音频到达」") and "「收到识别结果」到「收到表情指令」" in f["dev"]["data"]
    assert f["dev"]["cause"] == "「收到首包音频」记在回调入口" and opts[0]["name"] == "查「等第一包回复音频到达」"
    assert f["seg"] == "P7-P8" and f["when"] == ["main"]  # 编号字段码表自己要用，不动


def test_each_round_says_which_segment_made_it_slow():
    """第 4 轮整条线超标：多出来的时间算到比自己一般用时多得最多的那段，不让 AI 猜（10-21：AI 写错过"第 4、5 轮没有单独慢的段"）。"""
    sc = {"segments": {"a": {"name": "听写"}, "b": {"name": "合成"}},
          "lines": [{"name": "到出声", "segments": ["a", "b"]}]}
    rounds = [{"n": i, "ok": True, "segs": {"a": 0.6, "b": 1.1}, "ends": [1.7]} for i in range(1, 11)]
    rounds[3] = {"n": 4, "ok": True, "segs": {"a": 1.03, "b": 1.15}, "ends": [2.18]}
    out = jdg.judge(sc, rounds)
    c = next(x for x in out["lines"][0]["round_cause"] if x["n"] == 4)
    assert c["name"] == "听写" and round(c["more"], 2) == 0.43 and c["med"] == 0.6


def test_tech_page_has_no_point_codes_and_code_opens_in_its_own_row():
    from tests.test_boss_short import grab
    d = grab("dev")
    assert "（${s.code}）" not in d and "s.code}" not in d.replace("String(s.code", "")  # 段名后面不再带 (P1→P2)
    assert 'data-code="1"' in d and "codeRows(" in d and "测量条件" not in d and "计时点开销，" not in d
    t = grab("codeTog")
    assert "nextElementSibling" in t and "coderow" in t  # 代码在这段下面整行显示，不撑宽表格
    assert "if(!FX.length)return ''" in grab("fixDev") and "推算" in grab("fixDev")


def test_report_api_carries_code_context(tmp_path):
    """/api/report 带上每个计时点在原版源码里的位置、函数名和前后 3 行（导出后断网也能看）。"""
    from fsw.web import app as appmod
    src = tmp_path / "src" / "main"
    src.mkdir(parents=True)
    (src / "a.cc").write_text("\n".join(f"line{i}" for i in range(1, 21)), encoding="utf-8")
    pd = tmp_path / "proj"
    pd.mkdir()
    (pd / "project.json").write_text(json.dumps({"source": str(tmp_path / "src")}), encoding="utf-8")
    job = {"_project_dir": str(pd), "_page": {"steps": [{"p": "P2", "path": "main/a.cc", "l": 10, "func": "A::B（收消息）"},
                                                         {"p": "P3", "path": "../../etc/passwd", "l": 1}]}}
    ctx = appmod.code_ctx(job)
    assert ctx["P2"]["func"] == "A::B" and ctx["P2"]["ctx"][0] == [7, "line7"] and ctx["P2"]["ctx"][-1] == [13, "line13"]
    assert "P3" not in ctx  # 源码文件夹外面的不读
