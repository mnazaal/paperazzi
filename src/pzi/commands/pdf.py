"""PDF CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi.cli_render import _error_lines, _render_pdf_success
from pzi.commands.common import print_lines
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
    if args.pdf_command == "attach":
        result = attach_pdf_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
            source=args.source,
        )
        if result["status"] == "ok":
            print(_render_pdf_success("attached", result), file=stdout)
            return 0
        print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return 1

    if getattr(args, "failed_only", False):
        result = retry_failed_pdfs_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
        )
        if result["status"] == "error":
            print_lines(_error_lines(result["message"], result["errors"]), stderr)
            return 1

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
        return 0

    if not args.citekey:
        print("error: citekey required (or use --failed-only for batch retry)", file=stderr)
        return 2

    result = retry_pdf_fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
    )
    if result["status"] == "ok":
        print(_render_pdf_success("fetched", result), file=stdout)
        return 0
    print_lines(_error_lines(result["message"], result["errors"]), stderr)
    return 1
