"""CLI runner for `pzi add` (single and `--from-file` bulk capture)."""

from __future__ import annotations

import argparse
import random
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.add_planning import classify_capture_outcome
from pzi.add_service import describe_invalid_add_input
from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput
from pzi.cli_parser import (
    build_capture_input_from_add_args,
    build_capture_options_from_add_args,
    describe_invalid_metadata_json,
    load_text_arg,
    parse_batch_values,
)
from pzi.cli_render import error_lines, render_add_success
from pzi.commands.common import (
    batch_exit_code,
    command_label,
    emit_usage_error,
    first_error,
    print_capture_stream_line,
    print_capture_summary,
    print_dry_run_banner,
    print_lines,
    print_metadata_diagnostics,
)
from pzi.config import load_config_file
from pzi.errors import REASON_UNAVAILABLE, exit_code_for_error
from pzi.tag_service import parse_tag_csv

# Single-item-only flags (defined on the `add` parser) that have no meaning
# when capturing a whole batch via --from-file.
#: Flags that only mean something when iterating a list of inputs.
_SINGLE_INCOMPATIBLE = (
    ("delay", "--delay"),
    ("failures_out", "--failures-out"),
)

_BATCH_INCOMPATIBLE = (
    ("citekey", "--citekey"),
    ("metadata_json", "--metadata-json"),
    ("cookie_file", "--cookie-file"),
    ("page_html", "--page-html"),
)


def run_add_command(
    args: argparse.Namespace,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
    backend_session_fn=None,
) -> int:
    from_file = getattr(args, "from_file", None)
    invalid = _validate_add_args(args, from_file=from_file)
    if invalid is not None:
        return emit_usage_error(args, invalid, command_path=("add",),
                                stdout=stdout, stderr=stderr)

    # Reject unrecognized input (e.g. `pzi add l`) before starting the
    # translation-server or touching the bib — fail fast with no side effects.
    if not from_file and args.value:
        bad_value = describe_invalid_add_input(args.value)
        if bad_value is not None:
            return emit_usage_error(args, bad_value, command_path=("add",),
                                    stdout=stdout, stderr=stderr)

    # Same rule for the metadata file. It used to be parsed at capture time,
    # which runs *inside* the backend session below — so a JSON typo cost a full
    # translation-server startup before failing. `-` (stdin) is excluded: it can
    # only be read once, and reading it here would consume it.
    metadata_json = getattr(args, "metadata_json", None)
    if metadata_json and metadata_json != "-":
        bad_metadata = describe_invalid_metadata_json(metadata_json)
        if bad_metadata is not None:
            return emit_usage_error(args, bad_metadata, command_path=("add",),
                                    stdout=stdout, stderr=stderr)

    # And the batch input list, for the same reason: it was read inside `_work`,
    # which runs after the backend session below, so `--from-file typo.txt` paid
    # for a translation-server clone before reporting a path that is not there.
    # `-` (stdin) is excluded exactly as above.
    batch_text: str | None = None
    if from_file and from_file != "-":
        try:
            batch_text = load_text_arg(from_file)
        except OSError as exc:
            return emit_usage_error(args, f"cannot read --from-file {from_file}: {exc}",
                                    command_path=("add",), stdout=stdout, stderr=stderr)

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]

    def _work() -> int:
        if from_file:
            return _run_batch(
                args, cfg, from_file=from_file, text=batch_text, home_dir=home_dir,
                config_path=config_path,
                stdout=stdout, stderr=stderr, bib_selector=bib_selector,
                fetch_web=fetch_web, fetch_search=fetch_search,
            )
        return _capture_and_render(
            args, cfg, home_dir=home_dir, config_path=config_path,
            stdout=stdout, stderr=stderr, bib_selector=bib_selector,
            fetch_web=fetch_web, fetch_search=fetch_search,
        )

    if config is not None and fetch_web is None and fetch_search is None:
        if backend_session_fn is None:
            from pzi.ts_backend import backend_session

            backend_session_fn = backend_session

        # Bootstrap progress goes to stderr even here: under `--json` stdout
        # carries exactly one document, and "cloning translation-server …"
        # printed ahead of it made that document unparseable.
        with backend_session_fn(
            config, home_dir,
            interactive=True, stdout=stderr, stderr=stderr,
        ) as backend:
            if backend.get("auto_start_skipped"):
                # `ready` is True here because PZI_SKIP_AUTO_START says "I manage
                # the server", not because anything checked. Without this note a
                # server that is simply not running produced "translation server
                # returned no results" — the wording for a paper that does not
                # exist — and the accurate diagnostic below never ran.
                print(
                    "note: PZI_SKIP_AUTO_START is set, so the translation server "
                    "was not started or checked; a capture failure here may mean "
                    "it is not running",
                    file=stderr,
                )
            if not backend["ready"]:
                message = (
                    "translation server is not running — cannot add paper. "
                    "Run 'pzi server' (it starts the translation-server), then retry."
                )
                # The commonest failure there is, and `--json` promised a
                # parseable document on every outcome. This one printed prose to
                # stderr and nothing at all to stdout.
                if getattr(args, "json", False):
                    # `unavailable`, spelled out: `emit_error` defaults to
                    # `usage`, so this envelope told a consumer to retype while
                    # the process exited 5 and told it to retry. `inbox` names
                    # the reason on the same refusal; this call site did not.
                    cli_json.emit_error(
                        message, [message], stdout, command=command_label(args),
                        reason=REASON_UNAVAILABLE,
                    )
                else:
                    print(message, file=stderr)
                return exit_codes.ENVIRONMENT
            return _work()

    return _work()


