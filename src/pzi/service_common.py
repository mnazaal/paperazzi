"""Shared helpers for application service modules."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pzi.config import AppConfig, BibConfig, resolve_bib
from pzi.config_loader import load_config_file

_YEAR_PATTERN = re.compile(r"(19|20)\d{2}")


def _extract_year_from_str(value: str) -> int | None:
    """Extract a four-digit year string from a date string, or None."""
    match = _YEAR_PATTERN.search(value)
    return int(match.group(0)) if match else None


def _find_entry_index(entries: Sequence[dict[str, Any]], citekey: str) -> int | None:
    """Return index of first entry with the given citekey, or None."""
    return next(
        (i for i, entry in enumerate(entries) if entry["citekey"] == citekey),
        None,
    )


def load_and_resolve_bib(
    *, config_path: str, home_dir: str, bib_selector: str | None
) -> tuple[AppConfig, BibConfig] | list[str]:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return config_result["errors"]
    config = config_result["config"]
    bib = resolve_bib(config["bibs"], bib_selector)
    if bib is None:
        return ["no matching bib found or selection is ambiguous"]
    return config, bib
