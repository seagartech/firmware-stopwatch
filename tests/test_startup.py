import shutil
import subprocess
import sys


def test_program_starts_and_prints_version():
    result = subprocess.run(
        [sys.executable, "-m", "fsw", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "firmware-stopwatch 0.1.0"


def test_installed_command_starts_from_any_folder(tmp_path):
    fsw = shutil.which("fsw")
    assert fsw, "fsw 命令没装上"
    result = subprocess.run(
        [fsw, "--version"], capture_output=True, text=True, cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "firmware-stopwatch 0.1.0"


def test_busy_port_moves_to_the_next_one():
    import socket

    from fsw.__main__ import free_port

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        assert free_port(busy) == busy + 1
