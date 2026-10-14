import re
import subprocess
import sys
from pathlib import Path

from fsw.config import load_config

REPO = Path(__file__).resolve().parent.parent
FAKE_DEEPSEEK = "fake-deepseek-key-for-tests-0001"
FAKE_CLAUDE = "fake-claude-key-for-tests-0002"


def test_reads_settings_and_key_files_from_config_folder(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[llm]\nprovider = "deepseek"\n'
        '[toolchain]\nidf_path = "/opt/esp-idf"\n'
        '[device]\nserial_port = "/dev/cu.usbmodem1"\n'
        '[output]\ndata_dir = "~/fsw-data"\n',
        encoding="utf-8",
    )
    (tmp_path / "deepseek_key.txt").write_text(FAKE_DEEPSEEK + "\n", encoding="utf-8")

    cfg = load_config(config_dir=tmp_path, env={})

    assert cfg.llm_provider == "deepseek"
    assert cfg.deepseek_api_key == FAKE_DEEPSEEK
    assert cfg.idf_path == "/opt/esp-idf"
    assert cfg.serial_port == "/dev/cu.usbmodem1"
    assert cfg.data_dir == Path.home() / "fsw-data"
    assert cfg.problems() == []


def test_environment_variables_override_files(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "deepseek"\n', encoding="utf-8")
    (tmp_path / "deepseek_key.txt").write_text("file-key", encoding="utf-8")

    cfg = load_config(
        config_dir=tmp_path,
        env={
            "DEEPSEEK_API_KEY": FAKE_DEEPSEEK,
            "ANTHROPIC_API_KEY": FAKE_CLAUDE,
            "FSW_LLM_PROVIDER": "claude-api",
            "FSW_DATA_DIR": str(tmp_path / "data"),
        },
    )

    assert cfg.llm_provider == "claude-api"
    assert cfg.deepseek_api_key == FAKE_DEEPSEEK
    assert cfg.claude_api_key == FAKE_CLAUDE
    assert cfg.data_dir == tmp_path / "data"


def test_config_folder_can_be_moved_by_environment_variable(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "claude-code"\n', encoding="utf-8")

    cfg = load_config(env={"FSW_CONFIG_DIR": str(tmp_path)})

    assert cfg.llm_provider == "claude-code"


def test_defaults_when_nothing_is_configured(tmp_path):
    cfg = load_config(config_dir=tmp_path / "missing", env={})

    assert cfg.llm_provider is None
    assert cfg.claude_api_key is None
    assert cfg.deepseek_api_key is None
    assert cfg.serial_port is None
    assert cfg.data_dir == Path.home() / "firmware-stopwatch-data"


def test_plain_hint_when_no_model_is_chosen(tmp_path):
    problems = load_config(config_dir=tmp_path, env={}).problems()

    assert len(problems) == 1
    assert "还没选大模型" in problems[0]


def test_plain_hint_when_chosen_model_has_no_key(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "deepseek"\n', encoding="utf-8")

    problems = load_config(config_dir=tmp_path, env={}).problems()

    assert len(problems) == 1
    assert "还没填 DeepSeek 的密钥" in problems[0]
    assert "deepseek_key.txt" in problems[0]


def test_claude_code_needs_no_key(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "claude-code"\n', encoding="utf-8")

    assert load_config(config_dir=tmp_path, env={}).problems() == []


def test_unknown_model_name_gets_plain_hint(tmp_path):
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "gpt"\n', encoding="utf-8")

    problems = load_config(config_dir=tmp_path, env={}).problems()

    assert "不认识的大模型" in problems[0]


def test_broken_settings_file_gets_plain_hint_instead_of_crash(tmp_path):
    (tmp_path / "config.toml").write_text("[llm\nprovider = ", encoding="utf-8")

    problems = load_config(config_dir=tmp_path, env={}).problems()

    assert any("设置文件写得不对" in p for p in problems)


def test_keys_never_show_up_when_config_is_printed(tmp_path):
    cfg = load_config(
        config_dir=tmp_path,
        env={"DEEPSEEK_API_KEY": FAKE_DEEPSEEK, "ANTHROPIC_API_KEY": FAKE_CLAUDE},
    )

    assert FAKE_DEEPSEEK not in repr(cfg)
    assert FAKE_CLAUDE not in repr(cfg)


def test_program_start_prints_plain_hint_when_not_configured(tmp_path):
    env = {"FSW_CONFIG_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, "-m", "fsw", "--check"], capture_output=True, text=True, env=env
    )

    assert result.returncode == 0, result.stderr
    assert "还没选大模型" in result.stdout


def test_example_config_matches_what_the_program_reads(tmp_path):
    example = (REPO / "config.example.toml").read_text(encoding="utf-8")
    (tmp_path / "config.toml").write_text(example, encoding="utf-8")

    cfg = load_config(config_dir=tmp_path, env={})

    assert not any("设置文件写得不对" in p for p in cfg.problems())
    assert cfg.llm_provider in (None, "claude-api", "claude-code", "deepseek")


SECRET_OR_LOCAL = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"/Users/[^/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
    re.compile(r"[A-Za-z]:\\\\?Users\\\\?"),
]


def test_code_has_no_hardcoded_keys_or_local_paths():
    files = list((REPO / "fsw").rglob("*.py")) + [REPO / "config.example.toml"]
    hits = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for pattern in SECRET_OR_LOCAL:
            for m in pattern.finditer(text):
                hits.append(f"{f.relative_to(REPO)}: {m.group(0)}")
    assert hits == []
