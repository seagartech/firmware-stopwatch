"""用户亲手操作后提的问题（2026-10-16，决策 103～110）。网页逻辑用 node 跑纯函数，其余查代码结构；行为在浏览器里回放核对。"""

import json
import shutil
import subprocess

import pytest

from tests.test_boss_short import grab, JS

node = pytest.mark.skipif(not shutil.which("node"), reason="没有 node")


def run(names, expr):
    code = "\n".join(grab(n) for n in names) + f"\nconsole.log(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


# ---- 1. 闪烁：同一页只改变了的地方，换页才整页换（带淡入） ----
def test_same_page_updates_only_what_changed():
    r = grab("render")
    assert "morphHTML(mEl.firstElementChild,body)" in r and "morphHTML(document.getElementById('dlg')" in r
    assert "mEl.innerHTML=`<div class=\"view\">${body}</div>`" in r  # 换页时才整页换


# ---- 2. 选板子：一个输入框，边输边筛，模糊匹配 ----
BOARDS = [{"id": "m5stack/core-s3", "chip": "ESP32-S3"}, {"id": "m5stack/core-s3-echo", "chip": "ESP32-S3"},
          {"id": "espressif/esp32-s3-box-3", "chip": "ESP32-S3"}, {"id": "bread-compact-wifi", "chip": "ESP32-S3"},
          {"id": "lichuang-c3-dev", "chip": "ESP32-C3"}]


@node
@pytest.mark.parametrize("q", ["core-s3", "cores3", "core s3", "CORE_S3", "m5 cs3"])
def test_fuzzy_board_match_puts_the_obvious_one_first(q):
    r = run(["bNorm", "boardMatch"], f"boardMatch({json.dumps(BOARDS)},{json.dumps(q)}).map(b=>b.id)")
    assert r[0] == "m5stack/core-s3"


@node
def test_empty_query_lists_all_and_chip_matches():
    assert len(run(["bNorm", "boardMatch"], f"boardMatch({json.dumps(BOARDS)},'')")) == 5
    assert run(["bNorm", "boardMatch"], f"boardMatch({json.dumps(BOARDS)},'c3').map(b=>b.id)")[0] == "lichuang-c3-dev"
    assert run(["bNorm", "boardMatch"], f"boardMatch({json.dumps(BOARDS)},'zzzz')") == []


def test_board_picker_is_one_combobox():
    b = grab("boardRow")
    assert 'role="combobox"' in b and 'role="listbox"' in b and "data-pickboard" in b and "<select" not in b
    assert "ArrowDown" in JS and "data-pickboard" in JS


# ---- 4. 乱码：烧录工具输出里的控制字符去掉；"完整日志"能点开 ----
def test_tool_output_control_chars_are_removed():
    from fsw.web.app import _plain
    raw = "\x1b[2KWriting at 0x009964ac \x1b[90m────────\x1b[0m 100.0% 1001.77kB/1001.77kB [5s]"
    assert _plain(raw) == "Writing at 0x009964ac ──────── 100.0% 1001.77kB/1001.77kB [5s]"


def test_flash_line_is_plain_words_and_full_log_works(tmp_path):
    from tests.test_fsw_api import setup, start_insert, wait  # noqa: F401
    assert "正在写进设备：{o}%" in open("fsw/web/app.py", encoding="utf-8").read()
    assert 'data-fulllog' in JS and "FSWJ.tail" in JS
    assert '<button class="pill">完整日志</button>' not in JS  # 不再有点了没反应的按钮


# ---- 7. 导出：成功、失败都在对话框里说；"为什么、怎么改"没写完先提醒，可以等写完自动导出 ----
def test_export_dialog_reports_result_and_warns_when_advice_not_ready():
    assert "EXP.state==='ok'" in JS and "✓ 已导出：" in JS and "✕ 没导出成功：" in JS
    assert "等写完自动导出" in JS and "data-expwait" in JS and "EXP.state==='wait'" in JS
    assert "这份里没有建议" in JS