def _validate_add_args(args: argparse.Namespace, *, from_file: str | None) -> str | None:
    """Return an error message for an invalid value/--from-file combination."""
    value = getattr(args, "value", None)
    if not from_file:
        if not value:
            return "provide a DOI, URL, or PDF path, or use --from-file PATH"
        # The mirror of the check below, which was missing: batch-only flags
        # were accepted and ignored on a single add. `--delay` and
        # `--failures-out` describe iterating over a list of inputs; there is no
        # list here.
        for attr, flag in _SINGLE_INCOMPATIBLE:
            if getattr(args, attr, None) is not None:
                return f"{flag} applies to --from-file mode only"
        return None
    if value:
        return "provide either a value or --from-file, not both"
    if getattr(args, "pdf_candidate", None):
        return "--pdf-candidate cannot be combined with --from-file"
    for attr, flag in _BATCH_INCOMPATIBLE:
        if getattr(args, attr, None):
            return f"{flag} applies to a single paper and cannot be combined with --from-file"
    return None


def _capture_and_render(
    args: argparse.Namespace,
    cfg,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    service_kwargs = {}
    if fetch_web is not None:
        service_kwargs["fetch_web"] = fetch_web
    if fetch_search is not None:
        service_kwargs["fetch_search"] = fetch_search
    result = capture_to_bib(
        build_capture_input_from_add_args(args, bib_selector=bib_selector),
        build_capture_options_from_add_args(args, config=cfg.get("config")),
        config_path=config_path,
        home_dir=home_dir,
        service_kwargs=service_kwargs,
    )

    if result["status"] == "error":
        if getattr(args, "json", False):
            # Same envelope as the success path below — a consumer should not
            # have to switch shapes depending on the outcome.
            cli_json.emit_result(result, stdout, command="add")
        else:
            print_lines(error_lines(result["message"], result["errors"]), stderr)
        # A failed capture has as much to say as a successful one — often more,
        # since the way out of the refusal is a warning. These were rendered on
        # the success path only, so a service that explained how to proceed
        # explained it to nobody.
        for warning in result.get("warnings") or []:
            print(f"warning: {warning}", file=stderr)
        # The shared `reason` mapper, not a hardcoded ENVIRONMENT: a capture
        # `add_service` rejects as `REASON_USAGE` used to exit 5, "could not
        # run", while the envelope it just emitted said `"reason": "usage"`.
        return exit_code_for_error(result)

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="add")
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=stderr)
        return exit_codes.OK

    print(render_add_success(result), file=stdout)
    if args.dry_run and result.get("diff"):
        print(result["diff"], file=stdout, end="" if result["diff"].endswith("\n") else "\n")
    if args.verbose:
        print_metadata_diagnostics(result, stdout)
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=stderr)
    suggestion = result.get("pdf_suggestion")
    if isinstance(suggestion, str) and suggestion:
        print(f"suggestion: {suggestion}", file=stderr)
    return exit_codes.OK


