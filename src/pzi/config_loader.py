"""Thin TOML config loading wrappers around pure config validation."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, TypeAlias

from pzi.config import validate_app_config

DEFAULT_CONFIG_RELATIVE_PATH = ".config/pzi/config.toml"


LoadConfigResult: TypeAlias = dict[str, Any]



def default_config_path(home_dir: str) -> str:
    """Return the default TOML config path under the given home directory."""
    return str(Path(home_dir) / DEFAULT_CONFIG_RELATIVE_PATH)


def load_config_file(path: str, *, home_dir: str) -> LoadConfigResult:
    """Load, parse, and validate a TOML config file."""
    config_path = Path(path)
    if not config_path.exists():
        return {
            "config": None,
            "errors": [f"config file not found: {config_path}"],
            "path": str(config_path),
        }

    try:
        raw_bytes = config_path.read_bytes()
    except OSError as exc:
        return {
            "config": None,
            "errors": [f"failed to read config file: {exc}"],
            "path": str(config_path),
        }

    try:
        raw_config = tomllib.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return {
            "config": None,
            "errors": ["config file must be valid UTF-8 text"],
            "path": str(config_path),
        }
    except tomllib.TOMLDecodeError as exc:
        return {
            "config": None,
            "errors": [f"invalid TOML: {exc}"],
            "path": str(config_path),
        }

    config, errors = validate_app_config(raw_config, home_dir=home_dir)
    return {"config": config, "errors": errors, "path": str(config_path)}


def load_default_config(*, home_dir: str) -> LoadConfigResult:
    """Load config from the default path under the given home directory."""
    return load_config_file(default_config_path(home_dir), home_dir=home_dir)
