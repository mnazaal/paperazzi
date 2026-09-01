"""PDF CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.cli_render import error_lines, render_pdf_success
from pzi.commands.common import (
    batch_exit_code,
    emit_usage_error,
    print_lines,
    print_read_warnings,
)
from pzi.errors import exit_code_for_error
from pzi.pdf_service import attach_pdf, retry_failed_pdfs, retry_pdf

Result = Mapping[str, Any]
PdfService = Callable[..., Result]


def run_pdf_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    attach_pdf_fn: PdfService = attach_pdf,
    retry_pdf_fn: PdfService = retry_pdf,
    retry_failed_pdfs_fn: PdfService = retry_failed_pdfs,
) -> int:
    """Run `pzi pdf ...` using injected services for thin-I/O testing."""
    as_json = getattr(args, "json", False)
    if args.pdf_command == "attach":
        result = attach_pdf_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
            source=args.source,
        )
        if as_json:
            cli_json.emit_result(result, stdout, command="pdf attach", items=[])
            print_read_warnings(result, stderr)
            return _pdf_exit_code(result)
        if result["status"] == "ok":
            print(render_pdf_success("attached", result), file=stdout)
            print_read_warnings(result, stderr)
            return exit_codes.OK
        print_lines(error_lines(result["message"], result["errors"]), stderr)
        print_read_warnings(result, stderr)
        return _pdf_exit_code(result)

    if getattr(args, "failed_only", False):
        if args.citekey:
            # Previously the citekey was accepted and discarded. That was
            # documented, but doing something other than what was typed is
            # worse than refusing: `pzi pdf retry smith2024 --failed-only`
            # looks like it retries one entry and silently retried the whole
            # library instead.
            return emit_usage_error(
                args,
                "--failed-only retries every PDF-less entry; "
                "drop the citekey, or drop --failed-only to retry just that entry",
                command_path=("pdf", "retry"),
                stdout=stdout,
                stderr=stderr,
            )
        result = retry_failed_pdfs_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            deep=getattr(args, "discover", False),
        )
        if as_json:
            cli_json.emit_result(
                result, stdout, command="pdf retry", items=result.get("failures") or [],
            )
            print_read_warnings(result, stderr)
            if result["status"] == "error":
                return _pdf_exit_code(result)
            return _failed_only_exit_code(result)
        if result["status"] == "error":
            print_lines(error_lines(result["message"], result["errors"]), stderr)
            print_read_warnings(result, stderr)
            return _pdf_exit_code(result)

        lines = [
            f"bib: {result['bib_name']}",
            f"succeeded: {result['succeeded']}/{result['total']}",
            f"skipped (already have PDF): {result['skipped_already_has_pdf']}",
            f"skipped (no PDF URL): {result['skipped_no_url']}",
        ]
        if result["failures"]:
            lines.append(f"failed: {len(result['failures'])}")
            for failure in result["failures"]:
                lines.append(f"  {failure['citekey']}: {failure['error']}")
        print_lines(lines, stdout)
        print_read_warnings(result, stderr)
        # Mirror the JSON branch above: the exit code must not depend on the
        # output format, and this path has just printed the failure list.
        return _failed_only_exit_code(result)

    if not args.citekey:
        return emit_usage_error(
            args,
            "citekey required (or use --failed-only for batch retry)",
            command_path=("pdf", "retry"),
            stdout=stdout,
            stderr=stderr,
        )

    result = retry_pdf_fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
        deep=getattr(args, "discover", False),
    )
    if as_json:
        cli_json.emit_result(result, stdout, command="pdf retry", items=[])
        print_read_warnings(result, stderr)
        return _pdf_exit_code(result)
    if result["status"] == "ok":
        print(render_pdf_success("fetched", result), file=stdout)
        print_read_warnings(result, stderr)
        return exit_codes.OK
    print_lines(error_lines(result["message"], result["errors"]), stderr)
    print_read_warnings(result, stderr)
    return _pdf_exit_code(result)


def _failed_only_exit_code(result) -> int:
    """Exit code for `pdf retry --failed-only`, from the shared batch rule.

    Both output branches open-coded `PARTIAL if failures else OK`, which is the
    rule minus its all-failed case: a retry where every entry failed exited 4,
    claiming a partial success that did not happen, where every other batch
    command exits 5. Routed through `batch_exit_code` so there is one answer
    rather than a copy that can drift again.
    """
    return batch_exit_code(
        succeeded=result["succeeded"], failed=len(result.get("failures") or []),
    )


def _pdf_exit_code(result) -> int:
    """Map a PDF service result to an exit code."""
    if result["status"] == "ok":
        return exit_codes.OK
    return exit_code_for_error(result)
