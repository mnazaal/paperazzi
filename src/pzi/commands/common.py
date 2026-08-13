"""Shared CLI command helpers."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from pzi import cli_json, errors, exit_codes
from pzi.cli_parser import usage_error_lines
from pzi.config import AppConfig, BibConfig, BibResolutionFailure, load_bib_target
from pzi.errors import PziError


def print_lines(lines: Sequence[str], out: TextIO) -> None:
    """Print rendered CLI lines to a stream."""
    for line in lines:
        print(line, file=out)


def resolve_target(
    *, config_path: str, home_dir: str, bib_selector: str | None,
) -> tuple[AppConfig, BibConfig]:
    """Load config and resolve a single library target.

    Raises :class:`PziError`, which the CLI boundary renders.  Both failures are
    environment failures: the config is what defines the set of libraries, so a
    ``--target`` matching none of them means the config does not describe the
    library asked for.  ``NOT_FOUND`` stays reserved for a missing *entry*,
    which is the distinction a script branches on.  Shared by the
    library-maintenance command runners so each one resolves, and fails,
    identically.
    """
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        # Delegated rather than reimplemented: this used to duplicate
        # load_bib_target's control flow with a worse message ("bib not found"
        # against "no matching library target found or selection is
        # ambiguous"), and dropped the per-line config errors on the
        # unresolved-target path.
        raise PziError(
            resolved.errors[0] if resolved.errors else "failed to resolve bib",
            code=exit_codes.ENVIRONMENT,
            details=list(resolved.errors),
        )
    config, target = resolved
    return config, target


def target_list(target: Sequence[str] | None) -> list[str | None]:
    """Normalize optional repeated --target values for command loops."""
    return list(target) if target else [None]


def print_result_item_diffs(result: Mapping[str, object], stdout: TextIO) -> None:
    """Print per-item dry-run diffs when present."""
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        diff = item.get("diff")
        if not isinstance(diff, str) or not diff:
            continue
        print(diff, file=stdout, end="" if diff.endswith("\n") else "\n")


def metadata_diagnostic_lines(result: Mapping[str, object]) -> list[str]:
    """Collect result-level and item-level metadata diagnostics."""
    direct = result.get("metadata_diagnostics")
    if isinstance(direct, list):
        return [line for line in direct if isinstance(line, str)]
    lines: list[str] = []
    items = result.get("items")
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, Mapping):
            continue
        diagnostics = item.get("metadata_diagnostics")
        if not isinstance(diagnostics, list):
            continue
        lines.extend(line for line in diagnostics if isinstance(line, str))
    return lines


def print_metadata_diagnostics(result: Mapping[str, object], stdout: TextIO) -> None:
    """Print verbose metadata diagnostics block."""
    lines = metadata_diagnostic_lines(result)
    if not lines:
        return
    print("metadata diagnostics:", file=stdout)
    for line in lines:
        print(f"  {line}", file=stdout)


def metadata_warning_lines(result: Mapping[str, object]) -> list[str]:
    """Collect result-level and item-level metadata confidence warnings."""
    lines: list[str] = []
    direct = result.get("metadata_warnings")
    if isinstance(direct, list):
        lines.extend(line for line in direct if isinstance(line, str))
    items = result.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            warnings = item.get("metadata_warnings")
            if isinstance(warnings, list):
                lines.extend(line for line in warnings if isinstance(line, str))
    return lines


def print_metadata_warnings(result: Mapping[str, object], stderr: TextIO) -> None:
    """Print metadata confidence warnings.

    Advisory ("verify this candidate") messages that services attach to items;
    shown always (not gated on --verbose) so low-confidence writes are visible.
    """
    for line in metadata_warning_lines(result):
        print(f"warning: {line}", file=stderr)


# Bulk-capture stream rendering, shared by `add --from-file` and `inbox drain`.
# Both walked a list of captured items and printed one line each; keeping two
# copies meant a fix to one (here: printing per-item warnings at all) silently
# skipped the other.
_CAPTURE_SYMBOLS = {"added": "✓", "exists": "↻", "failed": "✗"}
_CAPTURE_LABELS = {"added": "added", "exists": "exists", "failed": "failed"}


def first_error(errors: Any) -> str | None:
    if isinstance(errors, list) and errors:
        return str(errors[0])
    return None


def shorten(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def print_capture_stream_line(
    *,
    index: int,
    total: int,
    value: str,
    bucket: str,
    citekey: object,
    reason: str | None,
    warnings: Sequence[str] = (),
    dry_run: bool = False,
    stderr: TextIO,
) -> None:
    """Print one bulk-capture progress line, plus any warnings it carried.

    The warnings matter: duplicate-capture detection attaches its "probable
    duplicate" notice here, and bulk capture — the likeliest place to add the
    same paper twice — printed nothing, because both renderers plumbed warnings
    into their results and then never showed them in text mode.

    *dry_run* changes the verb. A preview streamed "✓ added" for every item
    while its own banner and closing summary — both correct — said "would". The
    per-item lines are what scrolls past and what the user reads.
    """
    counter = f"[{index + 1:>{len(str(total))}}/{total}]"
    verb = _CAPTURE_LABELS[bucket]
    if dry_run and bucket != "failed":
        verb = f"would {verb}"
    label = f"{verb:<12}" if dry_run else f"{verb:<6}"
    if bucket == "failed":
        detail = f"{shorten(value)} — {reason or 'capture failed'}"
    else:
        detail = str(citekey or shorten(value))
    print(f"{counter} {_CAPTURE_SYMBOLS[bucket]} {label} {detail}", file=stderr)
    for warning in warnings:
        print(f"      warning: {warning}", file=stderr)


def _write_atomic(output_path: Path, content: str) -> None:
    """Write *content* to *output_path* all-or-nothing.

    Shared because two runners write user-named files. `export -o` was atomic
    and `check --report/--jsonl` were bare `open(..., "w")`, which truncates
    first — so an interrupted `check` (the long, network-bound command) left the
    previous report destroyed and the new one incomplete.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=f".{output_path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


