"""Metadata update CLI command runner (with optional preprint promotion)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi.cli_parser import usage_error_lines
from pzi.cli_render import (
    _error_lines,
    _render_bib_promote_items,
    _render_bib_update_items,
)
from pzi.commands.common import (
    print_lines,
    print_metadata_diagnostics,
    print_metadata_warnings,
    print_result_item_diffs,
    target_list,
)
from pzi.promote_service import promote_bib
from pzi.update_service import update_bib

Result = Mapping[str, Any]
Service = Callable[..., Result]


def run_update_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    update_bib_fn: Service = update_bib,
    promote_bib_fn: Service = promote_bib,
) -> int:
    """Run `pzi update`, dispatching to promotion when --promote is given.

    Without --promote, conservatively fills missing metadata.  With --promote,
    replaces preprints with their published versions (keeping both by default,
    or in place with --replace).
    """
    promote = getattr(args, "promote", False)
    if getattr(args, "replace", False) and not promote:
        print_lines(
            usage_error_lines(("update",), "--replace only applies with --promote"), stderr
        )
        return 2
    mark_resolved = getattr(args, "mark_resolved", False)
    if mark_resolved and not promote:
        print_lines(
            usage_error_lines(("update",), "--mark-resolved only applies with --promote"), stderr
        )
        return 2

    ok = True
    for target in target_list(args.target):
        if promote:
            result = promote_bib_fn(
                config_path=config_path,
                home_dir=home_dir,
                bib_selector=target,
                dry_run=args.dry_run,
                keep_preprint=not args.replace,
                mark_resolved=mark_resolved,
            )
            render = _render_bib_promote_items
            failure = "promote failed"
        else:
            result = update_bib_fn(
                config_path=config_path,
                home_dir=home_dir,
                bib_selector=target,
                dry_run=args.dry_run,
            )
            render = _render_bib_update_items
            failure = "update failed"

        if result["status"] == "ok":
            print_lines(render(result), stdout)
            if args.dry_run:
                print_result_item_diffs(result, stdout)
            print_metadata_warnings(result, stderr)
            if args.verbose:
                print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            print_lines(_error_lines(failure, result["errors"]), stderr)
    return 0 if ok else 1
