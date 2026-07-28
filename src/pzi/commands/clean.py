"""CLI runner for `pzi clean`."""

from __future__ import annotations

from pzi import cli_json
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
            return 1
        return 0 if not result.get("issues") else 1

    if result["status"] != "ok":
        # `CleanResult` carries no "message" key — the detail lives in the
        # issues list, which is where the parse error is recorded.
        details = [str(issue.get("message", "")) for issue in result.get("issues", [])]
        print_lines(_error_lines("clean failed", details or ["unparseable library"]), stderr)
        return 1

    print_lines(_render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return 0 if not result.get("issues") else 1
