"""CLI runner for `pzi inbox <file>` — drain an inbox file into the library."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from pzi import exit_codes
from pzi.commands.common import (
    first_error,
    print_capture_stream_line,
    print_capture_summary,
    print_dry_run_banner,
)
from pzi.config import load_config_file
from pzi.inbox_service import DrainItem, DrainResult, drain_inbox, parse_inbox_line
from pzi.tag_service import parse_tag_csv


def run_inbox_command(
    args: argparse.Namespace,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    drain_inbox_fn: Callable[..., DrainResult] = drain_inbox,
) -> int:
    """Drain `args.file`: process entries, remove successes, keep failures."""
    inbox_path = Path(args.file)
    dry_run: bool = getattr(args, "dry_run", False)
    raw_tags: str | None = getattr(args, "tags", None)
    extra_tags = parse_tag_csv(raw_tags) if raw_tags else []
    delay: float = max(0.0, getattr(args, "delay", 1.0) or 0.0)

    # Fast-fail before touching the translation server: if the file is missing
    # or has nothing to process, there is no reason to spin up a backend.
    try:
        raw_text = inbox_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"inbox file not found: {inbox_path}", file=stderr)
        return exit_codes.ENVIRONMENT
    except OSError as exc:
        print(f"cannot read inbox file: {exc}", file=stderr)
        return exit_codes.ENVIRONMENT

    if not any(parse_inbox_line(line) for line in raw_text.splitlines()):
        print(f"inbox is empty: {inbox_path}", file=stdout)
        return exit_codes.OK

    def _work() -> int:
        result = drain_inbox_fn(
            config_path=config_path,
            home_dir=home_dir,
            inbox_path=str(inbox_path),
            dry_run=dry_run,
            extra_tags=extra_tags or None,
            delay=delay,
        )
        if result["status"] == "error":
            for line in result["errors"]:
                print(f"error: {line}", file=stderr)
            return exit_codes.ENVIRONMENT

        total = result["total"]
        if dry_run:
            print_dry_run_banner(total, stderr)

        for seq, item in enumerate(result["items"]):
            _stream_item(seq, total, item, stderr)

        print_capture_summary(result["counts"], dry_run=dry_run, stdout=stdout)
        # PARTIAL, not FINDINGS — see the matching note in `commands/add.py`.
        return (
            exit_codes.PARTIAL if result["counts"]["failed"] else exit_codes.OK
        )

    # Real drain needs the translation server; an injected fake (tests) does not.
    if drain_inbox_fn is drain_inbox:
        cfg = load_config_file(config_path, home_dir=home_dir)
        config = cfg.get("config")
        if config is None:
            for line in cfg.get("errors") or ["config could not be loaded"]:
                print(f"error: {line}", file=stderr)
            return exit_codes.ENVIRONMENT
        from pzi.ts_backend import backend_session

        with backend_session(
            config, home_dir,
            interactive=True, stdout=stdout, stderr=stderr,
        ) as backend:
            if not backend["ready"]:
                print(
                    "translation server is not running — cannot add papers.\n"
                    "  Run 'pzi server' (it starts the translation-server), then retry.",
                    file=stderr,
                )
                return exit_codes.ENVIRONMENT
            return _work()

    return _work()


def _stream_item(seq: int, total: int, item: DrainItem, stderr: TextIO) -> None:
    print_capture_stream_line(
        index=seq,
        total=total,
        value=item["value"],
        bucket=item["status"],
        citekey=item.get("citekey"),
        reason=first_error(item.get("errors")),
        warnings=item.get("warnings") or (),
        stderr=stderr,
    )







