"""Shared CLI command helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.cli_parser import usage_error_lines
from pzi.config import BibConfig, load_config_file, resolve_library_target
from pzi.errors import PziError


def print_lines(lines: Sequence[str], out: TextIO) -> None:
    """Print rendered CLI lines to a stream."""
    for line in lines:
        print(line, file=out)


def resolve_target(
    *, config_path: str, home_dir: str, bib_selector: str | None,
) -> tuple[dict[str, Any], BibConfig]:
    """Load config and resolve a single library target.

    Raises :class:`PziError`, which the CLI boundary renders.  Both failures are
    environment failures: the config is what defines the set of libraries, so a
    ``--target`` matching none of them means the config does not describe the
    library asked for.  ``NOT_FOUND`` stays reserved for a missing *entry*,
    which is the distinction a script branches on.  Shared by the
    library-maintenance command runners so each one resolves, and fails,
    identically.
    """
    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        raise PziError(
            "failed to load config", code=exit_codes.ENVIRONMENT, details=cfg["errors"],
        )
    target = resolve_library_target(cfg["config"]["bibs"], bib_selector, home_dir=home_dir)
    if target is None:
        raise PziError("bib not found", code=exit_codes.ENVIRONMENT)
    return cfg["config"], target


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
    stderr: TextIO,
) -> None:
    """Print one bulk-capture progress line, plus any warnings it carried.

    The warnings matter: duplicate-capture detection attaches its "probable
    duplicate" notice here, and bulk capture — the likeliest place to add the
    same paper twice — printed nothing, because both renderers plumbed warnings
    into their results and then never showed them in text mode.
    """
    counter = f"[{index + 1:>{len(str(total))}}/{total}]"
    label = f"{_CAPTURE_LABELS[bucket]:<6}"
    if bucket == "failed":
        detail = f"{shorten(value)} — {reason or 'capture failed'}"
    else:
        detail = str(citekey or shorten(value))
    print(f"{counter} {_CAPTURE_SYMBOLS[bucket]} {label} {detail}", file=stderr)
    for warning in warnings:
        print(f"      warning: {warning}", file=stderr)


def exit_code_for_error(result: Mapping[str, object]) -> int:
    """Exit code for a service result that failed.

    Services report *why* they failed in a structured ``reason`` field rather
    than in prose, so a runner never has to match on message text — and a
    message reworded for humans cannot silently change a script's exit code.
    ``"not_found"`` is the only value today; anything else, including a missing
    ``reason``, means the command could not run.

    Callers must have already handled the success case: this always returns a
    failure code.
    """
    if result.get("reason") == "not_found":
        return exit_codes.NOT_FOUND
    return exit_codes.ENVIRONMENT


#: Sub-command attributes, in the order the CLI nests them. `pzi fix clean`
#: parses as `command="fix"`, `fix_command="clean"`, and the runners label their
#: envelopes with the joined form.
_SUBCOMMAND_ATTRS = ("fix_command", "tag_command", "pdf_command")


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
