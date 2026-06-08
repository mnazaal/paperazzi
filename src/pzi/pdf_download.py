#!/usr/bin/env python3
"""PDF download and local-source storage helpers."""

from __future__ import annotations

import urllib.error
from collections.abc import Callable
from pathlib import Path

from pzi.fetch_helpers import fetch_binary as _fetch_binary
from pzi.pdf_planning import is_pdf_content_type
from pzi.pdf_planning import is_pdf_bytes

FetchBinary = Callable[[str], tuple[bytes, str | None]]
PdfRecord = dict[str, object]


def copy_pdf_to_papers_dir(
    *,
    source_path: str,
    papers_dir: str,
    citekey: str,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
) -> tuple[str | None, str | None]:
    """Copy a local PDF into the papers directory with citekey naming."""
    src = Path(source_path)
    if not src.exists():
        return None, f"source PDF not found: {source_path}"
    try:
        data = src.read_bytes()
    except OSError as exc:
        return None, f"failed to read source PDF: {exc}"

    if not is_pdf_bytes(data):
        return None, f"source file is not a valid PDF: {source_path}"

    from pzi.pdf import write_pdf_bytes

    return write_pdf_bytes(
        data=data,
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=filename_format,
    ), None


def store_pdf_source(
    *,
    source: str,
    papers_dir: str,
    citekey: str,
    fetch_binary: FetchBinary | None = None,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
) -> tuple[str | None, str | None]:
    """Store a PDF from a URL or local path under the deterministic citekey path."""
    if source.startswith(("http://", "https://")):
        return fetch_and_store_pdf(
            url=source,
            papers_dir=papers_dir,
            citekey=citekey,
            fetch_binary=fetch_binary,
            record=record,
            filename_format=filename_format,
        )
    return copy_pdf_to_papers_dir(
        source_path=source,
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=filename_format,
    )


def fetch_and_store_pdf(
    *,
    url: str,
    papers_dir: str,
    citekey: str,
    fetch_binary: FetchBinary | None = None,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
) -> tuple[str | None, str | None]:
    """Download a PDF candidate, validate it, and store it atomically."""
    downloader = fetch_binary or _fetch_binary
    try:
        data, content_type = downloader(url)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return None, (
                f"PDF download blocked (HTTP {exc.code}) from {url}; "
                "use the browser extension or configure browser_pdf_cmd"
            )
        return None, f"failed to download PDF from {url}: HTTP {exc.code} {exc.reason}"
    except (OSError, ValueError) as exc:
        return None, f"failed to download PDF from {url}: {exc}"

    if not is_pdf_content_type(content_type) and not is_pdf_bytes(data):
        if content_type is not None and "text/html" in content_type.lower():
            return None, (
                f"downloaded content from {url} is HTML, not a PDF; "
                "use the browser extension or configure browser_pdf_cmd"
            )
        return None, f"downloaded content from {url} is not a PDF"

    if not is_pdf_bytes(data):  # pragma: no cover — covered by integration/browser tests
        return None, f"downloaded content from {url} is not a PDF"  # pragma: no cover

    from pzi.pdf import write_pdf_bytes

    return write_pdf_bytes(
        data=data,
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=filename_format,
    ), None
