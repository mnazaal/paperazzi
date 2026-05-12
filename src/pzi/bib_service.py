"""Bib administration services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi.config import AppConfig, BibConfig
from pzi.config_loader import load_config_file
from pzi.config_writer import dump_app_config

BibInfo: TypeAlias = dict[str, Any]



BibListResult: TypeAlias = dict[str, Any]



SetDefaultBibResult: TypeAlias = dict[str, Any]



def list_bibs(*, config_path: str, home_dir: str) -> BibListResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {"status": "error", "bibs": [], "errors": config_result["errors"]}
    config = config_result["config"]
    return {
        "status": "ok",
        "bibs": [
            {
                "name": bib["name"],
                "path": bib["path"],
                "papers_dir": bib["papers_dir"],
                "default": bib["default"],
            }
            for bib in config["bibs"]
        ],
        "errors": [],
    }


def set_default_bib(
    *, config_path: str, home_dir: str, name: str
) -> SetDefaultBibResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {
            "status": "error",
            "name": name,
            "message": "failed to load config",
            "errors": config_result["errors"],
        }
    config = config_result["config"]
    target = next((bib for bib in config["bibs"] if bib["name"] == name), None)
    if target is None:
        return {
            "status": "error",
            "name": name,
            "message": "bib not found",
            "errors": [f"no bib named {name}"],
        }

    updated_bibs = cast(
        list[BibConfig],
        [{**bib, "default": bib["name"] == name} for bib in config["bibs"]],
    )
    new_config = cast(AppConfig, {**dict(config), "bibs": updated_bibs})
    Path(config_path).write_text(dump_app_config(new_config), encoding="utf-8")
    return {
        "status": "ok",
        "name": name,
        "message": f"set default bib to {name}",
        "errors": [],
    }


