#!/usr/bin/env python3
"""PDF acquisition, storage, and filesystem helpers."""

from __future__ import annotations

import json
import os
import sys
import time as _time
import urllib.error
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from pzi.fetch_helpers import fetch_text as _fetch_text
from pzi.pdf_download import (
    copy_pdf_to_papers_dir,  # noqa: F401  # re-exported
    fetch_and_store_pdf,
    store_pdf_source,  # noqa: F401  # re-exported
)
from pzi.pdf_planning import (
    PdfRecord,
    build_browser_pdf_command,
    choose_firefox_profile,
    is_pdf_bytes,
    needs_desktop_browser_fallback,
    normalized_hostname,
    parse_firefox_default_profile,
    write_pdf_bytes,
)

FetchBinary = Callable[[str], tuple[bytes, str | None]]
FetchText = Callable[[str], str]


# ---------------------------------------------------------------------------
# Desktop-browser PDF fallback (merged from pdf_desktop.py)
# ---------------------------------------------------------------------------


def _wait_for_stable_file(path: Path, *, stable_seconds: float = 0.35) -> bool:
    """Return True after file size/mtime stay unchanged briefly."""
    try:
        first = path.stat()
    except OSError:
        return False
    _time.sleep(stable_seconds)
    try:
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and first.st_mtime == second.st_mtime


def _desktop_browser_timeout(raw: str | None) -> int:
    if raw is None:
        return 300
    try:
        return max(30, int(raw))
    except ValueError:
        return 300


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
    timeout = timeout or _desktop_browser_timeout(os.environ.get("PZI_DESKTOP_BROWSER_TIMEOUT"))
    started_at = _time.time()
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

    deadline = _time.monotonic() + timeout
    seen: set[Path] = set()
    while _time.monotonic() < deadline:
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
            from pzi.pdf_planning import candidate_matches_requested_pdf_name

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
            if not _wait_for_stable_file(candidate):
                continue
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            if not is_pdf_bytes(data):
                seen.add(candidate)
                continue
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
        _time.sleep(1)

    print(
        "Timed out waiting for a downloaded PDF. If the PDF opened in a viewer, "
        "click its download/save button, or rerun with "
        "PZI_DESKTOP_BROWSER_TIMEOUT=300.",
        file=sys.stderr,
    )
    return None, None


# ---------------------------------------------------------------------------
# Main PDF acquisition
# ---------------------------------------------------------------------------