# ---- 8. 拔了线点"还原成原版"：先把源码还原，说清设备的情况；插上线自动把原版烧回去 ----
def test_restore_without_device_says_so_and_flashes_when_plugged_in(tmp_path):
    from tests.test_fsw_api import FakeDevice, setup as _s  # noqa: F401
    from tests.test_fsw_api import start_insert, wait as fwait, run_to_try
    import json as _j
    from pathlib import Path
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_fsw_api import fake_build_output
    from tests.test_instrument import reply

    ports, dev = ["/dev/cu.usbmodem1101"], FakeDevice()

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: list(ports),
                              bg_builder=builder, device=dev, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    assert run_to_try(c)["status"] == "done"
    ports.clear()  # 拔了线
    j = c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]}).json()
    assert j["same"] and not j["flashed"] and j["waiting"]  # 源码还原了；设备没插，等插上
    assert "插上" in j["say"]
    ports.append("/dev/cu.usbmodem1101")  # 插回去
    j = fwait(c, lambda j: (j.get("restore") or {}).get("flashed"))
    assert dev.flashed[-1].endswith("firmware/orig")
    assert _j.loads((Path(r["project_dir"]) / "on-device.json").read_text())["fsw"] is False


def test_restore_button_always_asks_the_backend():
    h = JS[JS.index("const rs=e.target.closest('[data-restore]')"):][:900]
    assert "realFlash()&&FSWJ&&" not in h and "project_dir:REAL.project_dir" in h and "j.say" in h


def test_export_buttons_keep_the_dialog_open_to_show_the_result():
    """对话框里的按钮默认会关掉对话框；导出、等写完导出要把框留着，结果在框里说（用户：导出没有成功或失败的提醒）。"""
    assert "if(e.target.closest('[data-expgo]')){pop='export';" in JS and "if(e.target.closest('[data-expwait]')){pop='export';" in JS
    assert "没写成，导出的报告里会少这一部分" in JS


# ---- 第二次亲手操作（决策 113～116） ----
def test_picking_a_board_does_not_flash_the_reading_screen():
    r = JS[JS.index("async function realPick("):][:400]
    assert "const quiet=!!board&&!!REAL" in r and "if(!quiet){" in r


def test_flash_progress_is_one_bar_over_all_regions():
    src = open("fsw/web/app.py", encoding="utf-8").read()
    assert "def _overall(" in src  # 烧录工具一段一段写（引导程序、分区表、程序），每段各自 0～100%：合成一条


def test_beep_is_unlocked_inside_the_click():
    assert "function unlockAudio(" in JS and "unlockAudio();" in JS[JS.index("const ca=e.target.closest('[data-col]')"):][:300]


@node
def test_more_rounds_suggested_at_most_until_30_and_says_why():
    e = grab("enough")
    assert "stable=cap||" in e.replace(" ", "")  # 到 30 轮不再因为"还在变"催
    assert "moreWhy" in JS


def test_export_dialog_says_what_now_export_contains():
    assert "现在导出，只少这一部分，别的都一样" in JS  # 决策 120：一句话，不费脑子


def test_overall_flash_progress_never_goes_back(tmp_path):
    import json as _j
    from fsw.web.app import _overall
    (tmp_path / "flasher_args.json").write_text(_j.dumps({"flash_files": {"0x0": "b", "0x8000": "p", "0x10000": "a"}}))
    ov = _overall(tmp_path)
    seq = [ov(p) for p in (0, 50, 100, 0, 100, 0, 30, 65, 100)]  # 引导程序、分区表、程序三段
    assert seq == sorted(seq) and seq[-1] == 100 and seq[2] < 50


# ---- 第三次亲手操作（决策 120～122） ----
def test_board_full_name_written_into_the_box_after_picking():
    b = grab("boardPick")
    assert "q.value=id;q.blur()" in b and "picked" in grab("boardRow")


def test_step_log_lives_inside_the_running_step():
    assert "const stepLog=" in JS and "${cur&&fl.st===1&&i!==4?stepLog():''}" in JS


