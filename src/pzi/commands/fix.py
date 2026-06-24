"""CLI runner for `pzi fix` — dispatches maintenance subcommands."""

from __future__ import annotations

from typing import TextIO

from pzi.commands.clean import run_clean_command
from pzi.commands.dedupe import run_dedupe_command, run_merge_command
from pzi.commands.reindex import run_reindex_command

_SUBCOMMANDS = {
    "clean": run_clean_command,
    "dedupe": run_dedupe_command,
    "merge": run_merge_command,
    "reindex": run_reindex_command,
}


def run_fix_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
) -> int:
    runner = _SUBCOMMANDS.get(args.fix_command)
    if runner is None:  # pragma: no cover — argparse marks fix_command required
        print(f"unknown fix command: {args.fix_command}", file=stderr)
        return 2
    return runner(
        args,
        home_dir=home_dir,
        config_path=config_path,
        stdout=stdout,
        stderr=stderr,
        bib_selector=bib_selector,
    )
