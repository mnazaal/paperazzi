#!/usr/bin/env python3
"""PDF download and local-source storage helpers."""

from __future__ import annotations

import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from pzi.fetch_helpers import fetch_binary as _fetch_binary
from pzi.pdf_planning import is_pdf_bytes, is_pdf_content_type

FetchBinary = Callable[[str], tuple[bytes, str | None]]
PdfRecord = Mapping[str, object]


def _ezproxy_url(url: str, proxy_host: str) -> str:
    """Rewrite a URL through an EZProxy host.

    Converts ``https://doi.org/10.1038/...`` to
    ``https://doi-org.proxy.lib.university.edu/10.1038/...``.
    """
    # Strip scheme if user accidentally passes a URL (defense-in-depth).
    host_part = proxy_host
    if "://" in host_part:
        host_part = urlsplit(host_part).hostname or host_part
    parsed = urlsplit(url)
    host = parsed.hostname.replace(".", "-") if parsed.hostname else ""
    base = f"https://{host}.{host_part}{parsed.path}"
    return f"{base}?{parsed.query}" if parsed.query else base


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
    ezproxy_host: str | None = None,
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
            ezproxy_host=ezproxy_host,
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
    ezproxy_host: str | None = None,
) -> tuple[str | None, str | None]:
    """Download a PDF candidate, validate it, and store it atomically."""
    allow_host: str | None = None
    if ezproxy_host:
        url = _ezproxy_url(url, ezproxy_host)
        # The rewritten host is an explicitly-configured, trusted proxy; allow
        # it to resolve to a private/campus IP that the SSRF guard would
        # otherwise reject.
        allow_host = urlsplit(url).hostname
    downloader = fetch_binary or _fetch_binary
    try:
        if allow_host and downloader is _fetch_binary:
            data, content_type = _fetch_binary(url, allow_host=allow_host)
        else:
            data, content_type = downloader(url)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return None, (
                f"PDF download blocked (HTTP {exc.code}) from {url}; "
                "use the browser extension, configure browser_pdf_cmd, "
                "or set ezproxy_host for institutional access"
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
