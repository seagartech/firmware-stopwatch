import os
from pathlib import Path

import pytest

from fsw.workspace.workspace import ascii_work_root, fingerprint, prepare
from tests.test_inspect import idf_project


def test_work_folder_path_is_pure_english(tmp_path):
    root = ascii_work_root(tmp_path / "中文用户" / "数据")
    assert str(root).isascii()


def test_work_folder_uses_data_folder_when_it_is_english(tmp_path):
    assert ascii_work_root(tmp_path / "data") == tmp_path / "data" / "work"


def test_fingerprint_ignores_build_output_and_git(tmp_path):
    root = idf_project(tmp_path / "p")
    before = fingerprint(root)
    for d in ["build", ".git", "managed_components"]:
        (root / d).mkdir()
        (root / d / "x.o").write_text("junk")
    (root / "sdkconfig.old").write_text("CONFIG_X=y")  # sdkconfig 本身算进指纹（只改配置也算改了源码）
    (root / "dependencies.lock").write_text("x")

    assert fingerprint(root) == before


def test_fingerprint_changes_when_source_changes(tmp_path):
    root = idf_project(tmp_path / "p")
    before = fingerprint(root)
    (root / "main/main.c").write_text("void app_main(void) { int x; }\n")

    assert fingerprint(root) != before


def test_prepare_copies_to_english_path_and_leaves_original_alone(tmp_path):
    src = idf_project(tmp_path / "我的固件")
    original = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}

    r = prepare(src, data_dir=tmp_path / "数据")

    work = Path(r["work"])
    assert str(work).isascii()
    assert (work / "main/main.c").read_text() == "void app_main(void) {}\n"
    assert {p: p.read_bytes() for p in src.rglob("*") if p.is_file()} == original
    assert r["fingerprint"] == fingerprint(src)


def test_prepare_creates_project_folder_with_record(tmp_path):
    src = idf_project(tmp_path / "door")

    r = prepare(src, data_dir=tmp_path / "data")

    proj = Path(r["project_dir"])
    assert proj.parent == tmp_path / "data"
    rec = (proj / "project.json").read_text(encoding="utf-8")
    assert "door-ctrl" in rec and r["fingerprint"] in rec
    assert r["status"] == "new"


def test_same_source_again_is_recognized_as_unchanged(tmp_path):
    src = idf_project(tmp_path / "door")
    first = prepare(src, data_dir=tmp_path / "data")

    again = prepare(src, data_dir=tmp_path / "data")

    assert again["status"] == "unchanged"
    assert again["project_dir"] == first["project_dir"]


def test_same_project_in_another_folder_is_the_same_project(tmp_path):
    first = prepare(idf_project(tmp_path / "a" / "door"), data_dir=tmp_path / "data")
    moved = idf_project(tmp_path / "b" / "renamed-folder")
    (moved / "main/main.c").write_text("void app_main(void) { int y; }\n")

    again = prepare(moved, data_dir=tmp_path / "data")

    assert again["project_dir"] == first["project_dir"]
    assert again["status"] == "changed"


def test_different_project_gets_its_own_folder(tmp_path):
    a = prepare(idf_project(tmp_path / "a"), data_dir=tmp_path / "data")
    other = idf_project(tmp_path / "b")
    (other / "CMakeLists.txt").write_text((other / "CMakeLists.txt").read_text().replace("door-ctrl", "lamp"))

    b = prepare(other, data_dir=tmp_path / "data")

    assert a["project_dir"] != b["project_dir"]


def test_chosen_board_sets_the_chip(tmp_path):
    from tests.test_inspect import write, CMAKE
    src = write(tmp_path / "multi", {
        "CMakeLists.txt": CMAKE.replace("door-ctrl", "voice"),
        "main/boards/acme/s3-box/config.json": '{"target": "esp32s3"}',
        "main/boards/acme/c3-mini/config.json": '{"target": "esp32c3"}',
    })

    r = prepare(src, data_dir=tmp_path / "data", board="acme/s3-box")

    assert r["info"]["chip"] == "ESP32-S3"
    assert r["info"]["board"] == "acme/s3-box"


def test_unchanged_source_keeps_its_build_folder(tmp_path):
    src = idf_project(tmp_path / "door")
    first = prepare(src, data_dir=tmp_path / "data")
    (Path(first["work"]) / "build").mkdir()
    (Path(first["work"]) / "build" / "app.bin").write_text("built")

    again = prepare(src, data_dir=tmp_path / "data")

    assert again["status"] == "unchanged"
    assert (Path(again["work"]) / "build" / "app.bin").exists()


def test_retest_uses_the_board_chosen_last_time(tmp_path):
    """决策 130：同一个项目再测时，不再让人重新选板子：用上次选的，页面写明"按上次选的"，想换还能换。"""
    from tests.test_inspect import write, CMAKE
    src = write(tmp_path / "multi", {
        "CMakeLists.txt": CMAKE.replace("door-ctrl", "voice"),
        "main/boards/acme/s3-box/config.json": '{"target": "esp32s3"}',
        "main/boards/acme/c3-mini/config.json": '{"target": "esp32c3"}',
    })
    assert prepare(src, data_dir=tmp_path / "data")["status"] == "needs_board"  # 第一次：要选
    prepare(src, data_dir=tmp_path / "data", board="acme/c3-mini")
    again = prepare(src, data_dir=tmp_path / "data")  # 再测：没传板子
    assert again["status"] != "needs_board"
    assert again["info"]["board"] == "acme/c3-mini" and again["info"]["chip"] == "ESP32-C3"
    assert again["info"]["board_from"] == "last"
    assert again["info"]["boards"]  # 列表还在，想换能换
