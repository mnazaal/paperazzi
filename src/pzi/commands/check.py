"""CLI runner for `pzi check` — validate references against authoritative sources."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.check_service import CheckResult, check_bib
from pzi.cli_render import _error_lines, _render_check_items
from pzi.commands.common import emit_usage_error, print_lines


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

    Exit codes: 5 when the service could not run or no source could be reached;
    2 for a conflicting invocation; in --strict mode, 1 when any entry is
    problematic (so CI can gate on it); 0 otherwise.
    """
    jsonl_path: str | None = getattr(args, "jsonl", None)
    if jsonl_path == "-" and getattr(args, "json", False):
        # Both write to stdout: the NDJSON stream plus the pretty envelope is
        # neither valid NDJSON nor the single document `--json` promises. The
        # human table is already guarded against the stream; this is the same
        # collision one flag over. argparse cannot express "conflicts only when
        # --jsonl is `-`", so the check lives here.
        return emit_usage_error(
            args,
            "--jsonl - writes NDJSON to stdout and cannot be combined with --json",
            command_path=("check",),
            stdout=stdout,
            stderr=stderr,
        )
    strict: bool = getattr(args, "strict", False)
    result = check_bib_fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        strict=strict,
    )

    if result["status"] != "ok":
        if getattr(args, "json", False):
            cli_json.emit_result(result, stdout, command="check")
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

    # An audit that reached no source audited nothing, and the run exits 5 for
    # it below — so the envelope must not report a clean `ok`.
    audited_nothing = bool(
        result["items"]
        and result["errors"]
        and not any(item.get("sources_checked") for item in result["items"])
    )
    if audited_nothing:
        result = {**result, "status": "error"}

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="check")
    elif jsonl_path != "-":
        # Streaming NDJSON to stdout already occupied it; adding the human
        # table would corrupt the stream.
        print_lines(_render_check_items(result), stdout)

    # An audit that reached no source at all audited nothing. Exiting 0 there
    # reports a clean library, which is precisely the claim the run cannot make.
    if audited_nothing:
        return exit_codes.ENVIRONMENT

    problematic = result["counts"]["problematic"]
    return exit_codes.FINDINGS if (strict and problematic) else exit_codes.OK
