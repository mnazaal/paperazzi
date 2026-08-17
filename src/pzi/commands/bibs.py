"""CLI runner for `pzi library list`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.bib_service import list_bibs
from pzi.cli_render import error_lines
from pzi.commands.common import print_lines
from pzi.errors import exit_code_for_error

ListBibs = Callable[..., Mapping[str, Any]]


def run_list_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None = None,
    list_bibs_fn: ListBibs = list_bibs,
) -> int:
    """Print the configured libraries.

    *bib_selector* is accepted and ignored: this is the command that tells you
    what the selectors are, so selecting one would be circular. It takes the
    parameter because `commands/library.py` dispatches every subcommand with the
    same keyword shape.

    The same set `GET /bibs` and `pzi.list_bibs()` return. `pzi doctor` also
    prints it, mixed into health output; this is the plain answer to "what are
    my libraries called".
    """
    result = list_bibs_fn(config_path=config_path, home_dir=home_dir)

    if getattr(args, "json", False):
        cli_json.emit_result(
            result, stdout, command="library list", items=result.get("bibs")
        )
        return exit_codes.OK if result["status"] == "ok" else exit_code_for_error(result)

    if result["status"] != "ok":
        print_lines(error_lines("could not read the config", result["errors"]), stderr)
        return exit_code_for_error(result)

    bibs = result["bibs"]
    if not bibs:
        # Not an error: a config can legitimately declare no libraries, and the
        # loader already refuses an empty `bibs` list — so this is the "you have
        # not set one up yet" case, which `pzi init` answers.
        print("no libraries configured", file=stderr)
        return exit_codes.OK

    width = max(len(str(bib["name"])) for bib in bibs)
    for bib in bibs:
        marker = "  (default)" if bib.get("default") else ""
        print(f"{str(bib['name']).ljust(width)}  {bib['path']}{marker}", file=stdout)
    return exit_codes.OK
