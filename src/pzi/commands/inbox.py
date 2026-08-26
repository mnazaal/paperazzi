"""CLI runner for `pzi inbox <file>` — drain an inbox file into the library."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.commands.common import (
    batch_exit_code,
    first_error,
    print_capture_stream_line,
    print_capture_summary,
    print_dry_run_banner,
)
from pzi.config import load_config_file
from pzi.errors import REASON_CONFIG, REASON_UNAVAILABLE, exit_code_for_error
from pzi.inbox_service import DrainItem, DrainResult, drain_inbox, parse_inbox_line
from pzi.tag_service import parse_tag_csv


def _fail_early(
    message: str,
    *,
    inbox_path: Path,
    dry_run: bool,
    as_json: bool,
    reason: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Report a failure that happens before the drain, honouring ``--json``.

    Every early exit here printed prose to stderr and left stdout empty, even
    under ``--json`` — so `pzi inbox /nonexistent --json` produced zero bytes on
    stdout at exit 5. `inbox` is the command most likely to run unattended from
    cron, which is exactly the caller that cannot read prose.
    """
    if as_json:
        cli_json.emit_result(
            {
                "status": "error",
                "inbox_file": str(inbox_path),
                "dry_run": dry_run,
                "total": 0,
                "counts": {"added": 0, "exists": 0, "failed": 0},
                "items": [],
                "errors": [message],
                "reason": reason,
            },
            stdout,
            command="inbox",
        )
    else:
        print(message, file=stderr)
    return exit_code_for_error({"reason": reason})


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

    as_json: bool = getattr(args, "json", False)

    # Fast-fail before touching the translation server: if the file is missing
    # or has nothing to process, there is no reason to spin up a backend.
    try:
        raw_text = inbox_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # `unavailable`, not `not_found`: exit 3 is documented as an unknown
        # *citekey*, and a caller branching on it would read "no such entry" for
        # a mistyped path. The README puts a missing input under 5.
        return _fail_early(
            f"inbox file not found: {inbox_path}",
            inbox_path=inbox_path, dry_run=dry_run, as_json=as_json,
            reason=REASON_UNAVAILABLE, stdout=stdout, stderr=stderr,
        )
    except OSError as exc:
        return _fail_early(
            f"cannot read inbox file: {exc}",
            inbox_path=inbox_path, dry_run=dry_run, as_json=as_json,
            reason=REASON_UNAVAILABLE, stdout=stdout, stderr=stderr,
        )

    if not any(parse_inbox_line(line) for line in raw_text.splitlines()):
        if as_json:
            cli_json.emit_result(
                {
                    "status": "ok",
                    "inbox_file": str(inbox_path),
                    "dry_run": dry_run,
                    "total": 0,
                    "counts": {"added": 0, "exists": 0, "failed": 0},
                    "items": [],
                    "errors": [],
                },
                stdout,
                command="inbox",
            )
        else:
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
            if as_json:
                cli_json.emit_result(result, stdout, command="inbox")
            else:
                for line in result["errors"]:
                    print(f"error: {line}", file=stderr)
            return exit_codes.ENVIRONMENT

        total = result["total"]
        if dry_run:
            print_dry_run_banner(total, stderr)

        # Per-item progress goes to stderr in both modes: under `--json` stdout
        # carries exactly one document, and the stream is progress, not result.
        for seq, item in enumerate(result["items"]):
            _stream_item(seq, total, item, stderr, dry_run=dry_run)

        # Top-level `errors` on an otherwise-`ok` result (e.g. the inbox was
        # rewritten mid-drain, so the drained lines were left in place rather
        # than risking clobbering the edit) used to reach the user only under
        # `--json`, where `emit_result` below carries the whole dict — the
        # text path printed nothing and exited 0, so the same entries got
        # silently re-added on the next drain with no explanation of why.
        if not as_json:
            for line in result.get("errors") or ():
                print(f"error: {line}", file=stderr)

        if as_json:
            # The per-item reasons are inside `items[]`; lift the failures into
            # the documented `errors[]` channel too, as `add --from-file` does,
            # so a consumer branching on the channel sees them.
            failures = [
                f"{item['value']}: {first_error(item.get('errors')) or 'capture failed'}"
                for item in result["items"]
                if item["status"] == "failed"
            ]
            cli_json.emit_result(
                {**result, "errors": [*result.get("errors", []), *failures]},
                stdout,
                command="inbox",
            )
        else:
            print_capture_summary(result["counts"], dry_run=dry_run, stdout=stdout)
        # The shared contract — see `batch_exit_code`. This returned PARTIAL
        # whenever anything failed, so a drain in which *every* line failed
        # reported "some items succeeded" and exited 4, while identical input
        # through `add --from-file` exited 5.
        counts = result["counts"]
        return batch_exit_code(
            succeeded=counts["added"] + counts["exists"], failed=counts["failed"]
        )

    # Real drain needs the translation server; an injected fake (tests) does not.
    if drain_inbox_fn is drain_inbox:
        cfg = load_config_file(config_path, home_dir=home_dir)
        config = cfg.get("config")
        if config is None:
            return _fail_early(
                "; ".join(cfg.get("errors") or ["config could not be loaded"]),
                inbox_path=inbox_path, dry_run=dry_run, as_json=as_json,
                reason=REASON_CONFIG, stdout=stdout, stderr=stderr,
            )
        from pzi.ts_backend import backend_session

        with backend_session(
            config, home_dir,
            # Bootstrap progress ("cloning translation-server …") goes to
            # stderr, as `add` already does: it used to interleave with this
            # command's own stdout data line, which is the one thing a caller
            # parses.
            interactive=True, stdout=stderr, stderr=stderr,
        ) as backend:
            if not backend["ready"]:
                return _fail_early(
                    "translation server is not running — cannot add papers.\n"
                    "  Run 'pzi server' (it starts the translation-server), then retry.",
                    inbox_path=inbox_path, dry_run=dry_run, as_json=as_json,
                    reason=REASON_UNAVAILABLE, stdout=stdout, stderr=stderr,
                )
            return _work()

    return _work()


def _stream_item(
    seq: int, total: int, item: DrainItem, stderr: TextIO, *, dry_run: bool = False
) -> None:
    print_capture_stream_line(
        dry_run=dry_run,
        index=seq,
        total=total,
        value=item["value"],
        bucket=item["status"],
        citekey=item.get("citekey"),
        reason=first_error(item.get("errors")),
        warnings=item.get("warnings") or (),
        stderr=stderr,
    )