@node
def test_report_says_how_many_rounds_were_used_and_dropped():
    out = run(["qualityLine"], 'qualityLine({ok:30,missing:2,stale:1,half:0})')
    assert "用了 30 轮" in out and "2 轮没收全" in out and "1 轮记到的是上一轮剩下的" in out
    assert "没有丢掉的" in run(["qualityLine"], 'qualityLine({ok:20,missing:0,stale:0,half:0})')


def test_report_api_gives_quality(tmp_path):
    from tests.test_first_round_and_push import make, ROUND
    from tests.test_collect_api import wait
    from tests.test_standards import PAGE
    lines = []
    for k in range(4):
        lines += [f"FSW,RESET,{k * 10_000_000 + 1_000_000},0"] + [l.replace("FSW,P", "FSW,P") for l in [f"FSW,P{i},{k * 10_000_000 + 3_000_000 + i * 100_000},0" for i in range(1, 10)]]
    c = make(tmp_path, lines + ["FSW,RESET,99000000,0"])
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "p")})
    wait(c, lambda j: j["status"] == "done")
    q = c.get("/api/report").json()["quality"]
    assert q["ok"] == 3 and q["missing"] == 0 and q["stale"] == 0


# ---- 第四次亲手操作（决策 124～126） ----
def test_try_with_only_a_few_points_is_not_a_pass(tmp_path):
    from tests.test_fsw_api import FakeDevice, start_insert, wait as fwait, run_to_try, fake_build_output
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_instrument import reply

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    dev = FakeDevice(lines=["boot ok", "FSW,P5,30,0"])  # 3 个计时点只收到 1 个（比如只唤醒了设备）
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.usbmodem1101"],
                              bg_builder=builder, device=dev, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and "只收到 1 / 3 个" in j["err"]["message"]


def test_same_point_stale_again_and_again_says_so(tmp_path):
    from tests.test_first_round_and_push import make
    from tests.test_collect_api import wait
    from tests.test_standards import PAGE
    lines = []
    for k in range(4):
        b = k * 10_000_000
        lines += [f"FSW,RESET,{b + 1_000_000},0", f"FSW,P7,{b + 1_200_000},5"] + [f"FSW,P{i},{b + 3_000_000 + i * 100_000},0" for i in range(1, 10) if i != 7]
    c = make(tmp_path, lines + ["FSW,RESET,99000000,0"])
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "p")})
    j = wait(c, lambda j: "连着" in (j.get("half_hint") or ""))
    assert "插的位置不对" in j["half_hint"]


def test_no_auto_jump_after_try_and_elapsed_only_on_running_step():
    assert "page='collect';justFlashed=true" not in JS
    assert "${st===1?`已等 ${mmss(readSec)}，`:''}" in JS


def test_beep_can_be_tested_and_logged(tmp_path):
    assert "data-beeptest" in JS and "/api/client-log" in JS
    from tests.test_first_round_and_push import make
    c = make(tmp_path, [])
    assert c.post("/api/client-log", json={"what": "响", "state": "running"}).json()["ok"]
    assert "running" in (tmp_path / "d" / "client-log.jsonl").read_text(encoding="utf-8")


def test_try_reopens_the_port_when_it_hears_nothing(tmp_path, monkeypatch):
    """决策 127：烧完设备重启，调试口断开又回来；码表要是打开的是重启前那个，一直收不到东西。8 秒没输出就关掉重开。"""
    import time as _t
    from fsw.web import app as A
    from tests.test_fsw_api import FakeDevice, start_insert, wait as fwait, run_to_try, fake_build_output
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_instrument import reply
    monkeypatch.setattr(A, "REOPEN_SECS", 0.3)

    class Silent(FakeDevice):  # 第一次打开什么都收不到（重启前的那个口），第二次才正常
        opens = 0

        def read(self, port, seconds, on_line, stop=None):
            Silent.opens += 1
            if Silent.opens == 1:
                end = _t.time() + seconds
                while _t.time() < end and not (stop and stop()):
                    _t.sleep(0.02)
                return []
            return super().read(port, seconds, on_line, stop)

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    dev = Silent()
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.usbmodem1101"],
                              bg_builder=builder, device=dev, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "done" and Silent.opens == 2
