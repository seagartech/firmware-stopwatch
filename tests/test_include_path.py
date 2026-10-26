"""10-26 计时点头文件的 #include 写法（8787 真测：AI 写成 "main/fw_stopwatch.h"，四个文件都找不到头文件，页面却说"放的地方不对"）：
1. 插入时 #include 由码表按编译器找头文件的规则定：头文件所在目录是找头文件的起点（文件自己所在目录、组件或项目加的头文件目录）就只写文件名，
   否则写相对被插入文件的路径；AI 写的任何写法都换掉。
2. 插之前逐个文件核一遍这一句找得到头文件，找不到当场改。
3. 编译报"找不到码表的头文件"：归成头文件路径写错、列出所有文件，码表改成相对路径直接重编一次，不让点"重新插入"、不告诉 AI"放的地方不对"。"""

import json
import time
from pathlib import Path

import pytest

from fsw import instrument as ins
from tests.test_fsw_api import fake_build_output, setup, start_insert, wait  # noqa: F401（setup 是夹具）
from tests.test_instrument import PLAN, STEPS, reply
from tests.identify_fixtures import door_source

AI_WAYS = ['#include "main/fw_stopwatch.h"', '#include "../fw_stopwatch.h"', '#include "fw_stopwatch.h"',
           '#include <fw_stopwatch.h>', '#include <main/fw_stopwatch.h>']


