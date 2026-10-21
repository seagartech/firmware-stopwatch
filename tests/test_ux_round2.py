"""用户亲手操作后提的问题（2026-10-16，决策 103～110）。网页逻辑用 node 跑纯函数，其余查代码结构；行为在浏览器里回放核对。"""

import json
from pathlib import Path
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
def test_no_more_rounds_or_enough_panel():
    """10-21 用户定固定测 10 轮：测完不再有"再测 10 轮"和"测的够不够"（决策 170）。"""
    assert 'data-col="more"' not in JS and "function enough(" not in JS and "moreWhy" not in JS and "#enough" not in JS


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
    out = run(["qualityLine", "gapLine"], 'qualityLine({ok:30,missing:2,stale:1,half:0})')
    assert "用了 30 轮" in out and "2 轮没收全" in out and "1 轮记到的是上一轮剩下的" in out
    assert "没有丢掉的" in run(["qualityLine", "gapLine"], 'qualityLine({ok:20,missing:0,stale:0,half:0})')
    out = run(["qualityLine", "gapLine"], 'qualityLine({ok:30,missing:0,stale:0,half:0,nostart:1,startName:"设备听出你停了"})')
    assert "1 次没记到起点「设备听出你停了」" in out and "没有丢掉的" not in out  # 决策 133
    # 10-19：中间的点不是这一轮的（上一轮被中途打断），这一轮照样算，写清是哪几轮哪一个点、哪几段空着
    out = run(["qualityLine", "gapLine"], 'qualityLine({ok:10,missing:0,stale:0,half:0,gap:[{n:4,names:["收到开始说话"]},{n:5,names:["收到开始说话"]}]})')
    assert "第 4、5 轮的「收到开始说话」记在这一轮开始之前" in out and "两段空着" in out


def test_report_api_counts_rounds_without_start(tmp_path):
    from tests.test_first_round_and_push import make
    from tests.test_collect_api import wait
    from tests.test_standards import PAGE
    lines = []
    for k in range(4):
        pts = [f"FSW,P{i},{k * 10_000_000 + 3_000_000 + i * 100_000},0" for i in range(1, 10)]
        lines += [f"FSW,RESET,{k * 10_000_000 + 1_000_000},0"] + (pts[1:] if k == 1 else pts)
    c = make(tmp_path, lines + ["FSW,RESET,99000000,0"])
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "p")})
    wait(c, lambda j: j["status"] == "done")
    q = c.get("/api/report").json()["quality"]
    assert q["ok"] == 3 and q["nostart"] == 1 and q["startName"]


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
    assert "插的位置不对" in j["half_hint"]  # 10-19 起这几轮照样算（只空着那两段），但同一个点连着 3 轮这样仍点名


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
    assert j["boot_quiet"] is True                      # 重连过：页面改成"可能已经开机了，现在做一次"，不一直转圈
    assert "if(!up&&J.boot_quiet)" in JS


def test_try_with_a_stuck_port_says_replug_usb(tmp_path, monkeypatch):
    """10-21 真测：烧完调试口还在，但一个字节都读不到（乐鑫烧录工具也连不上），拔插 USB 线才好。
    码表一直重连、页面一直说"可能已经开机了"；现在页面过一会儿提示拔插，超时的报错也直接说拔插。"""
    import time as _t
    from fsw.web import app as A
    from tests.test_fsw_api import FakeDevice, start_insert, wait as fwait, run_to_try, fake_build_output
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_instrument import reply
    monkeypatch.setattr(A, "REOPEN_SECS", 0.2)
    monkeypatch.setattr(A, "TRY_SECONDS", 1)

    class Stuck(FakeDevice):  # 每次打开都什么都收不到
        def read(self, port, seconds, on_line, stop=None):
            end = _t.time() + seconds
            while _t.time() < end and not (stop and stop()):
                _t.sleep(0.02)
            return []

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.usbmodem1101"],
                              bg_builder=builder, device=Stuck(), wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and j["heard"] is None
    assert "拔下 USB 线" in j["err"]["why"]
    assert "!J.heard&&J.try_started&&now-J.try_started>USB_QUIET" in JS and "拔下 USB 线" in grab("tryBox")


