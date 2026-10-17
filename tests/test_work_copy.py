"""工作副本始终是"当前源码的干净副本"（定版排查 A 组）。

1. 测完没还原就改了源码再测：重新复制工作副本时，作废跟旧源码绑定的插入备份、插入方案、计时版固件；
   旧原版固件留着只用来把设备还原回去，不当新源码的原版。
2. 测完没还原就换板子、换一句"要测的"：编原版、找关键步骤之前先把副本里的计时点还原掉。
3. 只改 sdkconfig 也算源码改了。
4. 副本缺文件、被改过：重新复制。
5. 插计时点中途出错：撤回已改的文件，网页上说一句话，可以"重新插入"。
"""

import json
import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient

from fsw import instrument as ins
from fsw.llm import FakeLLM
from fsw.web.app import create_app
from fsw.workspace.workspace import prepare
from tests.identify_fixtures import door_source
from tests.test_fsw_api import FakeDevice, fake_build_output, run_to_try, start_insert, wait
from tests.test_bgbuild import wait_for
from tests.test_instrument import STEPS, reply


def plan_for(work):
    return ins.parse_plan(reply(), work, STEPS)


def has_fsw(root):
    return any("FSW_" in p.read_text(errors="replace") for p in Path(root).rglob("*.c"))


# ---------- 工作区这一层 ----------
def test_changed_source_drops_the_old_backup(tmp_path):
    """改了源码再选：旧的插入备份作废，不会在下次插入前被盖到新副本上（删掉的文件也不会复活）。"""
    src = door_source(tmp_path / "door")
    r = prepare(src, tmp_path / "data")
    work, backup = Path(r["work"]), Path(r["project_dir"]) / "fsw-backup"
    assert ins.apply(work, plan_for(work), backup)["ok"]
    f = src / "main/rfid_task.c"
    f.write_text(f.read_text(encoding="utf-8").replace("// rfid_task 第 3 行", "int my_new_fix = 1;"), encoding="utf-8")
    (src / "main/ui_led.c").unlink()

    r2 = prepare(src, tmp_path / "data")

    assert r2["status"] == "changed" and ins.current(backup) is None
    ins.restore(work, backup)
    assert "my_new_fix" in (work / "main/rfid_task.c").read_text(encoding="utf-8")
    assert not (work / "main/ui_led.c").exists() and ins.compare(work, src)["same"]


def test_restore_refuses_a_backup_that_does_not_match_the_copy(tmp_path):
    """插入记录和副本对不上（副本被换过）：不拿旧备份去盖。"""
    src = door_source(tmp_path / "door")
    r = prepare(src, tmp_path / "data")
    work, backup = Path(r["work"]), Path(r["project_dir"]) / "fsw-backup"
    ins.apply(work, plan_for(work), backup)
    shutil.copy2(src / "main/rfid_task.c", work / "main/rfid_task.c")
    (work / "main/rfid_task.c").write_text("int v2;\n", encoding="utf-8")

    out = ins.restore(work, backup)

    assert not out["ok"] and (work / "main/rfid_task.c").read_text(encoding="utf-8") == "int v2;\n"