#: Exit code per structured failure reason. Anything absent from this table —
#: including a missing ``reason`` — means "the command could not run", which is
#: the safe default: it is never ``1``, so a script can still tell a failure to
#: run from a successful run that found something.
_EXIT_CODE_BY_REASON: dict[str, int] = {
    errors.REASON_NOT_FOUND: exit_codes.NOT_FOUND,
    errors.REASON_USAGE: exit_codes.USAGE,
    errors.REASON_CONFIG: exit_codes.ENVIRONMENT,
    errors.REASON_UNAVAILABLE: exit_codes.ENVIRONMENT,
    errors.REASON_CONFLICT: exit_codes.ENVIRONMENT,
}


def exit_code_for_error(result: Mapping[str, object]) -> int:
    """Exit code for a service result that failed.

    Services report *why* they failed in a structured ``reason`` field rather
    than in prose, so a runner never has to match on message text — and a
    message reworded for humans cannot silently change a script's exit code.
    The vocabulary is :mod:`pzi.errors`; ``pzi.http_status`` maps the same
    values to HTTP statuses, so a service that classifies its failure once is
    correct on both surfaces.

    Callers must have already handled the success case: this always returns a
    failure code.
    """
    reason = result.get("reason")
    if isinstance(reason, str):
        return _EXIT_CODE_BY_REASON.get(reason, exit_codes.ENVIRONMENT)
    return exit_codes.ENVIRONMENT


#: Sub-command attributes, in the order the CLI nests them. `pzi fix clean`
#: parses as `command="fix"`, `fix_command="clean"`, and the runners label their
#: envelopes with the joined form.
_SUBCOMMAND_ATTRS = ("fix_command", "tag_command", "pdf_command")


