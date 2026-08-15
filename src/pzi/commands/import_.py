"""CLI runner for `pzi import`."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.cli_parser import load_text_arg
from pzi.cli_render import error_lines
from pzi.commands.common import batch_exit_code, print_lines
from pzi.errors import exit_code_for_error
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
            print_lines(error_lines("import failed", result.get("errors", [])), stderr)
        # Through the shared mapper, not a hardcoded ENVIRONMENT: the service
        # classifies an unreadable source as REASON_USAGE, and hardcoding 5 here
        # made the emitted envelope say `"reason": "usage"` while the process
        # exited 5. `pzi.http_status` reads that same field, so the CLI and the
        # HTTP API disagreed about one failure.
        return exit_code_for_error(result)

    if as_json:
        cli_json.emit_result(result, stdout, command="import")
        return _import_exit_code(result)

    prefix = "DRY RUN: " if getattr(args, "dry_run", False) else ""
    print(f"{prefix}imported {result['imported']}/{result['total_source']} entries", file=stdout)
    if result.get("updated"):
        print(f"{prefix}updated {result.get('updated', 0)} existing entries", file=stdout)
    if result["skipped_duplicates"]:
        print(f"{prefix}skipped {result['skipped_duplicates']} duplicates", file=stdout)
    if result["skipped_errors"]:
        print(f"{prefix}{result['skipped_errors']} errors", file=stdout)

    for r in result.get("results", []):
        # Only an error is a failure. Marking anything that was not an insert
        # with ✗ put a cross beside every entry an import successfully updated,
        # and beside every duplicate it correctly skipped.
        status_mark = "✗" if r["status"] == "error" else "✓"
        print(f"  {status_mark} {r['citekey']}: {r['status']}", file=stdout)

    if result.get("errors"):
        for err in result["errors"]:
            print(f"  ! {err}", file=stderr)

    # The writer's warnings — a near-duplicate insert is the one that matters,
    # since silently doubling the library is what `import` must never do.
    for warning in result.get("warnings") or []:
        print(f"  ! {warning}", file=stderr)

    return _import_exit_code(result)


def _import_exit_code(result) -> int:
    """The shared batch contract, applied to an import's counters.

    A duplicate the run correctly skipped counts as a success: the entry is in
    the library, which is what the caller asked for. Only `skipped_errors` are
    failures. This returned PARTIAL whenever any entry errored — including when
    *none* succeeded, which the README documents as 5.
    """
    succeeded = (
        result.get("imported", 0)
        + result.get("updated", 0)
        + result.get("skipped_duplicates", 0)
    )
    return batch_exit_code(succeeded=succeeded, failed=result["skipped_errors"])
