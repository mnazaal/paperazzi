"""CLI runner for ``pzi export``."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from pzi.cli_render import _error_lines
from pzi.commands.common import print_lines, resolve_target_or_error
from pzi.export_service import export_bibtex, export_csv, export_json, export_ris


def run_export_command(
    args, *, home_dir, config_path, stdout: TextIO, stderr: TextIO, bib_selector
) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector, stderr=stderr,
    )
    if resolved is None:
        return 1
    _config, target = resolved

    exporters = {
        "bibtex": export_bibtex,
        "csv": export_csv,
        "json": export_json,
        "ris": export_ris,
    }
    result = exporters[args.format](bib_path=target["path"])

    if result["status"] != "ok":
        print_lines(_error_lines("export failed", result.get("errors", [])), stderr)
        return 1

    content = result["content"]
    if args.output:
        output_path = Path(args.output)
        if output_path.exists() and not getattr(args, "force", False):
            print(
                f"error: output file already exists: {args.output} (use --force to overwrite)",
                file=stderr,
            )
            return 1
        output_path.write_text(content, encoding="utf-8")
        print(f"exported {result['total_entries']} entries to {args.output}", file=stdout)
    else:
        print(content, file=stdout)
    return 0


