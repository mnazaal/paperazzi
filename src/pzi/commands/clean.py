"""CLI runner for `pzi clean`."""

from __future__ import annotations

import os

from pzi import cli_json, exit_codes
from pzi.clean_service import clean_library, validate_library
from pzi.cli_render import error_lines, render_clean_result
from pzi.commands.common import emit_usage_error, print_lines, print_read_warnings, resolve_target


def _siblings_sharing_papers_dir(config, target) -> list[str]:
    """The other configured libraries storing PDFs in *target*'s papers directory.

    The default layout gives every bib the same `papers_dir`, so without this a
    check of one library reported the others' PDFs as orphans — and `--fix`
    quarantined them, breaking `file =` fields in a library the user never named.
    """
    shared = os.path.realpath(target["papers_dir"])
    return [
        bib["path"]
        for bib in config.get("bibs", [])
        if bib["path"] != target["path"]
        and os.path.realpath(bib["papers_dir"]) == shared
    ]


def run_clean_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    if getattr(args, "dry_run", False) and not args.fix:
        # `--dry-run` previews what `--fix` would do. Without `--fix` the
        # command is already read-only, so the flag was accepted and ignored.
        return emit_usage_error(
            args,
            "--dry-run previews --fix; without it the run is already read-only",
            command_path=("fix", "clean"),
            stdout=stdout,
            stderr=stderr,
        )

    siblings = _siblings_sharing_papers_dir(_config, target)
    if args.fix:
        result = clean_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
            dry_run=args.dry_run, sibling_bib_paths=siblings,
        )
    else:
        result = validate_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
            sibling_bib_paths=siblings,
        )

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="fix clean", bib_name=target["name"])
        if result["status"] != "ok":
            return exit_codes.ENVIRONMENT
        return exit_codes.OK if not result.get("issues") else exit_codes.FINDINGS

    if result["status"] != "ok":
        # ENVIRONMENT, not 1: the library could not be read, and 1 is reserved
        # for "ran fine, has something to report". Returning 1 here made an
        # unreadable bib indistinguishable from a handful of orphan PDFs.
        print_lines(
            error_lines("clean failed", result.get("errors") or ["unparseable library"]),
            stderr,
        )
        return exit_codes.ENVIRONMENT

    print_read_warnings(result, stderr)
    print_lines(render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return exit_codes.OK if not result.get("issues") else exit_codes.FINDINGS