def _idf_project(root):
    """乐鑫组件的样子：main 下好几层子目录，头文件目录用变量写（set + list(APPEND)），文件在 audio/engines、boards/x 里。"""
    root = Path(root)
    for rel in ["main/application.cc", "main/audio/audio_service.cc", "main/audio/engines/afe_audio_engine.cc",
                "main/boards/demo-board/demo_display.cc"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("#include <stdio.h>\nint x;\n", encoding="utf-8")
    (root / "main/boards/common").mkdir(parents=True)
    (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\nproject(demo)\n", encoding="utf-8")
    (root / "main/CMakeLists.txt").write_text(
        'set(SOURCES "application.cc")\n# 头文件目录\nset(INCLUDE_DIRS "." "audio" "protocols")\n'
        "list(APPEND INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/boards/common)\n"
        "idf_component_register(SRCS ${SOURCES}\n                    INCLUDE_DIRS ${INCLUDE_DIRS}\n                    REQUIRES esp_timer)\n",
        encoding="utf-8")
    return root


FILES = ["main/application.cc", "main/audio/audio_service.cc", "main/audio/engines/afe_audio_engine.cc",
         "main/boards/demo-board/demo_display.cc"]


@pytest.mark.parametrize("line", AI_WAYS)
@pytest.mark.parametrize("rel", FILES)
def test_any_ai_spelling_becomes_the_name_when_header_dir_is_an_include_dir(tmp_path, rel, line):
    root = _idf_project(tmp_path)
    assert ins.normalize_include(root, rel, line, "main/fw_stopwatch.h") == '#include "fw_stopwatch.h"'
    assert ins.include_resolves(root, rel, '#include "fw_stopwatch.h"', "main/fw_stopwatch.h")


def test_absolute_path_and_trailing_comment_are_normalised(tmp_path):
    root = _idf_project(tmp_path)
    line = f'  #include "{(root / "main/fw_stopwatch.h").resolve()}"  // 计时点工具'
    assert ins.normalize_include(root, FILES[2], line, "main/fw_stopwatch.h") == '  #include "fw_stopwatch.h" // 计时点工具'
    assert ins.normalize_include(root, FILES[2], '#include "other.h"', "main/fw_stopwatch.h") == '#include "other.h"'


def test_main_prefix_does_not_resolve_inside_the_component(tmp_path):
    root = _idf_project(tmp_path)
    for rel in FILES:  # 8787 那次的报错：组件里找头文件从 main/ 本身开始，"main/fw_stopwatch.h" 找不到
        assert not ins.include_resolves(root, rel, '#include "main/fw_stopwatch.h"', "main/fw_stopwatch.h")


def test_without_include_dirs_a_relative_path_is_used(tmp_path):
    (tmp_path / "src/drv/uart").mkdir(parents=True)
    (tmp_path / "src/drv/uart/u.c").write_text("int x;\n")
    assert ins.include_line(tmp_path, "src/drv/uart/u.c", "fw_stopwatch.h") == '#include "../../../fw_stopwatch.h"'
    assert ins.include_line(tmp_path, "fwmain.c", "fw_stopwatch.h") == '#include "fw_stopwatch.h"'
    assert ins.include_resolves(tmp_path, "src/drv/uart/u.c", '#include "../../../fw_stopwatch.h"', "fw_stopwatch.h")
    assert not ins.include_resolves(tmp_path, "src/drv/uart/u.c", "#include <fw_stopwatch.h>", "fw_stopwatch.h")


def test_parse_plan_replaces_ai_paths_and_records_it(tmp_path):
    root = door_source(tmp_path / "door")
    plan = json.loads(json.dumps(PLAN))
    plan["inserts"][0]["lines"] = ['#include "main/fw_stopwatch.h"']
    plan["inserts"][3]["lines"] = ["#include <../main/fw_stopwatch.h>"]
    p = ins.parse_plan(reply(plan), root, STEPS)
    assert p["ok"]
    incs = [l for x in p["inserts"] if x["step"] == "include" for l in x["lines"]]
    assert incs == ['#include "fw_stopwatch.h"', '#include "fw_stopwatch.h"']
    assert [f["file"] for f in p["include_fixed"]] == ["main/rfid_task.c", "main/lock_ctrl.c"]


def test_apply_checks_every_include_before_writing(tmp_path):
    root = door_source(tmp_path / "door")
    p = ins.parse_plan(reply(PLAN), root, STEPS)
    bad = {**p, "inserts": [{**x, "lines": ['#include "main/fw_stopwatch.h"'] if x["step"] == "include" else x["lines"]}
                            for x in p["inserts"]]}
    rec = ins.apply(root, bad, tmp_path / "bk")
    assert rec["ok"] and {x["file"] for x in rec["include_fixed"]} == {"main/rfid_task.c", "main/lock_ctrl.c"}
    for f in ("main/rfid_task.c", "main/lock_ctrl.c"):
        assert '#include "main/fw_stopwatch.h"' not in (root / f).read_text()
        assert '#include "fw_stopwatch.h"' in (root / f).read_text()
    assert ins.restore(root, tmp_path / "bk")["ok"]  # 改过写法照样能还原


GCC = """main/audio/engines/afe_audio_engine.cc:15:10: fatal error: main/fw_stopwatch.h: No such file or directory
main/audio/audio_service.cc:4:10: fatal error: main/fw_stopwatch.h: No such file or directory
main/application.cc:3:10: fatal error: main/fw_stopwatch.h: No such file or directory
main/boards/demo-board/demo_display.cc:3:10: fatal error: main/fw_stopwatch.h: No such file or directory"""


def test_header_missing_lists_every_file_gcc_and_clang():
    assert ins.header_missing(GCC) == ["main/audio/engines/afe_audio_engine.cc", "main/audio/audio_service.cc",
                                       "main/application.cc", "main/boards/demo-board/demo_display.cc"]
    assert ins.header_missing("/x/src/a.c:3:10: fatal error: 'fw_stopwatch.h' file not found") == ["/x/src/a.c"]
    assert ins.header_missing("main/a.cc:3:10: fatal error: esp_lcd.h: No such file or directory") == []


def test_header_missing_is_never_blamed_on_a_place(tmp_path):
    root = door_source(tmp_path / "door")
    rec = ins.apply(root, ins.parse_plan(reply(PLAN), root, STEPS), tmp_path / "bk")
    err = "main/rfid_task.c:2:10: fatal error: main/fw_stopwatch.h: No such file or directory"
    assert ins.error_place(err, rec, root) is None


def test_include_error_in_the_outer_level_is_not_called_misplaced(tmp_path):
    root = door_source(tmp_path / "door")
    rec = ins.apply(root, ins.parse_plan(reply(PLAN), root, STEPS), tmp_path / "bk")
    line = next(p for p in rec["places"] if p["step"] == "include")["new_lines"][0]
    place = ins.error_place(f"main/rfid_task.c:{line}:1: error: stray '\\302' in program", rec, root)
    assert place["step"] == "include" and not place["misplaced"]
    assert "放的地方不对" not in ins.error_text(place, STEPS)


def test_fix_include_paths_writes_relative_paths(tmp_path):
    root = _idf_project(tmp_path)
    plan = {"header": "main/fw_stopwatch.h",
            "inserts": [{"step": "include", "file": f, "after_line": 0, "lines": ['#include "main/fw_stopwatch.h"']} for f in FILES]}
    got = {x["file"]: x["lines"][0] for x in ins.fix_include_paths(plan, root)["inserts"]}
    assert got == {"main/application.cc": '#include "fw_stopwatch.h"', "main/audio/audio_service.cc": '#include "../fw_stopwatch.h"',
                   "main/audio/engines/afe_audio_engine.cc": '#include "../../fw_stopwatch.h"',
                   "main/boards/demo-board/demo_display.cc": '#include "../../fw_stopwatch.h"'}


# ---------- 网页后台：找不到头文件 → 码表自己改、直接重编一次 ----------

def test_build_fixes_include_paths_and_rebuilds_once(setup, tmp_path):
    seen = []

    def builder(work, target, board, idf, progress):
        seen.append(work)
        if len(seen) == 2:  # 第一次编计时版：找不到头文件
            return {"ok": False, "seconds": 0.1, "error": f"{work}/main/rfid_task.c:2:10: fatal error: main/fw_stopwatch.h: No such file or directory\n"
                                                           f"{work}/main/lock_ctrl.c:2:10: fatal error: main/fw_stopwatch.h: No such file or directory"}
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c, r, *_ = setup(builder=builder)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("done", "failed"))
    assert j["status"] == "done", j.get("err")
    assert len(seen) == 3  # 原版一次、计时版两次（改正后自动重编）
    assert j["inc_fixed"]["files"] == ["main/rfid_task.c", "main/lock_ctrl.c"]
    assert "计时点头文件的引用路径不对" in j["inc_fixed"]["message"] and "已自动改正并重编" in j["inc_fixed"]["message"]
    redo = Path(r["project_dir"]) / "fsw-redo.json"
    assert not redo.exists() or "note" not in json.loads(redo.read_text())  # 没让 AI 去挪位置


def test_build_still_missing_after_fix_says_so_without_asking_ai(setup):
    def builder(work, target, board, idf, progress):
        if not (Path(work) / "build").exists():
            fake_build_output(work)
            return {"ok": True, "seconds": 0.1}
        return {"ok": False, "seconds": 0.1, "error": "main/rfid_task.c:2:10: fatal error: fw_stopwatch.h: No such file or directory"}
    c, r, *_ = setup(builder=builder)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/fsw/run", json={"from": "build"})
    j = wait(c, lambda j: j["status"] in ("done", "failed"))
    e = j["err"]
    assert j["status"] == "failed" and e["kind"] == "build" and e["include_path"] == ["main/rfid_task.c"]
    assert "放的地方不对" not in e["message"] and "重新插入" not in e["message"] and "引用路径不对" in e["message"]


def test_page_shows_the_auto_fix_line():
    js = (Path(__file__).resolve().parents[1] / "fsw/web/static/app.js").read_text(encoding="utf-8")
    assert "FSWJ.inc_fixed" in js and "${incFixed()}" in js
