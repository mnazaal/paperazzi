"""CLI runner for `pzi clean`."""

from __future__ import annotations

from pzi import cli_json, exit_codes
from pzi.clean_service import clean_library, validate_library
from pzi.cli_render import _error_lines, _render_clean_result
from pzi.commands.common import print_lines, resolve_target


def run_clean_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    if args.fix:
        result = clean_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
            dry_run=args.dry_run,
        )
    else:
        result = validate_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
        )

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="fix clean")
        if result["status"] != "ok":
            return exit_codes.ENVIRONMENT
        return exit_codes.OK if not result.get("issues") else exit_codes.FINDINGS

    if result["status"] != "ok":
        # ENVIRONMENT, not 1: the library could not be read, and 1 is reserved
        # for "ran fine, has something to report". Returning 1 here made an
        # unreadable bib indistinguishable from a handful of orphan PDFs.
        print_lines(
            _error_lines("clean failed", result.get("errors") or ["unparseable library"]),
            stderr,
        )
        return exit_codes.ENVIRONMENT

    print_lines(_render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return exit_codes.OK if not result.get("issues") else exit_codes.FINDINGS
