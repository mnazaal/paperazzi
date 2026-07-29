"""CLI runner for `pzi add` (single and `--from-file` bulk capture)."""

from __future__ import annotations

import argparse
import random
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.add_service import describe_invalid_add_input
from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput
from pzi.cli_parser import (
    build_capture_input_from_add_args,
    build_capture_options_from_add_args,
    describe_invalid_metadata_json,
    load_text_arg,
    parse_batch_values,
    usage_error_lines,
)
from pzi.cli_render import _error_lines, _render_add_success
from pzi.commands.common import (
    first_error,
    print_capture_stream_line,
    print_capture_summary,
    print_dry_run_banner,
    print_lines,
    print_metadata_diagnostics,
)
from pzi.config import load_config_file
from pzi.tag_service import parse_tag_csv

# Single-item-only flags (defined on the `add` parser) that have no meaning
# when capturing a whole batch via --from-file.
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
) -> int:
    from_file = getattr(args, "from_file", None)
    invalid = _validate_add_args(args, from_file=from_file)
    if invalid is not None:
        print_lines(usage_error_lines(("add",), invalid), stderr)
        return exit_codes.USAGE

    # Reject unrecognized input (e.g. `pzi add l`) before starting the
    # translation-server or touching the bib — fail fast with no side effects.
    if not from_file and args.value:
        bad_value = describe_invalid_add_input(args.value)
        if bad_value is not None:
            print_lines(usage_error_lines(("add",), bad_value), stderr)
            return exit_codes.USAGE

    # Same rule for the metadata file. It used to be parsed at capture time,
    # which runs *inside* the backend session below — so a JSON typo cost a full
    # translation-server startup before failing. `-` (stdin) is excluded: it can
    # only be read once, and reading it here would consume it.
    metadata_json = getattr(args, "metadata_json", None)
    if metadata_json and metadata_json != "-":
        bad_metadata = describe_invalid_metadata_json(metadata_json)
        if bad_metadata is not None:
            print_lines(usage_error_lines(("add",), bad_metadata), stderr)
            return exit_codes.USAGE

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]

    def _work() -> int:
        if from_file:
            return _run_batch(
                args, cfg, from_file=from_file, home_dir=home_dir, config_path=config_path,
                stdout=stdout, stderr=stderr, bib_selector=bib_selector,
                fetch_web=fetch_web, fetch_search=fetch_search,
            )
        return _capture_and_render(
            args, cfg, home_dir=home_dir, config_path=config_path,
            stdout=stdout, stderr=stderr, bib_selector=bib_selector,
            fetch_web=fetch_web, fetch_search=fetch_search,
        )

    if config is not None and fetch_web is None and fetch_search is None:
        from pzi.ts_backend import backend_session

        with backend_session(
            config, config_path, home_dir,
            interactive=True, stdout=stdout, stderr=stderr,
        ) as backend:
            if not backend["ready"]:
                print(
                    "translation server is not running — cannot add paper.\n"
                    "  Run 'pzi server' (it starts the translation-server), then retry.",
                    file=stderr,
                )
                return exit_codes.ENVIRONMENT
            return _work()

    return _work()


def _validate_add_args(args: argparse.Namespace, *, from_file: str | None) -> str | None:
    """Return an error message for an invalid value/--from-file combination."""
    value = getattr(args, "value", None)
    if not from_file:
        if not value:
            return "provide a DOI, URL, or PDF path, or use --from-file PATH"
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
            print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="add")
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=stderr)
        return exit_codes.OK

    print(_render_add_success(result), file=stdout)
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
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    try:
        text = load_text_arg(from_file)
    except OSError as exc:
        message = f"cannot read --from-file {from_file}: {exc}"
        if getattr(args, "json", False):
            cli_json.emit_error(
                message, [message], stdout, command="add --from-file",
            )
        else:
            print(f"error: {message}", file=stderr)
        return exit_codes.ENVIRONMENT
    values = parse_batch_values(text)
    if not values:
        message = f"no DOIs or URLs found in {from_file}"
        if getattr(args, "json", False):
            cli_json.emit_error(
                message, [message], stdout, command="add --from-file",
            )
        else:
            print(f"error: {message}", file=stderr)
        return exit_codes.ENVIRONMENT

    options = build_capture_options_from_add_args(args, config=cfg.get("config"))
    tags = parse_tag_csv(args.tags) if getattr(args, "tags", None) else []
    service_kwargs: dict[str, Any] = {}
    if fetch_web is not None:
        service_kwargs["fetch_web"] = fetch_web
    if fetch_search is not None:
        service_kwargs["fetch_search"] = fetch_search

    delay = max(0.0, getattr(args, "delay", 1.0) or 0.0)
    total = len(values)
    counts = {"added": 0, "exists": 0, "failed": 0}
    failures: list[str] = []
    items: list[dict[str, Any]] = []

    if args.dry_run:
        print_dry_run_banner(total, stderr)

    for index, value in enumerate(values):
        if index > 0 and delay > 0:
            time.sleep(delay + random.uniform(0, delay * 0.25))
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
        except Exception as exc:  # noqa: BLE001 — one bad item must not lose the batch
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
        _stream_line(index, total, value, result, bucket, stderr)
        items.append({"value": value, "status": result["status"], "result": result})

    # `--dry-run` announces "nothing will be written" above, and the failures
    # file is written content like any other.
    failures_path = (
        _write_failures(failures, args, from_file)
        if failures and not args.dry_run
        else None
    )

    if getattr(args, "json", False):
        # Was hand-rolled with `json.dumps`, so it carried `items` by luck and
        # none of the other four envelope keys. `total`, `counts` and
        # `failures_file` still ride along as command-specific fields.
        cli_json.emit_result(
            {
                "status": "error" if counts["failed"] else "ok",
                "total": total,
                "counts": counts,
                "failures_file": str(failures_path) if failures_path else None,
                "items": items,
            },
            stdout,
            command="add --from-file",
        )
    else:
        print_capture_summary(
            counts, dry_run=args.dry_run, stdout=stdout, failures_path=failures_path
        )
    # A batch where some items failed is PARTIAL, not FINDINGS: `1` is reserved
    # for "ran fine and has something to report", so a script branching on it
    # must not see a half-failed capture as a clean run with findings.
    return exit_codes.PARTIAL if counts["failed"] else exit_codes.OK


def _classify(result: Mapping[str, Any]) -> str:
    if result.get("status") == "error":
        return "failed"
    return "exists" if result.get("action") == "update" else "added"




def _stream_line(
    index: int, total: int, value: str, result: Mapping[str, Any], bucket: str, stderr: TextIO
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
        stderr=stderr,
    )


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


