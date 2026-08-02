"""CLI runner for ``pzi export``."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TextIO

from pzi import exit_codes
from pzi.cli_render import _error_lines
from pzi.commands.common import print_lines, resolve_target
from pzi.export_service import export_bibtex, export_csv, export_json, export_ris


def _write_atomic(output_path: Path, content: str) -> None:
    """Write *content* to *output_path* all-or-nothing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=f".{output_path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def run_export_command(
    args, *, home_dir, config_path, stdout: TextIO, stderr: TextIO, bib_selector
) -> int:
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    exporters = {
        "bibtex": export_bibtex,
        "csv": export_csv,
        "json": export_json,
        "ris": export_ris,
    }
    result = exporters[args.format](bib_path=target["path"])

    if result["status"] != "ok":
        print_lines(_error_lines("export failed", result.get("errors", [])), stderr)
        return exit_codes.ENVIRONMENT

    content = result["content"]
    if args.output:
        output_path = Path(args.output)
        if output_path.exists() and not getattr(args, "force", False):
            print(
                f"error: output file already exists: {args.output} (use --force to overwrite)",
                file=stderr,
            )
            # USAGE: the invocation was refused, nothing ran. 1 would have said
            # "ran fine, here are findings".
            return exit_codes.USAGE
        # Write beside the destination and rename over it: `write_text`
        # truncates first, so an interrupted or failing export replaced a good
        # backup with a partial one — worst on `--force`, whose whole purpose is
        # overwriting a file the user still wants if the export fails.
        _write_atomic(output_path, content)
        print(f"exported {result['total_entries']} entries to {args.output}", file=stdout)
    else:
        print(content, file=stdout)
    return exit_codes.OK


