"""CLI runners for duplicate detection and merge commands."""

from __future__ import annotations

import json

from pzi.cli_render import _error_lines, _render_dedupe_result
from pzi.commands.common import print_lines, resolve_target_or_error
from pzi.dedupe_service import find_duplicates, merge_duplicates


def run_dedupe_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        stderr=stderr,
    )
    if resolved is None:
        return 1
    _config, target = resolved

    result = find_duplicates(bib_path=target["path"])
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        return 0 if result.get("total_clusters", 0) == 0 else 1
    print_lines(_render_dedupe_result(result), stdout)
    return 0 if result.get("total_clusters", 0) == 0 else 1


def run_merge_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        stderr=stderr,
    )
    if resolved is None:
        return 1
    config, target = resolved

    result = merge_duplicates(
        bib_path=target["path"],
        citekey_a=args.citekey_a,
        citekey_b=args.citekey_b,
        dry_run=getattr(args, "dry_run", False),
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )
    if result["status"] != "ok":
        print_lines(_error_lines(result["message"], []), stderr)
        return 1
    print(result["message"], file=stdout)
    return 0