def test_only_sdkconfig_changed_counts_as_changed(tmp_path):
    src = door_source(tmp_path / "door")
    (src / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\nCONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_160=y\n')
    r1 = prepare(src, tmp_path / "data")
    (src / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\nCONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y\n')

    r2 = prepare(src, tmp_path / "data")

    assert r2["status"] == "changed" and r1["fingerprint"] != r2["fingerprint"]
    assert "240" in (Path(r2["work"]) / "sdkconfig").read_text()


def test_same_source_again_gives_a_clean_copy_when_last_time_was_not_restored(tmp_path):
    src = door_source(tmp_path / "door")
    r = prepare(src, tmp_path / "data")
    work, backup = Path(r["work"]), Path(r["project_dir"]) / "fsw-backup"
    ins.apply(work, plan_for(work), backup)
    (work / "build").mkdir()
    (work / "build/app.bin").write_text("x")

    r2 = prepare(src, tmp_path / "data")

    assert r2["status"] == "unchanged" and ins.current(backup) is None
    assert ins.compare(work, src)["same"] and not has_fsw(work)
    assert (work / "build/app.bin").exists()  # 按备份还原，编好的东西留着，下次只重编改过的


def test_broken_copy_is_copied_again(tmp_path):
    """副本缺文件、被改过（复制到一半中断、临时目录被系统清理）：源码没变也重新复制。"""
    src = door_source(tmp_path / "door")
    work = Path(prepare(src, tmp_path / "data")["work"])
    (work / "main/lock_ctrl.c").unlink()
    (work / "main/ui_led.c").write_text("坏了", encoding="utf-8")

    r2 = prepare(src, tmp_path / "data")

    assert r2["status"] == "unchanged" and ins.compare(work, src)["same"]


def test_insert_failing_half_way_is_undone(tmp_path, monkeypatch):
    """写到第二个文件时出错（只读、磁盘满）：第一个文件撤回，没有残留记录；再插不会重复插，还原逐字节一样。"""
    door = door_source(tmp_path / "door")
    orig = tmp_path / "orig"
    shutil.copytree(door, orig)
    backup = tmp_path / "backup"
    plan = plan_for(door)
    real = Path.write_bytes

    def wb(self, data):
        if self.name == "rfid_task.c":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", wb)

    out = ins.apply(door, plan, backup)

    monkeypatch.setattr(Path, "write_bytes", real)
    assert not out["ok"] and "重新插入" in out["message"]
    assert ins.compare(door, orig)["same"] and ins.current(backup) is None
    assert ins.apply(door, plan, backup)["ok"]
    assert (door / "main/rfid_task.c").read_text(encoding="utf-8").count("FSW_MARK_ONCE(2") == 1
    ins.restore(door, backup)
    assert ins.compare(door, orig)["same"]


def test_insert_killed_half_way_can_still_be_restored(tmp_path, monkeypatch):
    """撤回也没做成（比如码表被强行关掉）：插入前就记下了要改哪些文件，还原照样能放回去。"""
    door = door_source(tmp_path / "door")
    orig = tmp_path / "orig"
    shutil.copytree(door, orig)
    backup = tmp_path / "backup"
    real = Path.write_bytes

    def wb(self, data):
        if self.name == "rfid_task.c":
            raise OSError(28, "No space left on device")
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", wb)
    monkeypatch.setattr(ins, "_undo", lambda *a, **k: None)

    assert not ins.apply(door, plan_for(door), backup)["ok"]

    monkeypatch.undo()
    assert ins.current(backup)["state"] == "applying" and has_fsw(door)
    assert ins.restore(door, backup)["ok"] and ins.compare(door, orig)["same"]


# ---------- 网页后台 ----------
def app(tmp_path, builds, dev, builder=None, llm=None):
    def default_builder(work, target, board, idf, progress):
        builds.append((work, board))
        fake_build_output(work)
        (Path(work) / "build/app.bin").write_text(f"board={board} has_fsw={has_fsw(work)}")
        return {"ok": True, "seconds": 0.1, "error": None}
    c = TestClient(create_app(data_dir=tmp_path / "data", config_dir=tmp_path / "cfg",
                              llm_factory=llm or (lambda p, k: FakeLLM(reply())),
                              idf_finder=lambda: [{"path": "/x/idf", "version": "6.1.0"}],
                              cc_status=lambda: {"found": True, "logged_in": True},
                              port_finder=lambda: ["/dev/cu.usbmodem1101"], bg_builder=builder or default_builder,
                              device=dev, wait_step=0.01))
    c.post("/api/settings", json={"llm_provider": "cc"})
    return c


def pick(c, src, board=None):
    r = c.post("/api/source/inspect", json={"path": str(src), "board": board}).json()
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"], "board": board})
    return r


def insert_and_run(c, r, board=None):
    c.post("/api/fsw/insert", json={"work": r["work"], "project_dir": r["project_dir"], "steps": STEPS,
                                    "chip": "ESP32-S3", "board": board})
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"
    assert run_to_try(c)["status"] == "done"


def test_change_source_without_restoring_then_test_again_uses_the_new_code(tmp_path):
    c = app(tmp_path, [], FakeDevice())
    src = door_source(tmp_path / "door")
    insert_and_run(c, pick(c, src))
    f = src / "main/rfid_task.c"
    f.write_text(f.read_text(encoding="utf-8").replace("// rfid_task 第 3 行", "int my_new_fix = 1;"), encoding="utf-8")

    r2 = pick(c, src)
    assert r2["status"] == "changed"
    start_insert(c, r2)
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"

    w = (Path(r2["work"]) / "main/rfid_task.c").read_text(encoding="utf-8")
    assert "FSW_" in w and "my_new_fix" in w


def test_change_source_keeps_what_is_needed_to_restore_the_device(tmp_path):
    """改了源码再选：设备上还是计时版，"还原成原版"照样能把原版烧回去，说清楚烧的是改之前那份。"""
    dev = FakeDevice()
    c = app(tmp_path, [], dev)
    src = door_source(tmp_path / "door")
    r = pick(c, src)
    insert_and_run(c, r)
    proj = Path(r["project_dir"])
    (src / "main/fix.c").write_text("int fix(void){return 1;}\n")

    assert pick(c, src)["status"] == "changed"
    assert json.loads((proj / "on-device.json").read_text())["fsw"]
    assert not (proj / "fsw-backup/record.json").exists() and not (proj / "fsw-plan.json").exists()
    out = c.post("/api/fsw/restore", json={"project_dir": str(proj)}).json()
    assert out["flashed"] and dev.flashed[-1].endswith("firmware/orig") and "改源码之前" in out["say"]
    assert not json.loads((proj / "on-device.json").read_text())["fsw"]


def test_switch_board_without_restoring_saves_a_clean_original(tmp_path):
    builds, dev = [], FakeDevice()
    c = app(tmp_path, builds, dev)
    src = door_source(tmp_path / "door")
    for b in ("board-a", "board-b"):
        (src / "main/boards" / b).mkdir(parents=True)
        (src / "main/boards" / b / "config.json").write_text(json.dumps({"target": "esp32s3", "builds": [{}]}))
    r = pick(c, src, "board-a")
    wait_for(c, "done")
    insert_and_run(c, r, "board-a")
    orig = Path(r["project_dir"]) / "firmware/orig/app.bin"
    assert orig.read_text() == "board=board-a has_fsw=False"

    r2 = pick(c, src, "board-b")
    s = wait_for(c, "done")
    assert s["board"] == "board-b"
    insert_and_run(c, r2, "board-b")

    assert orig.read_text() == "board=board-b has_fsw=False"


def test_bgbuild_cleans_the_copy_before_building_the_original(tmp_path):
    """直接点后台编原版（不经过重新选源码）：副本里还插着计时点，先还原再编。"""
    builds, dev = [], FakeDevice()
    seen = []

    def builder(work, target, board, idf, progress):
        seen.append(has_fsw(work))
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = app(tmp_path, builds, dev, builder=builder)
    src = door_source(tmp_path / "door")
    r = pick(c, src, "a")
    wait_for(c, "done")
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert has_fsw(r["work"])

    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"], "board": "b"})
    wait_for(c, "done")

    assert seen == [False, False] and not has_fsw(r["work"])


def test_identify_reads_a_copy_without_old_timing_points(tmp_path):
    """上次没还原、换一句"要测的"再找关键步骤：大模型读到的是没插计时点的副本，行号和原代码一一对应。"""
    seen = []

    class Spy(FakeLLM):
        def investigate(self, prompt, root, on_text=None, on_tool=None):
            seen.append(has_fsw(root))
            return super().investigate(prompt, root, on_text, on_tool)
    c = app(tmp_path, [], FakeDevice(), llm=lambda p, k: Spy(reply()))
    src = door_source(tmp_path / "door")
    r = pick(c, src)
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    assert has_fsw(r["work"])

    c.post("/api/identify/start", json={"work": r["work"], "what": "从刷卡到开门", "project_dir": r["project_dir"],
                                        "fingerprint": r["fingerprint"], "reuse": False})
    for _ in range(200):
        if c.get("/api/identify").json()["state"] != "running":
            break
        time.sleep(0.02)

    assert seen[-1] is False and not has_fsw(r["work"])
    assert ins.compare(r["work"], src)["same"]


def test_insert_that_throws_says_so_and_can_be_retried(tmp_path, monkeypatch):
    """插的时候码表出了没料到的错：任务不卡在"进行中"，一句话说清楚，可以点"重新插入"。"""
    c = app(tmp_path, [], FakeDevice())
    src = door_source(tmp_path / "door")
    r = pick(c, src)
    real = ins.apply

    def boom(*a, **k):
        raise RuntimeError("磁盘出错")
    monkeypatch.setattr(ins, "apply", boom)
    start_insert(c, r)
    j = wait(c, lambda j: j["status"] in ("inserted", "failed"))
    assert j["status"] == "failed" and j["err"]["kind"] == "insert" and "重新插入" in j["err"]["message"]

    monkeypatch.setattr(ins, "apply", real)
    start_insert(c, r)
    assert wait(c, lambda j: j["status"] in ("inserted", "failed"))["status"] == "inserted"


def test_rebuilding_the_original_mid_test_then_build_puts_the_timing_points_back(tmp_path):
    """原版编译失败后点"重试"：后台先把副本里的计时点撤掉再编原版，接着编计时版时按这次的插入方案插回去。"""
    seen = []

    def builder(work, target, board, idf, progress):
        seen.append(has_fsw(work))
        fake_build_output(work)
        return {"ok": True, "seconds": 0.1, "error": None}
    c = app(tmp_path, [], FakeDevice(), builder=builder)
    r = pick(c, door_source(tmp_path / "door"), "a")
    wait_for(c, "done")
    start_insert(c, r)
    wait(c, lambda j: j["status"] == "inserted")
    c.post("/api/bgbuild/start", json={"work": r["work"], "chip": "ESP32-S3", "project_dir": r["project_dir"], "board": "b"})
    wait_for(c, "done")

    assert run_to_try(c)["status"] == "done"
    assert seen == [False, False, True] and has_fsw(r["work"])


def test_an_instrumented_build_is_never_saved_as_the_original(tmp_path):
    """编译目录里是计时版时不存成原版（固件里有计时点的输出格式），之前存的原版留着。"""
    def builder(work, target, board, idf, progress):
        fake_build_output(work)
        (Path(work) / "build/app.bin").write_bytes(b"FSW,RESET,%lld,0" if has_fsw(work) else b"ORIG")
        return {"ok": True, "seconds": 0.1, "error": None}
    c = app(tmp_path, [], FakeDevice(), builder=builder)
    r = pick(c, door_source(tmp_path / "door"))
    insert_and_run(c, r)
    orig = Path(r["project_dir"]) / "firmware/orig"
    assert (orig / "app.bin").read_bytes() == b"ORIG"
    (orig / "fsw-orig.json").unlink()  # 原版的记录没了，下次编计时版前会想再存一次原版

    assert run_to_try(c)["status"] == "done"
    assert (orig / "app.bin").read_bytes() == b"ORIG"
