"""PDF acquisition and storage helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

from pzi.fetch_helpers import fetch_binary as _fetch_binary
from pzi.fetch_helpers import fetch_text as _fetch_text

FetchBinary = Callable[[str], tuple[bytes, str | None]]
FetchText = Callable[[str], str]


def is_pdf_bytes(data: bytes) -> bool:
    """Return True when content looks like a PDF by file signature."""
    return data.startswith(b"%PDF-")


def _is_pdf_content_type(content_type: str | None) -> bool | None:
    """Check whether the HTTP Content-Type header indicates PDF content.

    Returns:
        True when content_type explicitly indicates PDF (e.g. 'application/pdf').
        None when the type is ambiguous or missing (not enough signal to decide).
        False when content_type explicitly indicates non-PDF (e.g. 'text/html').
    """
    if content_type is None:
        return None
    ct_lower = content_type.lower()
    # Explicitly PDF
    if "application/pdf" in ct_lower:
        return True
    # Explicitly non-PDF — server is telling us this is markup or JSON
    if any(non_pdf in ct_lower for non_pdf in ("text/html", "application/json", "text/plain")):
        return False
    # Ambiguous — e.g. 'application/octet-stream', empty, other binary
    return None


def plan_pdf_path(*, papers_dir: str, citekey: str) -> str:
    """Return the deterministic destination path for a citekey PDF."""
    return str(Path(papers_dir) / f"{citekey}.pdf")


def write_pdf_bytes(*, data: bytes, papers_dir: str, citekey: str) -> str:
    """Atomically write PDF bytes to planned citekey path."""
    destination = Path(plan_pdf_path(papers_dir=papers_dir, citekey=citekey))
    destination.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        handle.write(data)
        temp_path = Path(handle.name)

    temp_path.replace(destination)
    return str(destination)


def copy_pdf_to_papers_dir(
    *,
    source_path: str,
    papers_dir: str,
    citekey: str,
) -> tuple[str | None, str | None]:
    """Copy a local PDF into the papers directory with citekey naming.

    Returns (destination_path, error).
    """
    src = Path(source_path)
    if not src.exists():
        return None, f"source PDF not found: {source_path}"
    try:
        data = src.read_bytes()
    except OSError as exc:
        return None, f"failed to read source PDF: {exc}"

    if not is_pdf_bytes(data):
        return None, f"source file is not a valid PDF: {source_path}"

    return write_pdf_bytes(data=data, papers_dir=papers_dir, citekey=citekey), None


def fetch_and_store_pdf(
    *,
    url: str,
    papers_dir: str,
    citekey: str,
    fetch_binary: FetchBinary | None = None,
) -> tuple[str | None, str | None]:
    """Download a PDF candidate, validate it, and store it atomically."""
    downloader = fetch_binary or _fetch_binary
    try:
        data, content_type = downloader(url)
    except Exception as exc:
        return None, f"failed to download PDF from {url}: {exc}"

    if not _is_pdf_content_type(content_type) and not is_pdf_bytes(data):
        return None, f"downloaded content from {url} is not a PDF"

    if not is_pdf_bytes(data):
        return None, f"downloaded content from {url} is not a PDF"

    return write_pdf_bytes(data=data, papers_dir=papers_dir, citekey=citekey), None


def fetch_and_store_pdf_with_fallbacks(
    *,
    url: str,
    papers_dir: str,
    citekey: str,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    fetch_binary: FetchBinary | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Download PDF with Cloudflare-bypass fallbacks.

    Tries in order:
    1. Direct download (fastest)
    2. Browser hook (authenticated access via browser profile)
    3. FlareSolverr (bypasses Cloudflare — gray area, warns user)

    Returns: (local_path, warning, error)
    - local_path: path to saved PDF, or None
    - warning: non-critical warning message, or None
    - error: error message if download failed, or None
    """
    # Try direct download first
    result = fetch_and_store_pdf(
        url=url,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
    )
    if result[0] is not None:
        return result[0], None, None

    # Try browser hook (authenticated access)
    if browser_pdf_cmd:
        from pzi.browser_pdf import download_pdf_with_browser

        pdf_bytes = download_pdf_with_browser(
            command=browser_pdf_cmd,
            pdf_url=url,
        )
        if pdf_bytes and is_pdf_bytes(pdf_bytes):
            local_path = write_pdf_bytes(data=pdf_bytes, papers_dir=papers_dir, citekey=citekey)
            return local_path, None, None

    # Try FlareSolverr (gray area — warn user)
    if flaresolverr_url:
        from pzi.flaresolverr import fetch_pdf_via_flaresolverr

        pdf_bytes = fetch_pdf_via_flaresolverr(url, server_url=flaresolverr_url)
        if pdf_bytes and is_pdf_bytes(pdf_bytes):
            warning = (
                "PDF downloaded via FlareSolverr (bypasses Cloudflare protection). "
                "This may violate publisher terms of service. "
                "Consider using browser_pdf_cmd with your institutional profile instead."
            )
            local_path = write_pdf_bytes(data=pdf_bytes, papers_dir=papers_dir, citekey=citekey)
            return local_path, warning, None

    return None, None, f"all download methods failed for {url}"


def fetch_unpaywall_pdf_url(
    doi: str,
    *,
    email: str,
    fetch_text: FetchText | None = None,
) -> str | None:
    """Return best open-access PDF URL from Unpaywall, or None."""
    fn = fetch_text or _fetch_text
    try:
        url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={quote(email, safe='')}"
        data = json.loads(fn(url))
        loc = data.get("best_oa_location") or {}
        pdf = loc.get("url_for_pdf")
        return pdf if isinstance(pdf, str) else None
    except Exception:
        return None
