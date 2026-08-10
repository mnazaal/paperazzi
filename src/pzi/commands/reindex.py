"""CLI runner for `pzi fix reindex`."""

from __future__ import annotations

import sys

from pzi import cli_json, exit_codes
from pzi.cli_render import _error_lines, _render_reindex_result
from pzi.commands.common import (
    emit_usage_error,
    print_lines,
    print_read_warnings,
    resolve_target,
)
from pzi.reindex_service import reindex_library


def run_reindex_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    as_json = getattr(args, "json", False)
    rename = getattr(args, "rename_citekeys", False)
    # Default is a read-only audit: keep citekeys stable unless explicitly asked.
    apply = rename and not args.dry_run
    if apply:
        print(
            "warning: rewriting citekeys will break any \\cite{} references that use "
            "the old keys (in LaTeX documents, notes, etc.).",
            file=stderr,
        )
    if apply and not getattr(args, "force", False):
        # Same gate as `delete`: this rewrites every citekey in the library and
        # the damage lands outside pzi, in whatever cites them. Never prompt
        # into a pipe — reading the answer would eat a line of the caller's
        # data, and answering "no" for them turns a forgotten --force into a
        # silent no-op reported as success.
        if not sys.stdin.isatty():
            return emit_usage_error(
                args,
                "refusing to prompt for confirmation with stdin not a terminal; "
                "pass --force to rewrite citekeys or --dry-run to preview",
                command_path=("fix", "reindex"),
                stdout=stdout,
                stderr=stderr,
            )
        print(
            f"Rewrite every citekey in {target['path']}? [y/N] ", end="", file=stderr
        )
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            if as_json:
                cli_json.emit_result(
                    {"status": "ok", "bib_path": target["path"], "message": "cancelled"},
                    stdout, command="fix reindex", items=[],
                )
            else:
                print("cancelled", file=stderr)
            return exit_codes.OK

    result = reindex_library(
        bib_path=target["path"],
        papers_dir=target["papers_dir"],
        citekey_format=config.get("citekey_format"),
        pdf_filename_format=config.get("pdf_filename_format"),
        dry_run=not apply,
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )

    # Computed once above the format branch, as `fix dedupe` does. Keying only
    # off `errors` meant a read-only audit that found renames to make exited 0
    # while simultaneously printing "run with --rename-citekeys to apply" —
    # nothing to report, according to the exit code. Renames only count as a
    # finding for the audit: once `--rename-citekeys` has applied them the work
    # is done and the caller has nothing left to act on.
    findings = bool(result.get("errors")) or (not apply and bool(result.get("changed")))

    if as_json:
        cli_json.emit_result(
            result, stdout, command="fix reindex", items=result.get("changed") or [],
        )
        if result["status"] != "ok":
            return exit_codes.ENVIRONMENT
        return exit_codes.FINDINGS if findings else exit_codes.OK

    if result["status"] != "ok":
        print_lines(_error_lines("reindex failed", result.get("errors", [])), stderr)
        return exit_codes.ENVIRONMENT

    print_read_warnings(result, stderr)
    print_lines(_render_reindex_result(result, dry_run=not apply), stdout)
    backup = result.get("backup_path")
    if isinstance(backup, str):
        print(f"backup saved to {backup}", file=stderr)
    if not rename and result.get("changed"):
        print(
            "run with --rename-citekeys to apply "
            "(this rewrites citekeys; see 'pzi fix reindex --help')",
            file=stdout,
        )
    return exit_codes.FINDINGS if findings else exit_codes.OK
