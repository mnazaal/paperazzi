"""CLI runner for `pzi library check` — validate references against authoritative sources."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.check_service import CheckItem, CheckResult, check_bib
from pzi.cli_render import error_lines, render_check_items
from pzi.commands.common import (
    emit_usage_error,
    print_lines,
    print_read_warnings,
    write_atomic,
)
from pzi.errors import REASON_UNAVAILABLE, exit_code_for_error


def _describe_unwritable(path: str) -> str | None:
    """Why *path* cannot be written, or None when it can.

    Probed by opening and closing it, which is the only answer that counts —
    permissions, a missing parent directory and a path that is a directory all
    surface the same way the real write would.
    """
    existed = os.path.exists(path)
    try:
        with open(path, "a", encoding="utf-8"):
            pass
    except OSError as exc:
        return f"cannot be written: {exc.strerror or exc}"
    if not existed:
        # The probe created it. Leaving an empty file behind for a run that may
        # yet fail is a side effect the caller did not ask for; the real write
        # recreates it a moment later.
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover — it exists, we just made it
            pass
    return None


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
    """Run `pzi library check`: audit each entry, report verdicts, never write the bib.

    Exit codes: 5 when the service could not run or no source could be reached;
    2 for a conflicting invocation; 1 when any entry is problematic or could not
    be verified (so CI can gate on it); 0 otherwise.
    """
    report_path: str | None = getattr(args, "report", None)
    if report_path == "-" and getattr(args, "json", False):
        # The `--jsonl -` twin of this guard existed; this one did not, so
        # `--report - --json` wrote the full report and the envelope to the same
        # stream and produced neither.
        return emit_usage_error(
            args,
            "--report - writes the report to stdout and cannot be combined with --json",
            command_path=("library", "check"),
            stdout=stdout,
            stderr=stderr,
        )
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
            command_path=("library", "check"),
            stdout=stdout,
            stderr=stderr,
        )
    if report_path == "-" and jsonl_path == "-":
        # The third pair, and the one nothing guarded: each of `--report -` and
        # `--jsonl -` was refused alongside `--json` and neither was refused
        # alongside the other, so this wrote the whole report document and then
        # an NDJSON stream to the same stdout — two documents where every other
        # combination guarantees one.
        return emit_usage_error(
            args,
            "--report - and --jsonl - both write to stdout and cannot be combined",
            command_path=("library", "check"),
            stdout=stdout,
            stderr=stderr,
        )
    if getattr(args, "force", False) and not report_path and not jsonl_path:
        # `--force` here means only "overwrite the file at --report/--jsonl", and
        # with neither given it was accepted and did nothing. This CLI refuses
        # that pattern in six other places (`export -o`, `library clean`,
        # `library reindex`, `update`, `entries`, `add --failures-out`);
        # `library check` was the one miss, on the longest-running command,
        # where a silently ignored flag costs a whole network run to discover.
        return emit_usage_error(
            args,
            "--force applies to --report/--jsonl and has no effect without one",
            command_path=("library", "check"),
            stdout=stdout,
            stderr=stderr,
        )
    limit: int | None = getattr(args, "limit", None)
    # `< 1` is no longer checked here: `--limit` is `_positive_int` at the
    # parser (`cli_parser.py`), so a value that reaches this point is already
    # `>= 1` and this guard could never fire.
    # Both output paths are checked *before* the audit, not after it. `check`
    # is the long, network-bound command — a whole run against every entry — and
    # opening the destination at the end meant an unwritable path threw the
    # finished audit away. `add.py` fail-fasts `--metadata-json` for this exact
    # reason.
    for flag, path in (("--report", report_path), ("--jsonl", jsonl_path)):
        if not path or path == "-":
            continue
        if os.path.exists(path) and not getattr(args, "force", False):
            # `export -o` refuses to overwrite without `--force`; these two
            # silently replaced whatever was there. Refused up front for the
            # same reason the writability probe is up front: `check` is the long
            # network-bound command, and finding out at the end wastes the run.
            return emit_usage_error(
                args,
                f"{flag} {path} already exists (use --force to overwrite)",
                command_path=("library", "check"),
                stdout=stdout,
                stderr=stderr,
            )
        unwritable = _describe_unwritable(path)
        if unwritable is not None:
            # ENVIRONMENT, not USAGE. The flag is spelled correctly and the
            # value is the path the user meant; what failed is permission or a
            # missing parent directory, which `exit_codes` defines as 5 and the
            # README documents as 5. Reaching for `emit_usage_error` because it
            # was the helper already in hand made this the one I/O failure in
            # the CLI reported as a usage mistake.
            #
            # Through the shared `emit_failure` rather than a hand-built
            # envelope: this used to call `print_lines` unconditionally, ahead
            # of the `--json` check below it, so `--json` got the human error
            # on stderr *and* the envelope on stdout for the same failure —
            # the one output stream `--json` promises to be the only one that
            # matters. `emit_failure` prints to stderr only on the non-JSON
            # branch, matching every other refusal in this file.
            return cli_json.emit_failure(
                f"cannot write {flag} {path}",
                command="library check",
                # `unavailable`: the destination cannot be used right now.
                # Both mappers turn it into 5 / HTTP 503.
                reason=REASON_UNAVAILABLE,
                as_json=getattr(args, "json", False),
                stdout=stdout,
                stderr=stderr,
                errors=[f"{flag} {unwritable}"],
                stderr_lines=error_lines(f"cannot write {flag} {path}", [unwritable]),
            )

    strict: bool = getattr(args, "strict", False)
    # `--jsonl` is written as the audit goes, not after it returns. On the
    # library this command exists for the run is hours long, so buffering meant
    # an interrupt discarded every completed verdict — including the ones the
    # user was watching scroll past.
    with contextlib.ExitStack() as stack:
        jsonl_stream: TextIO | None = None
        if jsonl_path == "-":
            jsonl_stream = stdout
        elif jsonl_path:
            # Not `write_atomic`: all-or-nothing is the opposite of what is
            # wanted here. The up-front `--force` gate already established that
            # this path is either new or one the user asked to replace.
            jsonl_stream = stack.enter_context(
                open(jsonl_path, "w", encoding="utf-8")
            )
        streamed = 0

        def _on_item(item: CheckItem, index: int, total: int) -> None:
            nonlocal streamed
            if jsonl_stream is not None:
                print(json.dumps(item, default=str), file=jsonl_stream)
                jsonl_stream.flush()
                streamed += 1
            _print_progress(index, total, stderr)

        result = check_bib_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            strict=strict,
            limit=limit,
            on_item=_on_item,
        )
        if jsonl_stream is not None:
            # Anything the audit produced but never handed over — a `check_bib`
            # that does not stream still gets a complete file.
            for item in result["items"][streamed:]:
                print(json.dumps(item, default=str), file=jsonl_stream)

    # The audit never ran: no config, no library, nothing to report. An audit
    # that *did* run and reached no source is also `error` now (the service
    # decides that — see `check_bib`), but it has items, a report to write and a
    # table to print, so it must not take this branch.
    if result["status"] != "ok" and not result["items"]:
        if getattr(args, "json", False):
            cli_json.emit_result(result, stdout, command="library check")
        else:
            print_lines(error_lines("check failed", result["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    # Read notices first: "the library file is not there" changes what every
    # count below means, and `check` used to print "checked 0: 0 verified, 0
    # problematic" — a clean audit verdict for a library it never found.
    print_read_warnings(result, stderr)

    # Sources that could not be consulted go to stderr regardless of output
    # format: every verdict below was reached without them, and the reader has
    # to know that before trusting a "could not verify".
    if result["errors"]:
        print_lines(
            error_lines("metadata sources unavailable", result["errors"]), stderr
        )

    if report_path == "-":
        # `-` is stdout, the marker this CLI already uses in six other places
        # (`--jsonl -`, `pzi import -`). It used to create a file named `-`.
        json.dump(result, stdout, indent=2, default=str)
        print(file=stdout)
    elif report_path:
        # Atomic and gated on `--force`, the same as `pzi export -o`: a bare
        # `open(..., "w")` truncated an existing report before the audit had
        # produced a replacement, so an interrupted run destroyed the previous
        # one. The gate is applied up front, beside the writability probe.
        write_atomic(Path(report_path), json.dumps(result, indent=2, default=str))

    if limit is not None and result["total"] >= limit:
        # Say it, because every count below is of the audited slice and reads
        # exactly like a count of the library.
        print(
            f"note: --limit {limit} — entries after the first {limit} were not audited",
            file=stderr,
        )

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="library check")
    elif jsonl_path != "-" and report_path != "-":
        # Streaming to stdout already occupied it; adding the human table would
        # corrupt the stream. `--jsonl -` was guarded and `--report -` was not,
        # so `pzi library check --report - | jq .` — the only reason `--report -` exists
        # — got the report with a plain-text table appended to it.
        print_lines(render_check_items(result), stdout)

    # An audit that reached no source at all audited nothing. Exiting 0 there
    # reports a clean library, which is precisely the claim the run cannot make.
    # The verdict and its `reason` come from the service, so the report file, the
    # `--json` envelope, the exit code and `pzi.check()` cannot disagree.
    if result["status"] != "ok":
        return exit_code_for_error(result)

    # Anything other than "verified" is something to report, in both modes.
    # `FINDINGS` is documented — in `exit_codes` and in the README's table — as
    # covering "entries `check` could not verify", but it fired only for
    # `problematic` and only under `--strict`. A CI gate written from that table
    # therefore passed a library of fabricated references. `--strict` selects
    # *harder checks* (single-edit title typos, truncated author lists); making
    # it also decide whether a finding is reported is what opened the hole.
    counts = result["counts"]
    findings = counts["problematic"] + counts["could_not_verify"]
    # A block the parser dropped is an entry this audit did not cover, which is
    # something to report even when everything it *did* cover verified clean.
    # Exit 1, not 5: the run happened, the report was written, and 1 is the
    # documented code for "ran fine, has something to report".
    if result.get("warnings"):
        return exit_codes.FINDINGS
    return exit_codes.FINDINGS if findings else exit_codes.OK


#: A run shorter than this finishes while the user is still looking at it.
_PROGRESS_MIN_ENTRIES = 200
#: One line per this many entries. At the measured 0.6-11.2 s/entry that is a
#: line every 15 s to 5 min — often enough to show the run is alive, rare enough
#: not to fill a terminal over the hours a whole-library audit takes.
_PROGRESS_STEP = 25


def _print_progress(index: int, total: int, stderr: TextIO) -> None:
    """Say how far along a long audit is, on stderr.

    `check` is the only command that can run for hours, and it printed nothing
    at all until it finished — indistinguishable from a hang, which is what a
    user reaching for Ctrl-C decides it is.
    """
    if total < _PROGRESS_MIN_ENTRIES:
        return
    done = index + 1
    if done % _PROGRESS_STEP and done != total:
        return
    print(f"checked {done}/{total} entries", file=stderr)
