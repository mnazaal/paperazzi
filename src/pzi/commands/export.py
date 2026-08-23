"""CLI runner for ``pzi export``."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from pzi import exit_codes
from pzi.cli_render import error_lines
from pzi.commands.common import (
    emit_usage_error,
    print_lines,
    resolve_target,
    write_atomic,
)
from pzi.export_service import EXPORTERS


def run_export_command(
    args, *, home_dir, config_path, stdout: TextIO, stderr: TextIO, bib_selector
) -> int:
    if getattr(args, "force", False) and args.output in (None, "-"):
        # `--force` only means "overwrite the file at -o". Accepting it without
        # a destination is the project's own rule broken (it is enforced in five
        # other places): a flag that is silently ignored reads as applied, and
        # the user finds out only if it mattered.
        return emit_usage_error(
            args,
            "--force applies to -o PATH and has no effect without it",
            command_path=("export",),
            stdout=stdout,
            stderr=stderr,
        )
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    result = EXPORTERS[args.format](target["path"])

    if result["status"] != "ok":
        # The partial-read verdict, `export`'s way. The other four read commands
        # report a lenient parse as a finding (exit 1) via `has_read_warnings`;
        # the exporters have no `warnings` channel at all, because
        # `_export_status` classifies a dropped block as an *error* — this
        # output replaces a backup, so an export that silently omits entries
        # must not be exit 0 and must not be exit 1 either.
        print_lines(error_lines("export failed", result.get("errors", [])), stderr)
        return exit_codes.ENVIRONMENT

    content = result["content"]
    if args.output == "-":
        # `-` is stdout, as everywhere else in this CLI; it used to create a
        # file named `-` in the working directory.
        print(content, file=stdout)
        return exit_codes.OK
    if args.output:
        output_path = Path(args.output)
        if output_path.is_dir():
            # `.exists()` is true for a directory, so `pzi export -o /` said
            # "output file already exists ... use --force" — and with --force it
            # proceeded, then died as a raw OSError naming the *temp* file,
            # which the user never asked for and cannot act on.
            print(f"error: output path is a directory: {args.output}", file=stderr)
            return exit_codes.USAGE
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
        write_atomic(output_path, content)
        print(f"exported {result['total_entries']} entries to {args.output}", file=stdout)
    else:
        print(content, file=stdout)
    return exit_codes.OK


