#!/usr/bin/env python3
"""PDF download and local-source storage helpers."""

from __future__ import annotations

import http.client
import os
import tempfile
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from pzi import exit_codes
from pzi.errors import PziError
from pzi.fetch_helpers import fetch_binary as _fetch_binary
from pzi.pdf_planning import (
    is_pdf_bytes,
    is_pdf_content_type,
    plan_pdf_path,
)

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


    return write_pdf_bytes(
        data=data,
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=filename_format,
    ), None


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
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # http.client.IncompleteRead (a chunked body cut short mid-download)
        # derives from HTTPException, not OSError, so without it a truncated
        # download escapes as a raw traceback out of `pzi add` / `pzi pdf retry`.
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


    return write_pdf_bytes(
        data=data,
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=filename_format,
    ), None


# ---------------------------------------------------------------------------
# PDF byte storage (real filesystem I/O — moved out of the pure
# pzi.pdf_planning module, which only computes destination paths)
# ---------------------------------------------------------------------------


#: How many ``-1``, ``-2``, … names to try before giving up. A papers dir with
#: this many collisions for one citekey is a bug or an attack, not a library.
_MAX_PDF_COLLISION_SUFFIX = 1000


def resolve_pdf_destination(destination: Path, data: bytes) -> Path:
    """Return existing identical path or first free suffixed path.

    "Free" is decided with :func:`os.path.lexists`, not ``Path.exists()``: a
    *dangling* symlink does not exist by the latter but very much occupies the
    name, so ``os.link`` below refuses it. The two disagreeing made
    ``write_pdf_bytes`` hand back the same occupied path forever — a 100%-CPU
    hang in ``pzi add``, ``pzi pdf retry``, ``pzi pdf attach`` and any HTTP
    worker that reached them. Symlinked papers directories are a normal way to
    keep PDFs on another volume.
    """
    candidate = destination
    for n in range(_MAX_PDF_COLLISION_SUFFIX + 1):
        if not os.path.lexists(candidate):
            return candidate
        try:
            if candidate.read_bytes() == data:
                return candidate
        except OSError:
            pass
        candidate = destination.with_stem(f"{destination.stem}-{n + 1}")
    raise PziError(
        f"could not find a free filename for {destination.name} in "
        f"{destination.parent} after {_MAX_PDF_COLLISION_SUFFIX} attempts",
        code=exit_codes.ENVIRONMENT,
    )


def write_pdf_bytes(
    *,
    data: bytes,
    papers_dir: str,
    citekey: str,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
) -> str:
    """Atomically write PDF bytes to planned citekey path."""
    destination = Path(
        plan_pdf_path(
            papers_dir=papers_dir,
            citekey=citekey,
            record=record,
            filename_format=filename_format,
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)

    destination = resolve_pdf_destination(destination, data)
    if destination.exists():
        return str(destination)

    # Bounded: every iteration must either return or move to a *new* name, and
    # an unbounded retry loop with no progress guarantee has no business on a
    # write path — that is exactly how the dangling-symlink case span forever.
    for _attempt in range(_MAX_PDF_COLLISION_SUFFIX):
        temp_fd, temp_name = tempfile.mkstemp(
            dir=str(destination.parent), prefix=".pdf-", suffix=".tmp"
        )
        try:
            os.fchmod(temp_fd, 0o600)
            _write_all(temp_fd, data)
        finally:
            os.close(temp_fd)
        temp_path = Path(temp_name)
        try:
            os.link(temp_path, destination)
            return str(destination)
        except FileExistsError:
            try:
                if destination.read_bytes() == data:
                    return str(destination)
            except OSError:
                pass
            destination = resolve_pdf_destination(destination, data)
            if destination.exists():
                return str(destination)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    raise PziError(
        f"could not store the PDF in {destination.parent}: "
        f"{_MAX_PDF_COLLISION_SUFFIX} candidate filenames were all taken",
        code=exit_codes.ENVIRONMENT,
    )


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    total = 0
    while total < len(view):
        written = os.write(fd, view[total:])
        if written <= 0:
            raise OSError("short write while storing PDF")
        total += written