def fetch_and_store_pdf_with_fallbacks(
    *,
    url: str,
    papers_dir: str,
    citekey: str,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    browser: str | None = None,
    browser_hook: bool = True,
    fetch_binary: FetchBinary | None = None,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
    api_url: str | None = None,
    api_auth_token: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    ezproxy_host: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Download PDF with direct, server-browser, browser-hook, and FlareSolverr fallbacks."""

    result = fetch_and_store_pdf(
        url=url,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
        record=record,
        filename_format=filename_format,
        ezproxy_host=ezproxy_host,
    )
    if result[0] is not None:
        return result[0], None, None
    direct_error = result[1]

    effective_browser_pdf_cmd = browser_pdf_cmd or _auto_browser_pdf_cmd_for_url(
        url, browser=browser, desktop_fallback_hosts=desktop_fallback_hosts
    )
    # When the request originates from the browser extension (browser is set),
    # skip the Playwright hook: the extension handles authenticated PDF download
    # itself via /attach-pdf-bytes using the live browser session.
    extension_capture = browser is not None and browser_pdf_cmd is None

    # Server-side persistent browser takes priority over subprocess hook.
    if api_url and browser_hook and not extension_capture:
        from pzi.server_browser import download_via_server_api

        pdf_bytes = download_via_server_api(
            api_url, url, auth_token=api_auth_token,
        )
        if pdf_bytes and is_pdf_bytes(pdf_bytes):
            local_path = write_pdf_bytes(
                data=pdf_bytes,
                papers_dir=papers_dir,
                citekey=citekey,
                record=record,
                filename_format=filename_format,
            )
            return local_path, None, None

    if (
        effective_browser_pdf_cmd
        and browser_hook
        and not os.environ.get("PZI_SKIP_BROWSER_HOOK")
        and not extension_capture
    ):
        from pzi.browser_pdf import download_pdf_with_browser

        pdf_bytes = download_pdf_with_browser(command=effective_browser_pdf_cmd, pdf_url=url)
        if pdf_bytes and is_pdf_bytes(pdf_bytes):
            local_path = write_pdf_bytes(
                data=pdf_bytes,
                papers_dir=papers_dir,
                citekey=citekey,
                record=record,
                filename_format=filename_format,
            )
            return local_path, None, None

    if flaresolverr_url:
        from pzi.flaresolverr import fetch_pdf_via_flaresolverr

        pdf_bytes = fetch_pdf_via_flaresolverr(url, server_url=flaresolverr_url)
        if pdf_bytes and is_pdf_bytes(pdf_bytes):  # pragma: no branch
            warning = (
                "PDF downloaded via FlareSolverr (bypasses Cloudflare protection). "
                "This may violate publisher terms of service. "
                "Consider using browser_pdf_cmd with your institutional profile instead."
            )
            local_path = write_pdf_bytes(
                data=pdf_bytes,
                papers_dir=papers_dir,
                citekey=citekey,
                record=record,
                filename_format=filename_format,
            )
            return local_path, warning, None

    if (
        _needs_desktop_browser_fallback(
            url, desktop_fallback_hosts=desktop_fallback_hosts
        )
        and not extension_capture
    ):
        desktop_path, desktop_warning = fetch_pdf_via_desktop_browser_download(
            url=url,
            papers_dir=papers_dir,
            citekey=citekey,
            record=record,
            filename_format=filename_format,
        )
        if desktop_path is not None:
            return desktop_path, desktop_warning, None

    detail = f"all download methods failed for {url}"
    if direct_error:
        detail = f"{detail} (direct download: {direct_error})"
    if not browser_pdf_cmd and not flaresolverr_url:
        detail = (
            f"{detail}; if this site is browser-protected, configure browser_pdf_cmd "
            "or attach from the browser extension"
        )
    return None, None, detail


def _auto_browser_pdf_cmd_for_url(
    url: str,
    browser: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
) -> str | None:
    """Return built-in browser fallback command for hosts that block direct PDF fetches."""
    hostname = normalized_hostname(url)
    effective_hosts = desktop_fallback_hosts or {"biorxiv.org", "medrxiv.org"}
    if hostname in effective_hosts:
        return _auto_browser_pdf_cmd(browser=browser)
    return None


def _needs_desktop_browser_fallback(
    url: str,
    desktop_fallback_hosts: set[str] | None = None,
) -> bool:
    return needs_desktop_browser_fallback(url, hosts=desktop_fallback_hosts)


def _auto_browser_pdf_cmd(browser: str | None = None) -> str:
    env_cmd = os.environ.get("PZI_BROWSER_PDF_CMD")
    env_profile = os.environ.get("PZI_BROWSER_PROFILE")
    env_browser = os.environ.get("PZI_BROWSER", "firefox")
    requested_browser = browser
    firefox_profile = None
    chrome_profile = None
    if not env_cmd and not env_profile:
        preferred = requested_browser or env_browser or "firefox"
        if preferred == "firefox":
            firefox_profile = _default_firefox_profile()
            if firefox_profile is None:
                chrome_profile = _default_chrome_profile()
        else:
            chrome_profile = _default_chrome_profile()
            if chrome_profile is None:
                firefox_profile = _default_firefox_profile()

    return build_browser_pdf_command(
        env_cmd=env_cmd,
        env_profile=env_profile,
        env_browser=env_browser,
        requested_browser=requested_browser,
        python_executable=sys.executable,
        firefox_profile=firefox_profile,
        chrome_profile=chrome_profile,
    )


def _default_chrome_profile() -> Path | None:
    base = Path.home() / ".config" / "google-chrome"
    if (base / "Default").exists():
        return base
    return base if base.exists() else None


def _read_firefox_default_profile() -> Path | None:
    """Parse Firefox profiles.ini to find the default profile path.

    Returns the full path to the profile directory marked Default=1,
    or None if profiles.ini is missing or unreadable.
    """
    base = Path.home() / ".mozilla" / "firefox"
    profiles_ini = base / "profiles.ini"
    if not profiles_ini.exists():
        return None
    try:
        return parse_firefox_default_profile(profiles_ini.read_text(), base_dir=base)
    except OSError:
        return None


def _default_firefox_profile() -> Path | None:
    base = Path.home() / ".mozilla" / "firefox"
    if not base.exists():
        return None

    default_from_ini = _read_firefox_default_profile()
    profile_dirs = [path for path in base.iterdir() if path.is_dir()]
    return choose_firefox_profile(
        default_from_ini=default_from_ini,
        default_exists=lambda path: path.exists(),
        profile_dirs=profile_dirs,
        modified_time=lambda path: path.stat().st_mtime,
    )


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
    except (OSError, json.JSONDecodeError, ValueError, urllib.error.HTTPError):
        return None


# ---------------------------------------------------------------------------
# PDF filesystem rollback helpers (merged from pdf_files.py)
# ---------------------------------------------------------------------------


def snapshot_pdf_paths(papers_dir: str) -> set[Path]:
    """Return resolved existing PDF paths, or empty set if directory cannot be read."""
    try:
        return {path.resolve() for path in Path(papers_dir).glob("*.pdf")}
    except OSError:
        return set()


def remove_new_pdf(path: str | None, existing_paths: set[Path]) -> None:
    """Remove path only when it was not present in prior snapshot."""
    if not path:
        return
    candidate = Path(path)
    try:
        resolved = candidate.resolve()
    except OSError:
        return
    if resolved in existing_paths:
        return
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        return
