"""CLI runner for `pzi delete`."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

from pzi.bib_service import delete_entry
from pzi.cli_render import _error_lines, _render_delete_success
from pzi.commands.common import print_lines, resolve_target_or_error


def _render_errors(title: str, errors: Sequence[str], stderr: TextIO) -> int:
    print_lines(_error_lines(title, errors), stderr)
    return 1


def run_delete_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector, stderr=stderr,
    )
    if resolved is None:
        return 1
    _config, target = resolved

    if not args.force and not args.dry_run:
        print(
            f"Delete entry '{args.citekey}' from {target['path']}? [y/N] ",
            end="",
            file=stderr,
        )
        response = sys.stdin.readline().strip().lower()
        if response not in ("y", "yes"):
            print("cancelled", file=stdout)
            return 0

    result = delete_entry(
        bib_path=target["path"],
        citekey=args.citekey,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        print(_render_delete_success(result), file=stdout)
        backup = result.get("backup_path")
        if isinstance(backup, str):
            print(f"backup saved to {backup}", file=stderr)
        return 0
    return _render_errors(result["message"], result["errors"], stderr)
