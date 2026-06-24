"""Shared CLI command helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from pzi.cli_render import _error_lines
from pzi.config import load_config_file, resolve_library_target


def print_lines(lines: Sequence[str], out: TextIO) -> None:
    """Print rendered CLI lines to a stream."""
    for line in lines:
        print(line, file=out)


def resolve_target_or_error(
    *, config_path: str, home_dir: str, bib_selector: str | None, stderr: TextIO,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Load config and resolve a single library target, printing errors on failure.

    Returns ``(config, target)`` or ``None`` (after printing the error).  Shared by
    the library-maintenance command runners so each one resolves identically.
    """
    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return None
    target = resolve_library_target(cfg["config"]["bibs"], bib_selector, home_dir=home_dir)
    if target is None:
        print_lines(_error_lines("bib not found", []), stderr)
        return None
    return cfg["config"], target


def target_list(target: Sequence[str] | None) -> list[str | None]:
    """Normalize optional repeated --target values for command loops."""
    return list(target) if target else [None]


def print_result_item_diffs(result: Mapping[str, object], stdout: TextIO) -> None:
    """Print per-item dry-run diffs when present."""
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        diff = item.get("diff")
        if not isinstance(diff, str) or not diff:
            continue
        print(diff, file=stdout, end="" if diff.endswith("\n") else "\n")


def metadata_diagnostic_lines(result: Mapping[str, object]) -> list[str]:
    """Collect result-level and item-level metadata diagnostics."""
    direct = result.get("metadata_diagnostics")
    if isinstance(direct, list):
        return [line for line in direct if isinstance(line, str)]
    lines: list[str] = []
    items = result.get("items")
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, Mapping):
            continue
        diagnostics = item.get("metadata_diagnostics")
        if not isinstance(diagnostics, list):
            continue
        lines.extend(line for line in diagnostics if isinstance(line, str))
    return lines


def print_metadata_diagnostics(result: Mapping[str, object], stdout: TextIO) -> None:
    """Print verbose metadata diagnostics block."""
    lines = metadata_diagnostic_lines(result)
    if not lines:
        return
    print("metadata diagnostics:", file=stdout)
    for line in lines:
        print(f"  {line}", file=stdout)


def metadata_warning_lines(result: Mapping[str, object]) -> list[str]:
    """Collect result-level and item-level metadata confidence warnings."""
    lines: list[str] = []
    direct = result.get("metadata_warnings")
    if isinstance(direct, list):
        lines.extend(line for line in direct if isinstance(line, str))
    items = result.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            warnings = item.get("metadata_warnings")
            if isinstance(warnings, list):
                lines.extend(line for line in warnings if isinstance(line, str))
    return lines


def print_metadata_warnings(result: Mapping[str, object], stderr: TextIO) -> None:
    """Print metadata confidence warnings.

    Advisory ("verify this candidate") messages that services attach to items;
    shown always (not gated on --verbose) so low-confidence writes are visible.
    """
    for line in metadata_warning_lines(result):
        print(f"warning: {line}", file=stderr)