def test_try_that_hears_the_device_does_not_blame_usb(tmp_path, monkeypatch):
    """调试口有输出、只是没有计时点（没触发到）：照旧说"没触发到"，不叫人拔插。"""
    import time as _t
    from fsw.web import app as A
    from tests.test_fsw_api import FakeDevice, start_insert, wait as fwait, run_to_try, fake_build_output
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_instrument import reply
    monkeypatch.setattr(A, "TRY_SECONDS", 1)

    class Chatty(FakeDevice):  # 只有设备平常的日志，没有计时点
        def read(self, port, seconds, on_line, stop=None):
            end = _t.time() + seconds
            while _t.time() < end and not (stop and stop()):
                on_line("I (100) main: idle")
                _t.sleep(0.05)
            return ["I (100) main: idle"]

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.usbmodem1101"],
                              bg_builder=builder, device=Chatty(), wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    j = run_to_try(c)
    assert j["status"] == "failed" and j["heard"]
    assert "拔下 USB 线" not in j["err"]["why"] and "没触发到" in j["err"]["why"]


def test_unlock_plays_a_real_sound_for_safari():
    """决策 128：Safari 只认点击那一下放出的真声音；放一段无声的不算，声音一直没打开（用户记录：多轮实测时一直是 suspended）。"""
    u = grab("unlockAudio")
    assert "createOscillator" in u and "createBuffer(1,1" not in u


# ---- 决策 129：提示按类型上色，出错的不自己消失；还原时调试口忙就等一下再烧 ----
def test_error_toast_is_red_and_stays():
    t = JS[JS.index("let toastT=null;function toast("):][:700]
    assert "kind==='bad'" in t and "'toast t-'+kind" in t and "if(kind!=='bad')toastT=setTimeout" in t and "tclose" in t


