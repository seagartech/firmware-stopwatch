"""10-26 凌晨 8772：AI 把"打印最后一次"（FSW_FLUSH_LAST(6)、(10)）插在 audio_service.cc 第 379 行后面——那一行是函数的结束 }，
两句落在函数外面，编译不过；报错指到码表的头文件第 144 行，看不出是哪一处。

改成（都看结构，不认文件名、函数名）：
1. 每一条插进去的计时点语句（点、清零、武装、打印最后一次）都要在函数（或 lambda）里面；在外面就去掉，会缺东西就追问 AI 一次；
   大括号对不上（宏、#if）说不准的不拦。
2. 码表放了每轮清零时，AI 写的"打印最后一次"不用：清零前设备会把这一轮还没打印的"最后一次"补打一行（FSW,LATE），码表本来就认。
3. 编译报错指到头文件时，按"在哪里展开的这个宏"找回插进去的那一句，页面上说清楚，重插时告诉 AI。
"""

import json
import shutil
from pathlib import Path

from fsw import instrument as ins
from fsw import structure
from fsw.llm import FakeLLM
from fsw.platforms.espidf import pick_errors
from tests.test_fsw_api import fake_build_output, setup, start_insert, wait  # noqa: F401

HERE = Path(__file__).parent / "fixtures" / "fullflow_multi_sentence"
SRC = HERE / "src"
BAD = "insert-reply-6afa2c1.txt"


def steps():
    return json.loads((HERE / "insert-steps-6e9c22a.json").read_text(encoding="utf-8"))


def text(name=BAD):
    return (HERE / name).read_text(encoding="utf-8")


def test_8772_plan_drops_model_print_last_and_keeps_tool_reset():
    r = ins.parse_plan(text(), SRC, steps())
    assert r["ok"]
    assert not any("FSW_FLUSH_LAST" in l for x in r["inserts"] for l in x["lines"])
    d = [f for f in r["fixes"] if f["kind"] == "dropped_by_tool"]
    assert [(f["step"], f["after_line"]) for f in d] == [("flush", 379)]
    assert [x["after_line"] for x in r["inserts"] for l in x["lines"] if "FSW_ROUND_RESET" in l] == [1027]


def test_every_inserted_statement_of_the_replayed_plans_is_inside_a_function(tmp_path):
    for name in (BAD, "insert-reply-00ab8bc.txt", "insert-reply-1742b64.txt", "insert-reply-0a026ac.txt"):
        work = tmp_path / name
        shutil.copytree(SRC, work)
        r = ins.parse_plan(text(name), work, steps())
        assert r["ok"], name
        rec = ins.apply(work, r, tmp_path / (name + "-bk"))
        assert rec["ok"], rec.get("message")
        for pl in rec["places"]:
            src = structure.Project(work).file(pl["file"])
            for ln, body in zip(pl["new_lines"], pl["lines"]):
                if body.strip().startswith("FSW_"):
                    at = src.starts[ln - 1]
                    assert any(src.blocks[i]["kind"] in ("function", "lambda") for i in src.chain(at)), (name, pl)


def test_good_8767_plan_still_passes():
    d = json.loads(text("insert-plan-4e1e8da.json"))
    r = ins.parse_plan(d["reply"], SRC, d["steps"])
    assert r["ok"] and not any(f["kind"] == "dropped" for f in r["fixes"])


def test_without_tool_reset_print_last_outside_function_is_asked_once(monkeypatch):
    monkeypatch.setattr(ins, "place_reset", lambda *a, **k: None)
    r = ins.parse_plan(text(), SRC, steps())
    assert not r["ok"] and r["bad"][0]["step"] == "flush" and "落在函数外面" in r["bad"][0]["why"]
    assert any("打印它的那一句被去掉了" in x for x in r["lost"])
    good = text().replace('"after_line": 379', '"after_line": 375')
    llm = FakeLLM("", followups=[good])
    plan, again = ins.fix_plan(llm, text(), SRC, steps())
    assert plan["ok"] and len(llm.asked) == 1 and "第 379 行" in llm.asked[0] and "函数外面" in llm.asked[0]
    plan, _ = ins.fix_plan(FakeLLM("", followups=[text()]), text(), SRC, steps())
    assert not plan["ok"] and "码表追问它改了一次还是不对" in plan["message"]


def _proj(tmp_path, body):
    (tmp_path / "main").mkdir(exist_ok=True)
    (tmp_path / "main" / "a.c").write_text(body, encoding="utf-8")


def _reply(inserts):
    return "```json\n" + json.dumps({"header": "main/fw_stopwatch.h", "inserts": [
        {"step": "include", "file": "main/a.c", "after_line": 1, "lines": ['#include "fw_stopwatch.h"']}, *inserts]}) + "\n```"


ST = [{"p": "P1", "n": "开始", "path": "main/a.c", "l": 3, "where": "before", "once": "first", "line": "start"},
      {"p": "P2", "n": "做完", "path": "main/a.c", "l": 7, "where": "before", "once": "first", "line": "talk"}]
C = "#include <stdio.h>\nvoid a(void) {\n    begin();\n}\n\nvoid b(void) {\n    finish();\n}\n"


