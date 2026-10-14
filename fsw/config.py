"""读设置和密钥：~/.config/firmware-stopwatch/ 里的文件，环境变量优先。

密钥不写进 config.toml，单独放成 claude_key.txt、deepseek_key.txt，
或用环境变量 ANTHROPIC_API_KEY、DEEPSEEK_API_KEY。
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROVIDERS = {
    "claude-api": "Claude（API 密钥）",
    "claude-code": "Claude（本机已登录的 Claude Code）",
    "deepseek": "DeepSeek",
}
KEY_FILES = {"claude-api": "claude_key.txt", "deepseek": "deepseek_key.txt"}
KEY_ENV = {"claude-api": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


def default_config_dir(env=None):
    env = os.environ if env is None else env
    if env.get("FSW_CONFIG_DIR"):
        return Path(env["FSW_CONFIG_DIR"]).expanduser()
    return Path.home() / ".config" / "firmware-stopwatch"


@dataclass
class Config:
    config_dir: Path
    llm_provider: str | None = None
    claude_api_key: str | None = field(default=None, repr=False)
    deepseek_api_key: str | None = field(default=None, repr=False)
    idf_path: str | None = None
    serial_port: str | None = None
    data_dir: Path = field(default_factory=lambda: Path.home() / "firmware-stopwatch-data")
    file_error: str | None = None

    def api_key(self, provider):
        return {"claude-api": self.claude_api_key, "deepseek": self.deepseek_api_key}.get(provider)

    def problems(self):
        """没配好的地方，每条一句大白话。"""
        out = []
        if self.file_error:
            out.append(
                f"设置文件写得不对，先按默认设置走：{self.config_dir / 'config.toml'}"
                f"（{self.file_error}）。可以在设置页重新保存一次。"
            )
        if self.llm_provider is None:
            out.append(
                "还没选大模型：找关键步骤要用它。到设置页选一个"
                "（Claude、本机的 Claude Code 或 DeepSeek）。"
            )
        elif self.llm_provider not in PROVIDERS:
            out.append(
                f"不认识的大模型「{self.llm_provider}」：只支持 "
                + "、".join(PROVIDERS) + "，到设置页重新选。"
            )
        elif self.llm_provider in KEY_FILES and not self.api_key(self.llm_provider):
            name = "Claude" if self.llm_provider == "claude-api" else "DeepSeek"
            out.append(
                f"还没填 {name} 的密钥：到设置页填上，"
                f"或者放在 {self.config_dir / KEY_FILES[self.llm_provider]}，"
                f"也可以设环境变量 {KEY_ENV[self.llm_provider]}。"
            )
        return out


def _read_key(path):
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def load_config(config_dir=None, env=None):
    env = os.environ if env is None else env
    config_dir = Path(config_dir) if config_dir else default_config_dir(env)
    cfg = Config(config_dir=config_dir)

    data = {}
    toml_path = config_dir / "config.toml"
    if toml_path.exists():
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            cfg.file_error = str(e)

    def setting(section, key):
        value = data.get(section, {}).get(key)
        return value or None

    cfg.llm_provider = env.get("FSW_LLM_PROVIDER") or setting("llm", "provider")
    cfg.idf_path = env.get("IDF_PATH") or setting("toolchain", "idf_path")
    cfg.serial_port = env.get("FSW_SERIAL_PORT") or setting("device", "serial_port")
    data_dir = env.get("FSW_DATA_DIR") or setting("output", "data_dir")
    if data_dir:
        cfg.data_dir = Path(data_dir).expanduser()

    cfg.claude_api_key = env.get(KEY_ENV["claude-api"]) or _read_key(
        config_dir / KEY_FILES["claude-api"]
    )
    cfg.deepseek_api_key = env.get(KEY_ENV["deepseek"]) or _read_key(
        config_dir / KEY_FILES["deepseek"]
    )
    return cfg


SECTIONS = {"provider": ("llm", "provider"), "idf_path": ("toolchain", "idf_path"),
            "serial_port": ("device", "serial_port"), "data_dir": ("output", "data_dir")}


def _toml_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(config_dir, *, provider=None, key=None, idf_path=None, serial_port=None, data_dir=None):
    """设置页的"保存"：没传的项保持原样。密钥单独写成只有自己能读的文件，不进 config.toml。"""
    config_dir = Path(config_dir).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    toml_path = config_dir / "config.toml"
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        data = {}
    given = {"provider": provider, "idf_path": idf_path, "serial_port": serial_port, "data_dir": data_dir}
    for name, value in given.items():
        if value is not None:
            sec, k = SECTIONS[name]
            data.setdefault(sec, {})[k] = value
    lines = []
    for sec in ("llm", "toolchain", "device", "output"):
        items = {k: v for k, v in data.get(sec, {}).items() if isinstance(v, str)}
        if items:
            lines += [f"[{sec}]"] + [f"{k} = {_toml_str(v)}" for k, v in items.items()] + [""]
    toml_path.write_text("\n".join(lines), encoding="utf-8")
    if key and (provider or data.get("llm", {}).get("provider")) in KEY_FILES:
        p = config_dir / KEY_FILES[provider or data["llm"]["provider"]]
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key.strip() + "\n")
        os.chmod(p, 0o600)
