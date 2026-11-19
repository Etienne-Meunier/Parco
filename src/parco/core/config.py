"""Read and write `.parco/config.toml`."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from parco.core.errors import ConfigError

CONFIG_NAME = "config.toml"


@dataclass
class Config:
    """Project configuration. Paths are relative to the project root."""

    main: str
    context: list[str] = field(default_factory=list)
    bib: str | None = None


def _toml_string(value: str) -> str:
    # JSON string escapes are a subset of TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)


def dump_config(config: Config) -> str:
    """Render the configuration as TOML text."""
    lines = [
        f"main = {_toml_string(config.main)}",
        "context = [" + ", ".join(_toml_string(c) for c in config.context) + "]",
    ]
    if config.bib:
        lines.append(f"bib = {_toml_string(config.bib)}")
    return "\n".join(lines) + "\n"


def write_config(parco_dir: Path, config: Config) -> None:
    """Write the configuration file into the `.parco` folder."""
    (parco_dir / CONFIG_NAME).write_text(dump_config(config), encoding="utf-8")


def read_config(parco_dir: Path) -> Config:
    """Load and validate the configuration of the `.parco` folder."""
    path = parco_dir / CONFIG_NAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing configuration file {path}.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid configuration file {path}: {exc}") from exc
    main = data.get("main")
    context = data.get("context", [])
    bib = data.get("bib")
    if not isinstance(main, str) or not main:
        raise ConfigError(f"{path}: `main` must be a non-empty string.")
    if not isinstance(context, list) or not all(isinstance(c, str) for c in context):
        raise ConfigError(f"{path}: `context` must be a list of strings.")
    if bib is not None and not isinstance(bib, str):
        raise ConfigError(f"{path}: `bib` must be a string.")
    return Config(main=main, context=context, bib=bib or None)
