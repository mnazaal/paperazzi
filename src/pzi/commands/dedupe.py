"""CLI runners for duplicate detection and merge commands."""

from __future__ import annotations

from pzi import cli_json, exit_codes
from pzi.cli_render import _error_lines, _render_dedupe_result
from pzi.commands.common import print_lines, resolve_target
from pzi.dedupe_service import find_duplicates, merge_duplicates


def run_dedupe_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )

    result = find_duplicates(bib_path=target["path"])
    # `total_clusters` counts exact clusters only, so it cannot stand in for
    # "has something to report" — a library whose sole finding is a fuzzy
    # near-duplicate still owes the caller exit 1.
    findings = result.get("total_clusters", 0) + len(result.get("fuzzy_candidates", []))
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="fix dedupe")
        return 0 if findings == 0 else 1
    print_lines(_render_dedupe_result(result), stdout)
    return 0 if findings == 0 else 1


def run_merge_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    config, target = resolve_target(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )

    result = merge_duplicates(
        bib_path=target["path"],
        citekey_a=args.citekey_a,
        citekey_b=args.citekey_b,
        dry_run=getattr(args, "dry_run", False),
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="fix merge", items=[])
        return exit_codes.OK if result["status"] == "ok" else exit_codes.ENVIRONMENT
    if result["status"] != "ok":
        print_lines(_error_lines(result["message"], []), stderr)
        return exit_codes.ENVIRONMENT
    print(result["message"], file=stdout)
    return exit_codes.OK
