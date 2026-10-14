import re
from pathlib import Path

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def section(title):
    m = re.search(rf"^## {title}\n(.*?)(?=^## |\Z)", README, re.S | re.M)
    assert m, f"README 里没有「{title}」这一段"
    return m.group(1)


def test_mac_section_goes_from_install_to_start():
    mac = section("在 Mac 上安装")
    for step in ("brew install uv", "git clone", "uv run fsw --version"):
        assert step in mac


def test_windows_section_goes_from_install_to_start():
    win = section("在 Windows 上安装")
    for step in ("install.ps1", "git clone", "uv run fsw --version"):
        assert step in win
    assert "还没在 Windows 上实测" in win


def test_readme_says_where_settings_and_keys_live():
    cfg = section("设置和密钥")
    assert "config.example.toml" in cfg
    assert "deepseek_key.txt" in cfg
