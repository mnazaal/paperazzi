"""CLI runner for `pzi import`."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

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
    if not getattr(args, "source", None):
        print("error: source .bib file required", file=stderr)
        return 2

    source = args.source
    if not Path(source).exists():
        print(f"error: source file not found: {source}", file=stderr)
        return 1

    result = import_from_bibtex(
        config_path=config_path,
        home_dir=home_dir,
        source_path=source,
        bib_selector=bib_selector,
        dry_run=getattr(args, "dry_run", False),
        force_new=getattr(args, "force_new", False),
    )

    if result["status"] == "error":
        print_lines(_error_lines("import failed", result.get("errors", [])), stderr)
        return 1

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

    return 0 if result["skipped_errors"] == 0 else 1