def test_restore_waits_when_port_busy_and_keeps_message_short(tmp_path):
    from tests.test_fsw_api import FakeDevice, start_insert, wait as fwait, run_to_try, fake_build_output
    from fastapi.testclient import TestClient
    from fsw.llm import FakeLLM
    from fsw.web.app import create_app
    from tests.identify_fixtures import door_source
    from tests.test_instrument import reply

    class Busy(FakeDevice):  # 还原时前两次调试口被占着
        busy = 0

        def flash(self, fw, port, on_progress):
            if str(fw).endswith("orig") and Busy.busy < 2:
                Busy.busy += 1
                return {"ok": False, "error": ["A fatal error occurred: Could not open /dev/cu.usbmodem1101, the port is busy or doesn't exist.",
                                               "ERROR: A serial exception error occurred: Could not configure port: (6, 'Device not configured')"][Busy.busy - 1]}
            return super().flash(fw, port, on_progress)

    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    dev = Busy()
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg", llm_factory=lambda p, k: FakeLLM(reply()),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True}, port_finder=lambda: ["/dev/cu.usbmodem1101"],
                              bg_builder=builder, device=dev, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    r = c.post("/api/source/inspect", json={"path": str(door_source(tmp_path / "door"))}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3"})
    start_insert(c, r)
    fwait(c, lambda j: j["status"] == "inserted")
    run_to_try(c)
    j = c.post("/api/fsw/restore", json={"project_dir": r["project_dir"]}).json()
    assert j["flashed"] and Busy.busy == 2  # 忙了两次，第三次烧上了，不用人再点


def test_board_from_last_time_is_filled_in_and_said():
    assert "if(REAL.board&&!hBoardOpen)hBoardQ=REAL.board" in JS and "按上次选的" in grab("boardRow")


def test_unlock_is_inaudible_and_happens_on_any_click():
    """决策 131：点开始时那一声"叮"让人以为收好一轮了：改成听不见的音量；任何一次点击都顺手打开声音。"""
    u = grab("unlockAudio")
    assert "linearRampToValueAtTime(.00001" in u and "o.frequency.value=20" in u and "createOscillator" in u
    assert "document.addEventListener('click',()=>unlockAudio(),true)" in JS


def test_blocked_sound_shows_a_one_click_fix():
    """决策 132：该响的时候浏览器的声音还关着（比如 Safari 不认那段听不见的声音）：页面上出现"提示音被浏览器关着，点这里打开"。"""
    assert "提示音被浏览器关着，点这里打开" in grab("beepBtn") and "beepBlocked=true" in grab("ding")


def test_cancel_while_waiting_does_not_export_later():
    """等"为什么、怎么改"写完再导出时点了取消：写完后不再自动导出（以前取消只关了框，写完照样导出一份）。"""
    assert "if(EXP.state==='wait'&&pop!=='export')EXP.state='';" in JS
    assert JS.index("if(EXP.state==='wait'&&pop!=='export')") < JS.index("if(x.status==='done'&&EXP.state==='wait')")


def test_error_toast_has_only_the_close_mark():
    """出错提示：红框、右边一个 × 关掉，前面不再多一个 ✕。"""
    assert "kind==='bad'?m.replace(/^✕\\s*/,''):m" in JS


def test_project_name_follows_the_chosen_source_not_demo():
    """选好源码后"确认要测的内容"里的项目名称按这次的源码和板子取，不能一直是演示数据的名字（取名那句曾被写进注释里）。"""
    line = next(l for l in JS.splitlines() if "REAL={...r.info" in l)
    code = line.split("//")[0]
    assert "pName=REAL.name+(REAL.board?" in code


def test_ding_only_when_a_round_is_not_all_in():
    """10-18 真机：测全程时一轮要等设备把回答整段放完才算收好，那时"叮"一声人只觉得提示音晚。改成收好不响，只有没收全低响一声。"""
    from tests.test_boss_short import grab
    f = grab("colApply")
    assert "for(const x of col.got.slice(before))if(x.bad)ding(true)" in f
    assert "ding(false)" not in f


def test_resume_clears_the_ding_mark():
    # 正好在"收齐已叮、还没记下"那 1.5 秒里点停止，接着测时清掉记号，下一轮照样叮
    assert "async function colStart(keep){colStop();if(keep){col.st=1;col.phase=0;col.rd=false}" in JS


def test_report_table_owner_column_and_reversal_note_do_not_squeeze():
    # 技术细节表："谁负责"列不被挤成竖排；"第 N 轮先后反了"放数字下一行，不撑宽整列
    css = (Path(__file__).resolve().parents[1] / "fsw/web/static/style.css").read_text(encoding="utf-8")
    assert "th.own,td.own{min-width:6.5em}" in css
    assert '<th class="own">谁负责</th>' in JS and '<td class="own" ${SANS}>${s.owner}</td>' in JS
    assert '<br><span class="note">第 ${neg.join' in JS


def test_start_point_missing_several_rounds_in_a_row_is_named(tmp_path):
    # 试一次不再等起点（唤醒那一轮本来没有）：起点在多轮实测里核对，连着 3 次没记到就点名
    from tests.test_first_round_and_push import make
    from tests.test_collect_api import wait
    from tests.test_standards import PAGE
    lines = []
    for k in range(4):
        pts = [f"FSW,P{i},{k * 10_000_000 + 3_000_000 + i * 100_000},0" for i in range(2, 10)]
        lines += [f"FSW,RESET,{k * 10_000_000 + 1_000_000},0"] + pts
    c = make(tmp_path, lines + ["FSW,RESET,99000000,0"])
    c.post("/api/collect/start", json={"page": PAGE, "source": "device", "rounds": 3, "project_dir": str(tmp_path / "p")})
    j = wait(c, lambda j: "没记到起点" in (j.get("half_hint") or ""))
    assert "连着" in j["half_hint"] and "插计时点" in j["half_hint"]


def test_spiky_rounds_are_red_in_the_round_chart():
    # 真测第 36 次：拍板区说"见逐轮图里红底的几轮"，可偶发特别慢的第 6 轮在图上没标红
    D = ('{J:{labels:[{kind:"spiky",segment:"a",rounds:[6]},{kind:"spiky",segment:"b",rounds:[2],with:"problem:x"}]},'
         'allSegs:[{k:"a",name:"上传录音、服务器听写"},{k:"b",name:"b"}]}')
    assert run(["spikyRounds"], f"[...spikyRounds({D})]") == [[6, "上传录音、服务器听写"]]
    assert "spk.has(r.n)" in grab("roundsChart")


def test_page_follows_a_stop_made_outside_the_page():
    # 真测：后台被别处停下（status=stopped）时测量页还显示"正在编译 0%"；页面要跟着停、回首页并说明
    pre = ("let FSWJ,fswT=1,runT=1,AUTO=true,page='flash',hStage=3,said='';const fl={st:1};"
           "const clearInterval=()=>{},render=()=>{},window={scrollTo(){}},toast=m=>{said=m};")
    code = pre + grab("fswApply") + "\nfswApply({status:'stopped',phase:'build'});console.log(JSON.stringify([page,AUTO,fl.st,said]));"
    r = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
    assert r[:3] == ["home", False, 0] and "后台停止" in r[3]


def test_failed_trial_stays_on_the_measurement_page():
    # 真测：试测超时后退回老的「插计时点和烧录」页，试过了还得手点「开始多轮实测」；试测没通过要留在测量页、就地「再试一次」
    code = ("let AUTO=true,autoHold='',pop=null,page='flash',fl={st:5,err:'try'};" + grab("runView") +
            "\nconst a=runView();fl.err='build';console.log(JSON.stringify([a,runView()]));")
    assert json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout) == [True, False]
    assert 'data-flx="retry"' in grab("runPage")


