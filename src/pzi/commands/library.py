"""CLI runner for `pzi library` — dispatches the library subcommands."""

from __future__ import annotations

from typing import TextIO

from pzi import exit_codes
from pzi.commands.bibs import run_list_command
from pzi.commands.check import run_check_command
from pzi.commands.clean import run_clean_command
from pzi.commands.dedupe import run_dedupe_command, run_merge_command
from pzi.commands.reindex import run_reindex_command

#: Read-only first, then the ones that write. `check` lives here rather than at
#: the top level because it inspects a library, which is what this group is —
#: it was the only pure-validation command outside it (item 432).
_SUBCOMMANDS = {
    "list": run_list_command,
    "check": run_check_command,
    "clean": run_clean_command,
    "dedupe": run_dedupe_command,
    "merge": run_merge_command,
    "reindex": run_reindex_command,
}


def run_library_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
) -> int:
    runner = _SUBCOMMANDS.get(args.library_command)
    if runner is None:  # pragma: no cover — `cli` prints group help for a bare group
        print(f"unknown library command: {args.library_command}", file=stderr)
        return exit_codes.USAGE
    return runner(
        args,
        home_dir=home_dir,
        config_path=config_path,
        stdout=stdout,
        stderr=stderr,
        bib_selector=bib_selector,
    )
