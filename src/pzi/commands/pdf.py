"""PDF CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.cli_render import _error_lines, _render_pdf_success
from pzi.commands.common import emit_usage_error, exit_code_for_error, print_lines
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
            return _pdf_exit_code(result)
        if result["status"] == "ok":
            print(_render_pdf_success("attached", result), file=stdout)
            return exit_codes.OK
        print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return _pdf_exit_code(result)

    if getattr(args, "failed_only", False):
        result = retry_failed_pdfs_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
        )
        if as_json:
            cli_json.emit_result(
                result, stdout, command="pdf retry", items=result.get("failures") or [],
            )
            if result["status"] == "error":
                return exit_codes.ENVIRONMENT
            return exit_codes.PARTIAL if result.get("failures") else exit_codes.OK
        if result["status"] == "error":
            print_lines(_error_lines(result["message"], result["errors"]), stderr)
            return exit_codes.ENVIRONMENT

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
        # Mirror the JSON branch above: the exit code must not depend on the
        # output format, and this path has just printed the failure list.
        return exit_codes.PARTIAL if result.get("failures") else exit_codes.OK

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
    )
    if as_json:
        cli_json.emit_result(result, stdout, command="pdf retry", items=[])
        return _pdf_exit_code(result)
    if result["status"] == "ok":
        print(_render_pdf_success("fetched", result), file=stdout)
        return exit_codes.OK
    print_lines(_error_lines(result["message"], result["errors"]), stderr)
    return _pdf_exit_code(result)


def _pdf_exit_code(result) -> int:
    """Map a PDF service result to an exit code."""
    if result["status"] == "ok":
        return exit_codes.OK
    return exit_code_for_error(result)
