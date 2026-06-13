"""Metadata update CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi.cli_render import _error_lines, _render_bib_update_items
from pzi.commands.common import (
    print_lines,
    print_metadata_diagnostics,
    print_metadata_warnings,
    print_result_item_diffs,
    target_list,
)
from pzi.update_service import update_bib

Result = Mapping[str, Any]
UpdateService = Callable[..., Result]


def run_update_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    update_bib_fn: UpdateService = update_bib,
) -> int:
    """Run `pzi update` using injected service for thin-I/O testing."""
    ok = True
    for target in target_list(args.target):
        result = update_bib_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            dry_run=args.dry_run,
        )
        if result["status"] == "ok":
            print_lines(_render_bib_update_items(result), stdout)
            if args.dry_run:
                print_result_item_diffs(result, stdout)
            print_metadata_warnings(result, stderr)
            if args.verbose:
                print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            print_lines(_error_lines("update failed", result["errors"]), stderr)
    return 0 if ok else 1