@node
def test_step_bar_colours_the_points_that_really_arrived():
    """10-19：设备要到一轮结束才打出"你说完"那两个点，按个数从左往右涂会一直停在"收到第一段声音"，还把没到的涂绿。"""
    setup = ("globalThis.CX=()=>({pk:['P1','P2','P3','P6','P8'],ph:['说完','录音发出','听写','开始说话','第一段声音','这一轮记好了']}),"
             "globalThis.PH=()=>CX().ph,")
    out = run(["phCls"], f"({setup}globalThis.col={{have:['P3','P6'],phase:3}},phCls())")
    assert [c for _, c in out] == ["ok", "ok", "ok", "ok", "cur", ""]  # 头两步要到一轮结束才打出：后面到了，前面一起绿
    out = run(["phCls"], f"({setup}globalThis.col={{have:['P3','P8'],phase:3}},phCls())")
    assert [c for _, c in out] == ["ok", "ok", "ok", "ok", "cur", ""]  # 打断后"开始说话"空着：不在中间留一块灰，最后一步没收尾只加粗
    out = run(["phCls"], f"({setup}globalThis.col={{have:[],phase:0}},phCls())")
    assert [c for _, c in out] == ["cur", "", "", "", "", ""]
    out = run(["phCls"], f"({setup}globalThis.col={{have:null,phase:5}},phCls())")  # 收全了：按原来的走，停在"这一轮记好了"
    assert [c for _, c in out] == ["ok"] * 5 + ["cur"]


@node
def test_report_line_uses_the_round_totals_even_when_a_segment_is_empty():
    """10-19：被打断那几轮中间两段空着，线的用时后台按首尾算好了；报告网页按段相加把这几轮丢了，中位写成 1.22（实际 1.34）、最慢 1.42（实际 2.44）。"""
    expr = ("(()=>{const S={lines:[{name:'开口',segs:['a','b'],ok:[0,9]}],segs:{a:{v:[0.5,0.6,0.7]},b:{v:[0.5,null,0.7]}}};"
            "S.lines[0].ends0=[1.0,2.5,1.4];const L=build(S).lines[0];return [L.med,L.max]})()")
    assert run(["LCOL", "LSOFT", "num", "sorted", "med", "p90", "mx", "build"], expr) == [1.4, 2.5]
    assert "ends0:li<0?null:J.rounds.map(r=>(r.ends||[])[li]??null)" in JS  # 真测报告把后台每轮的线用时带进来


@node
def test_range_skips_rounds_without_a_value():
    """10-19：第 2 轮没换表情，表情这条那轮没数，范围写成了 0.00～1.87。"""
    out = run(["f", "fms", "rangeTxt"], "rangeTxt([null,0.62,1.87],[1,2,3])")
    assert out.startswith("0.62") and "0.00" not in out


@node
def test_report_takes_line_and_segment_numbers_from_the_backend():
    """网页不自己再算一遍一般/最慢/最差，用后台判定里的数。"""
    expr = ("(()=>{const D={lines:[{name:'开口',med:1.22,p90:1.42,max:1.42}],allSegs:[{k:'a',med:0.1,max:0.2}],rounds:[],main:null};D.main=D.lines[0];"
            "const J={lines:[{name:'开口',med:1.34,p90:1.46,max:2.44}],segments:[{k:'a',med:0.3,max:0.5}],labels:[]};"
            "withJudge(D,J);return [D.lines[0].med,D.lines[0].p90,D.lines[0].max,D.allSegs[0].med,D.allSegs[0].max]})()")
    assert run(["withJudge"], f"(globalThis.VCLS={{}},{expr})") == [1.34, 1.46, 2.44, 0.3, 0.5]
