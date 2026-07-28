"""CLI runner for `pzi check` — validate references against authoritative sources."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.check_service import CheckResult, check_bib
from pzi.cli_render import _error_lines, _render_check_items
from pzi.commands.common import print_lines


def run_check_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    check_bib_fn: Callable[..., CheckResult] = check_bib,
) -> int:
    """Run `pzi check`: audit each entry, report verdicts, never write the bib.

    Exit codes: 1 on service error; in --strict mode, 1 when any entry is
    problematic (so CI can gate on it); 0 otherwise.
    """
    strict: bool = getattr(args, "strict", False)
    result = check_bib_fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        strict=strict,
    )

    if result["status"] != "ok":
        if getattr(args, "json", False):
            cli_json.emit(result, stdout)
        else:
            print_lines(_error_lines("check failed", result["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    # Sources that could not be consulted go to stderr regardless of output
    # format: every verdict below was reached without them, and the reader has
    # to know that before trusting a "could not verify".
    if result["errors"]:
        print_lines(
            _error_lines("metadata sources unavailable", result["errors"]), stderr
        )

    report_path: str | None = getattr(args, "report", None)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

    jsonl_path: str | None = getattr(args, "jsonl", None)
    if jsonl_path:
        lines = [json.dumps(item, default=str) for item in result["items"]]
        if jsonl_path == "-":
            # `-` means stdout, the same marker the capture inputs already use,
            # so the stream can be piped straight into jq.
            for line in lines:
                print(line, file=stdout)
        else:
            with open(jsonl_path, "w", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="check")
    elif jsonl_path != "-":
        # Streaming NDJSON to stdout already occupied it; adding the human
        # table would corrupt the stream.
        print_lines(_render_check_items(result), stdout)

    # An audit that reached no source at all audited nothing. Exiting 0 there
    # reports a clean library, which is precisely the claim the run cannot make.
    if result["items"] and result["errors"] and not any(
        item.get("sources_checked") for item in result["items"]
    ):
        return exit_codes.ENVIRONMENT

    problematic = result["counts"]["problematic"]
    return exit_codes.FINDINGS if (strict and problematic) else exit_codes.OK
