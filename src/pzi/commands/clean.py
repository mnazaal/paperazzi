"""CLI runner for `pzi clean`."""

from __future__ import annotations

import json

from pzi.clean_service import clean_library, validate_library
from pzi.cli_render import _error_lines, _render_clean_result
from pzi.commands.common import print_lines, resolve_target_or_error


def run_clean_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector, stderr=stderr,
    )
    if resolved is None:
        return 1
    _config, target = resolved

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
        print(json.dumps(result, indent=2, default=str), file=stdout)
        if result["status"] != "ok":
            return 1
        return 0 if not result.get("issues") else 1

    if result["status"] != "ok":
        print_lines(_error_lines("clean failed", [result.get("message", "")]), stderr)
        return 1

    print_lines(_render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return 0 if not result.get("issues") else 1
