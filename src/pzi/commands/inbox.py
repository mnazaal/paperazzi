"""CLI runner for `pzi inbox <file>` — drain an inbox file into the library."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from pzi.config import load_config_file
from pzi.inbox_service import DrainItem, DrainResult, drain_inbox, parse_inbox_line
from pzi.tag_service import parse_tag_csv

_SYMBOLS = {"added": "✓", "exists": "↻", "failed": "✗"}
_LABELS  = {"added": "added", "exists": "exists", "failed": "failed"}


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
        return 1
    except OSError as exc:
        print(f"cannot read inbox file: {exc}", file=stderr)
        return 1

    if not any(parse_inbox_line(line) for line in raw_text.splitlines()):
        print(f"inbox is empty: {inbox_path}", file=stdout)
        return 0

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
            return 1

        total = result["total"]
        if dry_run:
            print(
                f"dry run: previewing {total} item(s), nothing will be written",
                file=stderr,
            )

        for seq, item in enumerate(result["items"]):
            _stream_item(seq, total, item, stderr)

        _print_summary(result["counts"], dry_run, stdout)
        return 1 if result["counts"]["failed"] else 0

    # Real drain needs the translation server; an injected fake (tests) does not.
    if drain_inbox_fn is drain_inbox:
        cfg = load_config_file(config_path, home_dir=home_dir)
        config = cfg.get("config")
        if config is None:
            for line in cfg.get("errors") or ["config could not be loaded"]:
                print(f"error: {line}", file=stderr)
            return 1
        from pzi.ts_backend import backend_session

        with backend_session(
            config, config_path, home_dir,
            interactive=True, stdout=stdout, stderr=stderr,
        ) as backend:
            if not backend["ready"]:
                print(
                    "translation server is not running — cannot add papers.\n"
                    "  Run 'pzi server' (it starts the translation-server), then retry.",
                    file=stderr,
                )
                return 1
            return _work()

    return _work()


def _stream_item(seq: int, total: int, item: DrainItem, stderr: TextIO) -> None:
    bucket = item["status"]
    counter = f"[{seq + 1:>{len(str(total))}}/{total}]"
    label = f"{_LABELS[bucket]:<6}"
    value = item["value"]
    if bucket == "failed":
        reason = _first(item.get("errors")) or "capture failed"
        detail = f"{_short(value)} — {reason}"
    else:
        detail = str(item.get("citekey") or _short(value))
    print(f"{counter} {_SYMBOLS[bucket]} {label} {detail}", file=stderr)


def _print_summary(counts: dict[str, int], dry_run: bool, stdout: TextIO) -> None:
    verb = "would add" if dry_run else "added"
    print(
        f"done: {counts['added']} {verb}, {counts['exists']} already present, "
        f"{counts['failed']} failed",
        file=stdout,
    )


def _first(errors: Any) -> str | None:
    if isinstance(errors, list) and errors:
        return str(errors[0])
    return None


def _short(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[:limit - 1] + "…"