def test_extra_helper_outside_a_function_is_just_dropped(tmp_path):
    _proj(tmp_path, C)
    r = ins.parse_plan(_reply([{"step": "P1", "file": "main/a.c", "after_line": 2, "lines": ["    FSW_MARK_ONCE(1, 0);"]},
                               {"step": "P2", "file": "main/a.c", "after_line": 6, "lines": ["    FSW_MARK_ONCE(2, 0);"]},
                               {"step": "arm", "file": "main/a.c", "after_line": 4, "lines": ["FSW_ARM();"]}]), tmp_path, ST)
    assert r["ok"] and [(f["kind"], f["step"], f["after_line"]) for f in r["fixes"]] == [("dropped", "arm", 4)]


def test_point_outside_a_function_is_not_used(tmp_path):
    _proj(tmp_path, C)
    r = ins.parse_plan(_reply([{"step": "P1", "file": "main/a.c", "after_line": 2, "lines": ["    FSW_MARK_ONCE(1, 0);"]},
                               {"step": "P2", "file": "main/a.c", "after_line": 8, "lines": ["FSW_MARK_ONCE(2, 0);"]}]), tmp_path, ST)
    assert not r["ok"] and r["bad"][0]["step"] == "P2" and "函数外面" in r["bad"][0]["why"]


def test_unbalanced_braces_are_let_through(tmp_path):
    """#if 两边各开一个 { ：数不清，不拦。"""
    _proj(tmp_path, "#include <stdio.h>\n#if X\nvoid a(void) {\n#else\nvoid a(int y) {\n#endif\n    begin();\n}\nvoid b(void) {\n    finish();\n}\n")
    st = [{**ST[0], "l": 7}, {**ST[1], "l": 10}]
    r = ins.parse_plan(_reply([{"step": "P1", "file": "main/a.c", "after_line": 6, "lines": ["    FSW_MARK_ONCE(1, 0);"]},
                               {"step": "P2", "file": "main/a.c", "after_line": 9, "lines": ["    FSW_MARK_ONCE(2, 0);"]}]), tmp_path, st)
    assert r["ok"]


def test_lambda_body_counts_as_inside(tmp_path):
    _proj(tmp_path, "#include <stdio.h>\nvoid a(void) {\n    begin();\n}\nauto cb = [](int x) {\n    finish();\n};\n")
    st = [ST[0], {**ST[1], "l": 6}]
    r = ins.parse_plan(_reply([{"step": "P1", "file": "main/a.c", "after_line": 2, "lines": ["    FSW_MARK_ONCE(1, 0);"]},
                               {"step": "P2", "file": "main/a.c", "after_line": 5, "lines": ["    FSW_MARK_ONCE(2, 0);"]}]), tmp_path, st)
    assert r["ok"] and r["fixes"] == []


def test_build_error_in_header_is_traced_to_the_inserted_line(tmp_path):
    """8772 的原话：报错在 fw_stopwatch.h:144，"在哪里展开的宏"指到 audio_service.cc:389——就是 AI 插在第 379 行后面的打印最后一次。"""
    work = tmp_path / "src"
    shutil.copytree(SRC, work)
    raw = ins._json_block(text())
    rec = ins.apply(work, {"header": raw["header"], "inserts": raw["inserts"], "notes": [], "values": {}, "start": 1}, tmp_path / "bk")
    assert rec["ok"]
    err = (HERE / "build-error-6afa2c1.txt").read_text(encoding="utf-8").replace("{ROOT}", str(work.resolve()))
    picked = "\n".join(pick_errors(err.splitlines()))
    assert "in expansion of macro" in picked and "fw_stopwatch.h:144:42: error" in picked
    pl = ins.error_place(picked, rec, work)
    assert pl and pl["step"] == "flush" and pl["file"] == "main/audio/audio_service.cc" and pl["at"] == 379 and pl["line"] == 389
    t = ins.error_text(pl, steps())
    assert "「打印最后一次」那一句" in t and "main/audio/audio_service.cc 第 379 行后面" in t
    assert ins.locate_error(picked, rec, work) is None  # 辅助语句：不是哪个计时点


def test_page_names_the_line_when_build_error_is_in_the_header_and_reinsert_tells_the_ai(setup, monkeypatch):
    prompts = []
    orig = FakeLLM.investigate

    def spy(self, prompt, root, on_text=None, on_tool=None):
        prompts.append(prompt)
        return orig(self, prompt, root, on_text=on_text, on_tool=on_tool)
    monkeypatch.setattr(FakeLLM, "investigate", spy)
    state = {"n": 0}

    def builder(work, target, board, idf, progress):
        state["n"] += 1
        if state["n"] == 2:  # 编计时版：报错在头文件，"在哪里展开的宏"指到 P2 那一句
            lines = (Path(work) / "main/rfid_task.c").read_text().split("\n")
            k = next(i for i, l in enumerate(lines, 1) if "FSW_MARK_ONCE(2" in l)
            return {"ok": False, "seconds": 0.1, "error":
                    f"{work}/main/fw_stopwatch.h:144:42: error: expected constructor, destructor, or type conversion before '(' token\n"
                    f"{work}/main/rfid_task.c:{k}:5: note: in expansion of macro 'FSW_MARK_ONCE'"}
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c, r, *_ = setup(builder=builder)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("failed", "done"))
    assert j["status"] == "failed" and j["err"]["step"] == "P2"
    m = j["err"]["message"]
    assert "计时点 P2「卡号读完」" in m and "main/rfid_task.c 第 131 行后面" in m and "重新插入" in m
    n = len(prompts)
    start_insert(c, r)
    wait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert len(prompts) == n + 1 and "上一次插完试的时候发现的问题" in prompts[-1] and "计时点 P2「卡号读完」" in prompts[-1]
