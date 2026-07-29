"""CLI runner for `pzi import`."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.cli_parser import load_text_arg
from pzi.cli_render import _error_lines
from pzi.commands.common import print_lines
from pzi.import_service import import_from_bibtex


def run_import_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector,
) -> int:
    source = args.source
    # `-` reads BibTeX from stdin, closing the `pzi export | pzi import -` pipe;
    # the same marker `add --from-file` already accepts.
    source_text = load_text_arg(source) if source == "-" else None
    if source_text is None and not Path(source).exists():
        # ENVIRONMENT, not NOT_FOUND: `3` is reserved for a missing *entry*
        # (`exit_codes.py`, `commands/common.py`), and a source path that is not
        # there is the same "could not run" condition `import_service` already
        # reports as an error when its own check wins the race.
        message = f"source file not found: {source}"
        if getattr(args, "json", False):
            cli_json.emit_error(message, [message], stdout, command="import")
        else:
            print(f"error: {message}", file=stderr)
        return exit_codes.ENVIRONMENT

    result = import_from_bibtex(
        config_path=config_path,
        home_dir=home_dir,
        source_path="<stdin>" if source == "-" else source,
        source_text=source_text,
        bib_selector=bib_selector,
        dry_run=getattr(args, "dry_run", False),
        force_new=getattr(args, "force_new", False),
    )

    as_json = getattr(args, "json", False)
    if result["status"] == "error":
        if as_json:
            cli_json.emit_result(result, stdout, command="import")
        else:
            print_lines(_error_lines("import failed", result.get("errors", [])), stderr)
        return exit_codes.ENVIRONMENT

    if as_json:
        cli_json.emit_result(result, stdout, command="import")
        return exit_codes.OK if result["skipped_errors"] == 0 else exit_codes.PARTIAL

    prefix = "DRY RUN: " if getattr(args, "dry_run", False) else ""
    print(f"{prefix}imported {result['imported']}/{result['total_source']} entries", file=stdout)
    if result["skipped_duplicates"]:
        print(f"{prefix}skipped {result['skipped_duplicates']} duplicates", file=stdout)
    if result["skipped_errors"]:
        print(f"{prefix}{result['skipped_errors']} errors", file=stdout)

    for r in result.get("results", []):
        status_mark = "✓" if r["status"] in ("imported", "would_import") else "✗"
        print(f"  {status_mark} {r['citekey']}: {r['status']}", file=stdout)

    if result.get("errors"):
        for err in result["errors"]:
            print(f"  ! {err}", file=stderr)

    # Some entries imported and some failed is a partial result, distinct from
    # the whole command failing.
    return exit_codes.OK if result["skipped_errors"] == 0 else exit_codes.PARTIAL
