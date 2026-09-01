"""CLI runner for `pzi library reindex`."""

from __future__ import annotations

import sys
from pathlib import Path

from pzi import cli_json, exit_codes
from pzi.cli_render import error_lines, reindex_error_lines, render_reindex_result
from pzi.commands.common import (
    emit_usage_error,
    has_read_warnings,
    print_lines,
    print_read_warnings,
    resolve_target,
)
from pzi.reindex_service import reindex_library, rename_files_to_policy


def run_reindex_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    as_json = getattr(args, "json", False)
    rename = getattr(args, "rename_citekeys", False)
    rename_files = getattr(args, "rename_files", False)
    if rename and rename_files:
        return emit_usage_error(
            args,
            "--rename-citekeys and --rename-files are separate passes; run one, "
            "then the other",
            command_path=("library", "reindex"),
            stdout=stdout,
            stderr=stderr,
        )
    if rename_files:
        # A distinct pass, deliberately: bare `reindex` keeps reporting citekeys
        # only. Folding filename findings into the default output would have
        # added ~10.7k lines on a real library, for names that differ from the
        # template purely in case and are not defects.
        result = rename_files_to_policy(
            bib_path=target["path"],
            papers_dir=target["papers_dir"],
            pdf_filename_format=config.get("pdf_filename_format"),
            dry_run=getattr(args, "dry_run", False),
            include_all=getattr(args, "all", False),
            file_path_style=config.get("pdf_file_path_style", "absolute"),
        )
        if as_json:
            cli_json.emit_result(
                result, stdout, command="library reindex",
                items=result.get("changed") or [], bib_name=target["name"],
            )
        else:
            verb = "would rename" if getattr(args, "dry_run", False) else "renamed"
            print(f"{verb} {len(result['changed'])} PDF(s)", file=stdout)
            for change in result["changed"]:
                print(f"  {change['citekey']}: {Path(change['new_pdf']).name}",
                      file=stdout)
            if result.get("skipped_cosmetic") and not getattr(args, "all", False):
                print(
                    f"  ({result['skipped_cosmetic']} name(s) differ only "
                    "cosmetically and were left alone; --all includes them)",
                    file=stderr,
                )
            for error in result["errors"]:
                print(f"  {error}", file=stderr)
            backup = result.get("backup_path")
            if backup:
                print(f"backup saved to {backup}", file=stderr)
        return exit_codes.FINDINGS if result["changed"] or result["errors"] else exit_codes.OK

    for flag, given in (
        ("--force", getattr(args, "force", False)),
        ("--dry-run", getattr(args, "dry_run", False)),
    ):
        if given and not rename:
            # Without `--rename-citekeys` the run is already a read-only audit,
            # so both flags are accepted and do nothing — and `--dry-run`
            # especially reads as "I have made this safe" when it changed
            # nothing about what the command was going to do anyway.
            return emit_usage_error(
                args,
                f"{flag} applies to --rename-citekeys; without it the run is "
                f"already a read-only audit",
                command_path=("library", "reindex"),
                stdout=stdout,
                stderr=stderr,
            )
    # Default is a read-only audit: keep citekeys stable unless explicitly asked.
    apply = rename and not args.dry_run
    # No configured format means the *built-in* scheme (author+year+title) will
    # be used. For a library imported from Zotero or Mendeley that rewrites
    # every key to something the user never chose — worth saying before they
    # answer the prompt, not after.
    no_format = not config.get("citekey_format")
    if apply:
        print(
            "warning: rewriting citekeys will break any \\cite{} references that use "
            "the old keys (in LaTeX documents, notes, etc.).",
            file=stderr,
        )
        if no_format:
            print(
                "warning: no citekey_format is configured, so pzi's built-in "
                "scheme (author + year + title) will be used. Set citekey_format "
                "in config.toml first if you want a different one.",
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
                "pass --force to rewrite citekeys or --dry-run to preview"
                + (
                    " (note: no citekey_format is configured, so the built-in "
                    "author+year+title scheme would be used)"
                    if no_format
                    else ""
                ),
                command_path=("library", "reindex"),
                stdout=stdout,
                stderr=stderr,
            )
        scheme = "the built-in scheme" if no_format else "citekey_format"
        print(
            f"Rewrite every citekey in {target['path']} using {scheme}? [y/N] ",
            end="",
            file=stderr,
        )
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            if as_json:
                cli_json.emit_result(
                    {"status": "ok", "bib_path": target["path"], "message": "cancelled"},
                    stdout, command="library reindex", items=[], bib_name=target["name"],
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

    # Computed once above the format branch, as `library dedupe` does. Keying only
    # off `errors` meant a read-only audit that found renames to make exited 0
    # while simultaneously printing "run with --rename-citekeys to apply" —
    # nothing to report, according to the exit code. Renames only count as a
    # finding for the audit: once `--rename-citekeys` has applied them the work
    # is done and the caller has nothing left to act on.
    # A partial read counts too: the rename plan below was computed from the
    # blocks that parsed, so "no citekey changes needed" at exit 0 described a
    # library this run had only partly seen.
    findings = (
        bool(result.get("errors"))
        or has_read_warnings(result)
        or (not apply and bool(result.get("changed")))
    )

    if as_json:
        # `applied` distinguishes the two runs that otherwise look identical: an
        # audit reported a populated `changed[]` next to `backup_path: null`,
        # which reads as "these renames happened, and nothing was backed up".
        cli_json.emit_result(
            {**result, "applied": apply}, stdout, command="library reindex",
            items=result.get("changed") or [], bib_name=target["name"],
        )
        if result["status"] != "ok":
            return exit_codes.ENVIRONMENT
        return exit_codes.FINDINGS if findings else exit_codes.OK

    if result["status"] != "ok":
        print_lines(error_lines("reindex failed", result.get("errors", [])), stderr)
        return exit_codes.ENVIRONMENT

    print_read_warnings(result, stderr)
    print_lines(render_reindex_result(result, dry_run=not apply), stdout)
    print_lines(reindex_error_lines(result), stderr)
    backup = result.get("backup_path")
    if isinstance(backup, str):
        print(f"backup saved to {backup}", file=stderr)
    if not rename and result.get("changed"):
        print(
            "run with --rename-citekeys to apply "
            "(this rewrites citekeys; see 'pzi library reindex --help')",
            file=stdout,
        )
    return exit_codes.FINDINGS if findings else exit_codes.OK