def batch_exit_code(*, succeeded: int, failed: int) -> int:
    """Exit code for a batch of items, from its outcome counts alone.

    One function so every batch command answers the same way. It was duplicated
    per command instead, and the copies diverged: `add --from-file` returned
    ENVIRONMENT when nothing succeeded while `inbox` and `import` returned
    PARTIAL regardless, so identical all-invalid input exited 5 from one command
    and 4 from another — against a README that documents the all-failed rule
    without qualification.

    - Nothing failed: OK. An empty batch is a run with nothing to report, not a
      failure.
    - Something failed but something succeeded: PARTIAL (4), which is what 4
      means.
    - Everything failed: ENVIRONMENT (5). PARTIAL would claim a partial success
      that did not happen, and 1 is reserved for "ran fine, has something to
      report".
    """
    if not failed:
        return exit_codes.OK
    if succeeded:
        return exit_codes.PARTIAL
    return exit_codes.ENVIRONMENT


def command_label(args: object) -> str:
    """The envelope ``command`` label for an invocation.

    The runners pass string literals (``"fix clean"``, ``"entries --stats"``).
    The CLI boundary has only ``args``, so it reconstructs the same label here —
    otherwise a command's failure document would name itself differently from
    its success document, and `.command` would stop being a reliable key.
    """
    label = str(getattr(args, "command", "") or "")
    for attr in _SUBCOMMAND_ATTRS:
        sub = getattr(args, attr, None)
        if isinstance(sub, str) and sub:
            return f"{label} {sub}"
    if label == "entries" and getattr(args, "stats", False):
        return "entries --stats"
    if label == "update" and getattr(args, "promote", False):
        return "update --promote"
    if label == "add" and getattr(args, "from_file", None):
        return "add --from-file"
    return label


def emit_usage_error(
    args: object,
    message: str,
    *,
    command_path: tuple[str, ...],
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Report a usage error, as the JSON envelope when ``--json`` was passed.

    A rejected invocation is still a result the caller has to classify, and the
    contract says they never have to scrape stderr to do it. Without this, every
    conditional usage check (the ones argparse cannot express) emitted prose
    only, so `--json` produced no document at all.
    """
    if getattr(args, "json", False):
        cli_json.emit_error(message, [message], stdout, command=command_label(args))
    else:
        print_lines(usage_error_lines(command_path, message), stderr)
    return exit_codes.USAGE


def print_capture_summary(
    counts: Mapping[str, int],
    *,
    dry_run: bool,
    stdout: TextIO,
    failures_path: Path | None = None,
) -> None:
    """Closing line for a bulk capture, shared by `add --from-file` and `inbox drain`.

    Same reason as `print_capture_stream_line` above: two verbatim copies meant
    a wording or counting fix to one silently skipped the other.
    """
    verb = "would add" if dry_run else "added"
    print(
        f"done: {counts['added']} {verb}, {counts['exists']} already present, "
        f"{counts['failed']} failed",
        file=stdout,
    )
    if failures_path is not None:
        print(f"wrote {counts['failed']} failed item(s) to {failures_path}", file=stdout)
        print(f"  retry with: pzi add --from-file {failures_path}", file=stdout)


def print_dry_run_banner(total: int, stderr: TextIO) -> None:
    """Announce a dry run before a bulk capture streams its items."""
    print(f"dry run: previewing {total} item(s), nothing will be written", file=stderr)


def print_read_warnings(result: Mapping[str, Any], stderr: TextIO) -> None:
    """Report blocks the parser dropped, on an otherwise successful read.

    These are not `errors`: the command worked and is showing what it could
    read. But the counts it prints are of a *lenient* parse — a duplicate
    citekey keeps only the first occurrence — so without this the user sees
    fewer entries than the file holds and is told nothing. Goes to stderr, like
    the other summary output, so piping stdout stays clean.
    """
    for warning in result.get("warnings") or ():
        print(f"warning: {warning}", file=stderr)
