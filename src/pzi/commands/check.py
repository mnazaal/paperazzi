"""CLI runner for `pzi check` — validate references against authoritative sources."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TextIO

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
        print_lines(_error_lines("check failed", result["errors"]), stderr)
        return 1

    report_path: str | None = getattr(args, "report", None)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

    jsonl_path: str | None = getattr(args, "jsonl", None)
    if jsonl_path:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for item in result["items"]:
                f.write(json.dumps(item, default=str) + "\n")

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
    else:
        print_lines(_render_check_items(result), stdout)

    problematic = result["counts"]["problematic"]
    return 1 if (strict and problematic) else 0
