"""CLI runner for `pzi delete`."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.bib_service import delete_entry
from pzi.cli_render import error_lines, render_delete_success
from pzi.commands.common import emit_usage_error, exit_code_for_error, print_lines, resolve_target


def _render_errors(title: str, errors: Sequence[str], stderr: TextIO, code: int) -> int:
    print_lines(error_lines(title, errors), stderr)
    return code


def run_delete_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    as_json = getattr(args, "json", False)
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    if not args.force and not args.dry_run:
        # Never prompt into a pipe: reading the confirmation would eat a line of
        # the caller's data, and answering "no" for them turns a forgotten
        # --force into a silent no-op that reports success.
        if not sys.stdin.isatty():
            return emit_usage_error(
                args,
                "refusing to prompt for confirmation with stdin not a terminal; "
                "pass --force to delete or --dry-run to preview",
                command_path=("delete",),
                stdout=stdout,
                stderr=stderr,
            )
        print(
            f"Delete entry '{args.citekey}' from {target['path']}? [y/N] ",
            end="",
            file=stderr,
        )
        response = sys.stdin.readline().strip().lower()
        if response not in ("y", "yes"):
            # Declining is a result the caller has to see; emitting nothing left
            # `--json` unable to distinguish it from a successful delete.
            if as_json:
                cli_json.emit_result(
                    {"status": "ok", "citekey": args.citekey, "deleted": False,
                     "message": "cancelled"},
                    stdout, command="delete", items=[], bib_name=target["name"],
                )
            else:
                print("cancelled", file=stderr)
            return exit_codes.OK

    result = delete_entry(
        bib_path=target["path"],
        citekey=args.citekey,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        if as_json:
            cli_json.emit_result(
                result, stdout, command="delete", items=[], bib_name=target["name"]
            )
        else:
            print(render_delete_success(result), file=stdout)
        backup = result.get("backup_path")
        if isinstance(backup, str):
            print(f"backup saved to {backup}", file=stderr)
        return exit_codes.OK
    code = exit_code_for_error(result)
    if as_json:
        cli_json.emit_result(
                result, stdout, command="delete", items=[], bib_name=target["name"]
            )
        return code
    return _render_errors(result["message"], result["errors"], stderr, code)
