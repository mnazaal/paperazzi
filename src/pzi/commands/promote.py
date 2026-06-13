"""Preprint promotion CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi.cli_render import _error_lines, _render_bib_promote_items
from pzi.commands.common import (
    print_lines,
    print_metadata_diagnostics,
    print_metadata_warnings,
    print_result_item_diffs,
    target_list,
)
from pzi.promote_service import promote_bib

Result = Mapping[str, Any]
PromoteService = Callable[..., Result]


def run_promote_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    promote_bib_fn: PromoteService = promote_bib,
) -> int:
    """Run `pzi promote` using injected service for thin-I/O testing."""
    ok = True
    for target in target_list(args.target):
        result = promote_bib_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            dry_run=args.dry_run,
            keep_preprint=not args.replace,
        )
        if result["status"] == "ok":
            print_lines(_render_bib_promote_items(result), stdout)
            if args.dry_run:
                print_result_item_diffs(result, stdout)
            print_metadata_warnings(result, stderr)
            if args.verbose:
                print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            print_lines(_error_lines("promote failed", result["errors"]), stderr)
    return 0 if ok else 1
