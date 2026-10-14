from pathlib import Path

import pytest

from fsw.platforms.espidf import EspIdf, find_idf
from fsw.platforms.espidf.install import MacInstaller, choose_version, split_progress_lines, windows_command
from tests.test_espidf import idf_install


def test_version_follows_project_requirement():
    assert choose_version("6.0.1") == "v6.1"
    assert choose_version("5.3") == "v6.1"
    assert choose_version(None) == "v6.1"
    assert choose_version("9.0") is None


def test_git_and_download_progress_are_split_on_carriage_returns():
    data = ["Receiving objects:  10% (10/100)\rReceiving objects:  55% (55/100)\rReceiving objects: 100% (100/100), done.\n"]
    assert list(split_progress_lines(data)) == ["Receiving objects:  10% (10/100)", "Receiving objects:  55% (55/100)",
                                                "Receiving objects: 100% (100/100), done."]


class Script:
    """按命令关键字回放事先录好的输出（照乐鑫官方工具真实的样子）。"""
    def __init__(self, plans):
        self.plans, self.calls = plans, []

    def __call__(self, cmd, cwd, env):
        self.calls.append((cmd, env))
        line = " ".join(map(str, cmd))
        for key, out, code, effect in self.plans:
            if key in line:
                if effect:
                    effect(cmd)
                yield from out
                return code
        raise AssertionError(f"没录这个命令：{line}")


def fake_clone(cmd):
    dest = Path(cmd[-1])
    idf_install(dest, 6, 1)
    (dest / ".git").mkdir()
    (dest / ".gitmodules").write_text('[submodule "a"]\n\tpath = components/a\n[submodule "b"]\n\tpath = components/b\n')
    (dest / "examples/get-started/hello_world").mkdir(parents=True)
    (dest / "examples/get-started/hello_world/CMakeLists.txt").write_text("project(hello_world)\n")


OK_PLANS = [
    ("xcode-select", ["/Library/Developer/CommandLineTools"], 0, None),
    ("clone", ["Cloning into 'esp-idf'...", "Receiving objects:  40% (400/1000)", "Receiving objects: 100% (1000/1000), done."], 0, fake_clone),
    ("submodule", ["Submodule path 'components/a': checked out 'abc'", "Submodule path 'components/b': checked out 'def'"], 0, None),
    ("install.sh", ["Installing ESP-IDF tools", "Installing tools: xtensa-esp-elf, cmake, ninja",
                    "Installing xtensa-esp-elf@esp-15.2.0", "Downloading https://dl.espressif.com/xtensa.tar.xz", "37%", "100%",
                    "Installing cmake@3.30.2", "Skipping ninja@1.12.1 (already installed)",
                    "Installing Python environment and packages", "Installing collected packages: esptool",
                    "All done! You can now run:"], 0, None),
]


def run_install(tmp_path, plans, events=None):
    script = Script(plans)
    built = []
    inst = MacInstaller(tmp_path / "idf", "v6.1", tools_path=tmp_path / "tools", runner=script, work_dir=tmp_path / "work",
                        which=lambda n: f"/usr/bin/{n}",
                        try_build=lambda idf, ex, tools, cb: built.append((str(idf), str(ex))) or {"ok": True, "seconds": 3.0})
    r = inst.run(on_event=(events.append if events is not None else lambda e: None))
    return r, script, built


def test_mac_install_goes_through_the_five_steps_with_progress(tmp_path):
    events = []
    r, script, built = run_install(tmp_path, OK_PLANS, events)
    assert r["ok"] and r["path"] == str(tmp_path / "idf")
    steps = [e["step"] for e in events]
    assert steps == sorted(steps) and set(steps) == {0, 1, 2, 3, 4}
    assert any(e["step"] == 1 and e["pct"] == 40 for e in events)
    assert any(e["step"] == 2 and e["pct"] > 0 and "xtensa" in e["log"] for e in events)
    assert events[-1]["state"] == "done"
    assert built and built[0][1].startswith(str(tmp_path))  # 试编译在码表自己的目录，不写进编译工具里
    assert (tmp_path / "idf/.fsw_tools_path").read_text().strip() == str(tmp_path / "tools")
    install_env = next(env for cmd, env in script.calls if "install.sh" in " ".join(map(str, cmd)))
    assert install_env["IDF_TOOLS_PATH"] == str(tmp_path / "tools")


def test_offline_failure_says_which_step_and_why(tmp_path):
    plans = [OK_PLANS[0], ("clone", ["Cloning into 'esp-idf'...", "fatal: unable to access 'https://github.com/espressif/esp-idf.git/': Could not resolve host: github.com"], 128, None)]
    r, _, _ = run_install(tmp_path, plans)
    assert not r["ok"] and r["step"] == 1
    assert "第 2 步" in r["message"] and "下载乐鑫编译工具" in r["message"] and "连不上" in r["message"]
    assert "Could not resolve host" in r["log"]


def test_retry_continues_from_where_it_stopped(tmp_path):
    fake_clone(["git", tmp_path / "idf"])  # 上次已经下好了主体
    plans = [p for p in OK_PLANS if p[0] != "clone"]
    r, script, _ = run_install(tmp_path, plans)
    assert r["ok"]
    assert not any("clone" in " ".join(map(str, c)) for c, e in script.calls), "已下好的不该重新下"


def test_half_finished_download_is_restarted(tmp_path):
    (tmp_path / "idf").mkdir()
    (tmp_path / "idf/half.txt").write_text("x")
    r, script, _ = run_install(tmp_path, OK_PLANS)
    assert r["ok"] and any("clone" in " ".join(map(str, c)) for c, e in script.calls)


def test_missing_git_is_explained(tmp_path):
    inst = MacInstaller(tmp_path / "idf", "v6.1", runner=Script(OK_PLANS), which=lambda n: None if n == "git" else "/usr/bin/" + n)
    r = inst.run(on_event=lambda e: None)
    assert not r["ok"] and r["step"] == 0 and "git" in r["message"]


def test_installed_idf_uses_its_own_tools_folder(tmp_path):
    idf = idf_install(tmp_path / "idf", 6, 1)
    (idf / ".fsw_tools_path").write_text(str(tmp_path / "tools"))
    assert EspIdf(idf).export_env()["IDF_TOOLS_PATH"] == str(tmp_path / "tools")


def test_install_record_makes_it_findable(tmp_path):
    idf = idf_install(tmp_path / "somewhere/idf", 6, 1)
    assert [f["version"] for f in find_idf(home=tmp_path / "h", env={}, extra=[str(idf)])] == ["6.1.0"]


def test_windows_uses_official_eim_command_line(tmp_path):
    cmd = windows_command("v6.1", Path("C:/esp"))
    assert cmd[0].lower().endswith("eim.exe") and "install" in cmd and "-i" in cmd and "v6.1" in cmd


def test_interrupted_download_is_named_and_log_is_not_repeated(tmp_path):
    plans = [OK_PLANS[0], OK_PLANS[1], OK_PLANS[2],
             ("install.sh", ["Installing ESP-IDF tools", "Installing tools: xtensa-esp-elf, cmake",
                             "Installing xtensa-esp-elf@esp-15.2.0", "65%", "66%", "66%", "66%",
                             "install.sh: line 31: 94165 Terminated: 15  idf_tools.py install"], 143, None)]
    r, _, _ = run_install(tmp_path, plans)
    assert r["step"] == 2 and "打断" in r["message"] and "重试" in r["message"]
    assert r["log"].count("66%") <= 1
