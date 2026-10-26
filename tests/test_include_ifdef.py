"""10-26 8776 编译不过：AI 把 #include "fw_stopwatch.h" 插在 #if CONFIG_IDF_TARGET_ESP32S3 … #else … #endif 的 #else 那一支里，
ESP32-S3 上不编译那一支，文件里的计时点语句都报"没有声明"。码表自己把 #include 挪到文件最外层（不再问 AI）；
计时点落在按配置不编译的那一支里，插点结果直接说是哪个开关、记不到。"""

import json

from fsw import instrument as ins, structure

HEAD = """#include "audio_service.h"
#include <esp_log.h>
#include <cstring>

#define RATE_CVT_CFG(_src_rate, _dest_rate, _channel)                                        \\
    (esp_ae_rate_cvt_cfg_t) {                                                                \\
        .src_rate = (uint32_t)(_src_rate), .dest_rate = (uint32_t)(_dest_rate),              \\
        .channel = (uint8_t)(_channel), .bits_per_sample = ESP_AUDIO_BIT16, .complexity = 2, \\
        .perf_type = ESP_AE_RATE_CVT_PERF_TYPE_SPEED,                                        \\
    }

#define OPUS_DEC_CFG(_sample_rate, _frame_duration_ms)                                     \\
    (esp_opus_dec_cfg_t) {                                                                 \\
        .sample_rate = (uint32_t)(_sample_rate), .channel = ESP_AUDIO_MONO,                \\
        .frame_duration =                                                                  \\
            (esp_opus_dec_frame_duration_t)AS_OPUS_GET_FRAME_DRU_ENUM(_frame_duration_ms), \\
        .self_delimited = false,                                                           \\
    }

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32P4 || CONFIG_IDF_TARGET_ESP32S31
#include "engines/afe_audio_engine.h"
#else
#include "engines/lite_audio_engine.h"
#endif

#define TAG "AudioService"

void AudioService::Play() {
    int n = 0;
    n++;
#if CONFIG_USE_LITE
    n += 2;
#else
    n += 3;
#endif
    Output(n);
}
"""
REL = "main/audio/audio_service.cc"


def project(tmp_path, target="CONFIG_IDF_TARGET_ESP32S3=y"):
    root = tmp_path / "src"
    (root / "main/audio").mkdir(parents=True)
    (root / REL).write_text(HEAD, encoding="utf-8")
    (root / "sdkconfig").write_text(f"{target}\n# CONFIG_USE_LITE is not set\n", encoding="utf-8")
    return root


def steps():
    return [{"p": "P1", "n": "开始播放", "path": REL, "l": 37, "where": "before", "once": "once", "line": "start"},
            {"p": "P2", "n": "轻量播放", "path": REL, "l": 33, "where": "before", "once": "once", "line": "talk"}]


def reply(include_after=23):
    plan = {"header": "main/fw_stopwatch.h", "inserts": [
        {"step": "include", "file": REL, "after_line": include_after, "lines": ['#include "fw_stopwatch.h"']},
        {"step": "P1", "file": REL, "after_line": 36, "lines": ["    // 计时点 P1：开始播放", "    FSW_MARK(1, 0);"]},
        {"step": "P2", "file": REL, "after_line": 32, "lines": ["    // 计时点 P2：轻量播放", "    FSW_MARK_ONCE(2, 0);"]}]}
    return "```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```"


def test_include_ok_and_spot(tmp_path):
    src = structure.Project(project(tmp_path)).file(REL)
    assert structure.include_ok(src, 3) and structure.include_ok(src, 24)
    assert not structure.include_ok(src, 23) and not structure.include_ok(src, 21)  # #else / #if 那一支里
    assert not structure.include_ok(src, 13)  # 多行宏中间
    assert not structure.include_ok(src, 30)  # 函数里
    assert structure.include_spot(src) == 3


def test_cond_value_from_sdkconfig(tmp_path):
    proj = structure.Project(project(tmp_path))
    assert structure.cond_value(proj, "CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32P4") is True
    assert structure.cond_value(proj, "CONFIG_USE_LITE") is False
    assert structure.cond_value(proj, "defined(CONFIG_IDF_TARGET_ESP32S3) && !CONFIG_USE_LITE") is True
    assert structure.cond_value(proj, "SOME_OTHER_MACRO") is None
    src = proj.file(REL)
    assert [f["on"] for f in structure.pp_regions(proj, src, 23)] == [False]  # #else 那一支
    assert [f["on"] for f in structure.pp_regions(proj, src, 21)] == [True]


def test_include_in_else_branch_is_moved_by_tool(tmp_path):
    root = project(tmp_path)
    plan = ins.parse_plan(reply(23), root, steps())
    assert plan["ok"]
    inc = [x for x in plan["inserts"] if x["step"] == "include"]
    assert [x["after_line"] for x in inc] == [3]
    assert any("#include" in n and "第 3 行后面" in n for n in plan["notes"])
    assert any(f["kind"] == "moved" and f["step"] == "include" for f in plan["fixes"])


def test_good_include_is_left_alone(tmp_path):
    plan = ins.parse_plan(reply(24), project(tmp_path), steps())
    assert [x["after_line"] for x in plan["inserts"] if x["step"] == "include"] == [24]


def test_point_in_branch_not_compiled_is_named(tmp_path):
    plan = ins.parse_plan(reply(3), project(tmp_path), steps())
    assert plan["ok"]
    off = [x for x in plan["ifdef"] if x["on"] is False]
    assert [x["name"] for x in off] == ["轻量播放"]
    assert "CONFIG_USE_LITE" in off[0]["text"] and "记不到" in off[0]["text"]
    assert any("轻量播放" in c for c in ins.plan_checks(plan))
    assert not any(x["name"] == "开始播放" for x in plan["ifdef"])


def test_point_in_compiled_branch_is_quiet(tmp_path):
    root = project(tmp_path)
    (root / "sdkconfig").write_text("CONFIG_IDF_TARGET_ESP32S3=y\nCONFIG_USE_LITE=y\n", encoding="utf-8")
    plan = ins.parse_plan(reply(3), root, steps())
    assert plan["ok"] and plan["ifdef"] == []


def test_undeclared_compile_error_blames_include(tmp_path):
    root = project(tmp_path)
    err = f"{root}/{REL}:38:5: error: 'FSW_MARK' was not declared in this scope"
    rec = {"places": [{"step": "P1", "file": REL, "at": 36, "new_lines": [37, 38], "lines": []}], "steps": {}}
    pl = ins.error_place(err, rec, root)
    assert pl and pl["undeclared"]
    t = ins.error_text(pl, steps())
    assert "#include 没被读到" in t and "放错了" in t
