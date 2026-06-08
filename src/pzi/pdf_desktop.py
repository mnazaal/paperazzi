#!/usr/bin/env python3
"""Desktop-browser PDF fallback helpers."""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

from pzi.pdf_planning import candidate_matches_requested_pdf_name, is_pdf_bytes, write_pdf_bytes

PdfRecord = dict[str, object]


def fetch_pdf_via_desktop_browser_download(
    *,
    url: str,
    papers_dir: str,
    citekey: str,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
    timeout: int | None = None,
) -> tuple[str | None, str | None]:
    """Open URL in user's browser and import newly downloaded matching PDF."""
    if os.environ.get("PZI_DISABLE_DESKTOP_BROWSER_FALLBACK"):
        return None, None

    download_dir = Path(
        os.environ.get("PZI_DOWNLOAD_DIR", str(Path.home() / "Downloads"))
    ).expanduser()
    download_dir.mkdir(parents=True, exist_ok=True)
    timeout = timeout or desktop_browser_timeout(os.environ.get("PZI_DESKTOP_BROWSER_TIMEOUT"))
    started_at = time.time()
    existing_downloads = set(download_dir.glob("*.pdf"))

    print(
        "Direct PDF download was blocked. Opening the PDF in your desktop browser.\n"
        "Complete any verification/CAPTCHA, then let the PDF download or click the "
        f"browser download button. Watching {download_dir} for {timeout}s …",
        file=sys.stderr,
    )
    opened = webbrowser.open(url)
    if not opened:
        print("Could not open desktop browser for PDF fallback.", file=sys.stderr)
        return None, None

    deadline = time.monotonic() + timeout
    seen: set[Path] = set()
    while time.monotonic() < deadline:
        candidates = sorted(
            download_dir.glob("*.pdf"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate in seen:
                continue
            try:
                stat = candidate.stat()
            except OSError:
                continue
            if candidate in existing_downloads:
                seen.add(candidate)
                continue
            if stat.st_mtime + 1 < started_at:
                continue
            if not candidate_matches_requested_pdf_name(
                filename=candidate.name,
                url=url,
                citekey=citekey,
                record=record,
            ):
                print(
                    "Ignoring unrelated desktop browser PDF download "
                    f"{candidate.name}; filename did not match requested URL, DOI, or citekey.",
                    file=sys.stderr,
                )
                seen.add(candidate)
                continue
            if not wait_for_stable_file(candidate):
                continue
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            from pzi.pdf import is_pdf_bytes

            if not is_pdf_bytes(data):
                seen.add(candidate)
                continue
            from pzi.pdf import write_pdf_bytes

            local_path = write_pdf_bytes(
                data=data,
                papers_dir=papers_dir,
                citekey=citekey,
                record=record,
                filename_format=filename_format,
            )
            warning = (
                "PDF attached from desktop browser download because direct "
                "bioRxiv/medRxiv download was blocked."
            )
            return local_path, warning
        time.sleep(1)

    print(
        "Timed out waiting for a downloaded PDF. If the PDF opened in a viewer, "
        "click its download/save button, or rerun with "
        "PZI_DESKTOP_BROWSER_TIMEOUT=300.",
        file=sys.stderr,
    )
    return None, None


def wait_for_stable_file(path: Path, *, stable_seconds: float = 0.35) -> bool:
    """Return True after file size/mtime stay unchanged briefly."""
    try:
        first = path.stat()
    except OSError:
        return False
    time.sleep(stable_seconds)
    try:
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and first.st_mtime == second.st_mtime


def desktop_browser_timeout(raw: str | None) -> int:
    if raw is None:
        return 300
    try:
        return max(30, int(raw))
    except ValueError:
        return 300