# ---------------------------------------------------------------------------
# Bulk capture (`pzi add --from-file`)
# ---------------------------------------------------------------------------


def _run_batch(
    args: argparse.Namespace,
    cfg,
    *,
    from_file: str,
    text: str | None,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    if text is None:
        # `--from-file -`. Stdin is read here rather than in the fail-fast block
        # because it can only be read once.
        try:
            text = load_text_arg(from_file)
        except OSError as exc:
            return emit_usage_error(args, f"cannot read --from-file {from_file}: {exc}",
                                    command_path=("add",), stdout=stdout, stderr=stderr)
    values = parse_batch_values(text)
    if not values:
        # The file is there and holds nothing this command can act on, so the
        # user has to edit it — `usage`, and exit 2 to match. Both refusals here
        # used to emit `"reason": "usage"` and then return 5.
        return emit_usage_error(args, f"no DOIs or URLs found in {from_file}",
                                command_path=("add",), stdout=stdout, stderr=stderr)

    options = build_capture_options_from_add_args(args, config=cfg.get("config"))
    tags = parse_tag_csv(args.tags) if getattr(args, "tags", None) else []
    service_kwargs: dict[str, Any] = {}
    if fetch_web is not None:
        service_kwargs["fetch_web"] = fetch_web
    if fetch_search is not None:
        service_kwargs["fetch_search"] = fetch_search

    raw_delay = getattr(args, "delay", None)
    delay = max(0.0, 1.0 if raw_delay is None else raw_delay)
    total = len(values)
    counts = {"added": 0, "exists": 0, "failed": 0}
    failures: list[str] = []
    items: list[dict[str, Any]] = []

    if args.dry_run:
        print_dry_run_banner(total, stderr)

    interrupted = False
    for index, value in enumerate(values):
        if index > 0 and delay > 0:
            try:
                time.sleep(delay + random.uniform(0, delay * 0.25))
            except KeyboardInterrupt:
                # The per-item guard below is `except Exception`, which does not
                # catch this — and `_write_failures` runs after the loop. So the
                # commonest way a long polite-delay batch ends was the one case
                # that wrote no failures file and printed no summary: everything
                # already captured was in the bib, and the user had no record of
                # where to resume. Stop the loop, then report as usual.
                interrupted = True
                break
        try:
            result = capture_to_bib(
                CaptureInput(
                    value=value,
                    record_overrides={"tags": tags} if tags else {},
                    bib_selector=bib_selector,
                    pdf_candidates=(),
                    page_artifact=None,
                    auth_hints=AuthHints(cookies=None),
                ),
                options,
                config_path=config_path,
                home_dir=home_dir,
                service_kwargs=service_kwargs,
            )
        except KeyboardInterrupt:
            # Mid-capture. Same reasoning as the delay above: report what has
            # already happened rather than unwinding through it.
            interrupted = True
            result = {
                "status": "error",
                "citekey": None,
                "message": "interrupted",
                "errors": ["interrupted"],
                "warnings": [],
            }
        except Exception as exc:  # one bad item must not lose the batch
            # `inbox drain` already guards its loop this way. Without it, an
            # exception on item K discarded the K-1 results already captured,
            # printed no summary, and wrote no failures file — so a long run had
            # nothing to resume from and no record of what had succeeded.
            result = {
                "status": "error",
                "action": None,
                "citekey": None,
                "message": str(exc),
                "errors": [str(exc)],
                "warnings": [],
            }
        bucket = _classify(result)
        counts[bucket] += 1
        if bucket == "failed":
            failures.append(value)
        _stream_line(
            index, total, value, result, bucket, stderr,
            dry_run=getattr(args, "dry_run", False),
        )
        if getattr(args, "verbose", False) and not getattr(args, "json", False):
            # `--verbose` was parsed here and never read, so the one mode where
            # per-item provider choices are hardest to follow was the mode that
            # would not explain them. Suppressed under `--json`, where stdout
            # carries exactly one document.
            print_metadata_diagnostics(result, stdout)
        items.append({"value": value, "status": result["status"], "result": result})
        if interrupted:
            break

    # `--dry-run` announces "nothing will be written" above, and the failures
    # file is written content like any other.
    failures_path = None
    if failures and not args.dry_run:
        try:
            failures_path = _write_failures(failures, args, from_file)
        except OSError as exc:
            # An unwritable `--failures-out` used to turn a completed batch into
            # exit 5 with no summary at all: the entries were already in the
            # library while the calling script saw total failure. The list is a
            # convenience for resuming; losing it does not undo the captures.
            print(
                f"warning: could not write the failures file "
                f"({exc.strerror or exc}); the failed inputs are listed above",
                file=stderr,
            )

    if getattr(args, "json", False):
        # Was hand-rolled with `json.dumps`, so it carried `items` by luck and
        # none of the other four envelope keys. `total`, `counts` and
        # `failures_file` still ride along as command-specific fields.
        # A batch where something worked is not an "error": the exit code
        # already distinguishes a partial run (PARTIAL) from a clean one, and
        # calling a half-successful capture an error made `.status` contradict
        # it — a script reading the envelope would treat "1 added, 1 failed" as
        # a total failure. `import` reports the same situation as "ok" with the
        # per-item reasons in `errors`; this now matches it. Only a batch that
        # captured nothing at all is an error.
        captured = counts["added"] + counts["exists"]
        cli_json.emit_result(
            {
                "status": "error" if counts["failed"] and not captured else "ok",
                "total": total,
                "counts": counts,
                "failures_file": str(failures_path) if failures_path else None,
                # The documented failure channel: previously empty even when
                # items had failed, leaving the reasons only inside `items[]`.
                "errors": [
                    # `first_error` takes the *errors list*, not the whole
                    # result — passing the result Mapping returned None every
                    # time, so this documented failure channel was the literal
                    # string "failed" for every item. Text mode has always read
                    # `message` first and fallen back to the list; do the same.
                    f"{item['value']}: {_failure_reason(item['result'])}"
                    for item in items
                    if _classify(item["result"]) == "failed"
                ],
                "items": items,
            },
            stdout,
            command="add --from-file",
        )
    else:
        print_capture_summary(
            counts, dry_run=args.dry_run, stdout=stdout, failures_path=failures_path
        )
    if interrupted:
        # 130, the documented interrupted code, and only *after* the summary,
        # envelope and failures file are out. The user gets what the run
        # achieved and a list to resume from; the shell gets the right signal.
        return exit_codes.INTERRUPTED
    # The shared contract — see `batch_exit_code`. This command had the correct
    # rule and the other two did not, which is the argument for there being one
    # of it rather than three.
    return batch_exit_code(
        succeeded=counts["added"] + counts["exists"], failed=counts["failed"]
    )


_classify = classify_capture_outcome


def _stream_line(
    index: int, total: int, value: str, result: Mapping[str, Any], bucket: str,
    stderr: TextIO, *, dry_run: bool = False,
) -> None:
    raw_warnings = result.get("warnings")
    print_capture_stream_line(
        index=index,
        total=total,
        value=value,
        bucket=bucket,
        citekey=result.get("citekey"),
        reason=str(result.get("message") or "") or first_error(result.get("errors")),
        warnings=[w for w in raw_warnings if isinstance(w, str)]
        if isinstance(raw_warnings, list)
        else (),
        dry_run=dry_run,
        stderr=stderr,
    )


def _failure_reason(result: Mapping[str, Any]) -> str:
    """Why one `--from-file` item failed, in the same words text mode uses."""
    message = result.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return first_error(result.get("errors")) or "failed"


def _write_failures(failures: list[str], args: argparse.Namespace, from_file: str) -> Path:
    path = _failures_path(getattr(args, "failures_out", None), from_file)
    path.write_text("\n".join(failures) + "\n", encoding="utf-8")
    return path


def _failures_path(override: str | None, from_file: str) -> Path:
    if override:
        return Path(override)
    if from_file == "-":
        return Path("pzi-failed.txt")
    src = Path(from_file)
    return src.with_name(f"{src.stem}.failed.txt")


