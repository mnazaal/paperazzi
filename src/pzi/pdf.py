#!/usr/bin/env python3
"""PDF acquisition, storage, and filesystem helpers."""

from __future__ import annotations

import json
import sys
import time as _time
import urllib.error
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import fetch_text as _fetch_text
from pzi.pdf_download import fetch_and_store_pdf, write_pdf_bytes
from pzi.pdf_planning import (
    PdfFallbackSettings,
    PdfRecord,
    build_browser_pdf_command,
    choose_firefox_profile,
    is_pdf_bytes,
    needs_desktop_browser_fallback,
    normalized_hostname,
    parse_firefox_default_profile,
)

FetchBinary = Callable[[str], tuple[bytes, str | None]]
FetchText = Callable[[str], str]


# ---------------------------------------------------------------------------
# Desktop-browser PDF fallback (merged from pdf_desktop.py)
# ---------------------------------------------------------------------------


def _wait_for_stable_file(
    path: Path,
    *,
    stable_seconds: float = 0.35,
    sleep: Callable[[float], None] = _time.sleep,
) -> bool:
    """Return True after file size/mtime stay unchanged briefly."""
    try:
        first = path.stat()
    except OSError:
        return False
    sleep(stable_seconds)
    try:
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and first.st_mtime == second.st_mtime


def _newest_first(download_dir: Path) -> list[Path]:
    """PDFs in *download_dir*, newest first, skipping any that vanish mid-scan.

    The browser is writing into this directory while we watch it, so a partial
    download that completes and is renamed between the glob and the sort used to
    raise ``FileNotFoundError`` out of ``sorted``'s key — killing the fallback at
    the moment it was about to succeed. The loop body already re-stats each
    candidate and tolerates one disappearing.
    """
    dated: list[tuple[float, Path]] = []
    for path in download_dir.glob("*.pdf"):
        try:
            dated.append((path.stat().st_mtime, path))
        except OSError:
            continue
    dated.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in dated]


def fetch_pdf_via_desktop_browser_download(
    *,
    url: str,
    papers_dir: str,
    citekey: str,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
    timeout: int | None = None,
    settings: PdfFallbackSettings | None = None,
    sleep: Callable[[float], None] = _time.sleep,
    monotonic: Callable[[], float] = _time.monotonic,
) -> tuple[str | None, str | None]:
    """Open URL in user's browser and import newly downloaded matching PDF.

    *sleep* and *monotonic* are the watch loop's clock. They are injectable so a
    test can drive the loop to its deadline without spending real seconds in it;
    the mtime comparison against ``started_at`` stays on the wall clock, because
    it is compared against timestamps the filesystem writes.
    """
    settings = settings or PdfFallbackSettings.from_environment()
    if settings.disable_desktop_browser:
        # A *skipped* stage, not one that ran and found nothing. Returning a
        # bare `None, None` made the caller append "desktop browser download:
        # no PDF appeared", so a user who had switched the stage off was told
        # it had been tried.
        return None, "skipped (PZI_DISABLE_DESKTOP_BROWSER_FALLBACK is set)"

    # `webbrowser.open` hands the string straight to the OS scheme handler, and
    # this URL came from a metadata provider or a captured page — so a `file:`,
    # `javascript:` or `data:` URL would be run by whatever the desktop is
    # configured to open it with. Everything downstream expects an http(s)
    # download; nothing else has a reason to reach here.
    if urlsplit(url).scheme not in {"http", "https"}:
        return None, f"refusing to open a non-http(s) URL in the browser: {url!r}"

    download_dir = settings.download_dir
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # This is the *last* rung. `fetch_and_store_pdf_with_fallbacks` has no
        # exception handling of its own, so raising here discarded every
        # stage_error the earlier rungs had recorded and the caller saw a
        # traceback instead of "here is what each stage tried". An unwritable
        # PZI_DOWNLOAD_DIR (or $HOME/Downloads) reports like any other stage
        # failure: return None with a reason.
        return None, f"desktop download dir unavailable: {exc}"
    timeout = timeout or settings.desktop_timeout
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
        # Same distinction: the browser never opened, so nothing was watched
        # for. "No PDF appeared" describes a wait that did not happen.
        return None, "could not open a desktop browser"

    deadline = monotonic() + timeout
    seen: set[Path] = set()
    while monotonic() < deadline:
        candidates = _newest_first(download_dir)
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
            if not _wait_for_stable_file(candidate, sleep=sleep):
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
            # Name the actual host: this path serves every host in
            # desktop_fallback_hosts, which is configurable and whose *default*
            # already includes Research Square, SSRN and Authorea -- so the
            # hardcoded "bioRxiv/medRxiv" was wrong for three of five hosts
            # before any user configuration.
            warning = (
                f"PDF attached from desktop browser download because direct "
                f"download from {normalized_hostname(url) or 'the publisher'} "
                f"was blocked."
            )
            return local_path, warning
        sleep(1)

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
    settings: PdfFallbackSettings | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Download PDF with direct, server-browser, browser-hook, and FlareSolverr fallbacks.

    *settings* carries the fallback knobs (download directory, timeouts, browser
    choice). It is resolved from the environment once here when not supplied,
    rather than each helper reading ``os.environ`` at the moment it needs a value.
    """
    settings = settings or PdfFallbackSettings.from_environment()

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
    # One line per stage that ran and did not produce a PDF. Only the direct
    # stage used to contribute a reason, so a broken `browser_pdf_cmd`, a
    # FlareSolverr failure or a server-browser returning HTML were stderr-only:
    # under `--json` the operator could not tell which stage broke, or whether
    # it ran at all.
    stage_errors: list[str] = []
    if direct_error:
        stage_errors.append(f"direct download: {direct_error}")

    effective_browser_pdf_cmd = browser_pdf_cmd or _auto_browser_pdf_cmd_for_url(
        url,
        browser=browser,
        desktop_fallback_hosts=desktop_fallback_hosts,
        settings=settings,
    )
    # When the request originates from the browser extension (browser is set),
    # skip the Playwright hook: the extension handles authenticated PDF download
    # itself via /attach-pdf-bytes using the live browser session.
    extension_capture = browser is not None and browser_pdf_cmd is None

    # Server-side persistent browser takes priority over subprocess hook.
    if api_url and browser_hook and not extension_capture:
        from pzi.server_browser import download_via_server_api

        server_errors: list[str] = []
        pdf_bytes = download_via_server_api(
            api_url, url, auth_token=api_auth_token, errors=server_errors,
        )
        if not pdf_bytes:
            stage_errors.append(
                "server browser: " + (server_errors[0] if server_errors else "no PDF returned")
            )
        elif not is_pdf_bytes(pdf_bytes):
            # Unreachable today: `download_via_server_api` returns bytes only
            # when they start with `%PDF-`, and the `browser_pdf_cmd` and
            # FlareSolverr helpers below do the same. Kept rather than deleted
            # (deleting all three has been proposed) because that is a
            # property of three other modules, not a local invariant, and these
            # bytes are about to be written to the user's papers directory. The
            # check costs one comparison.
            stage_errors.append("server browser: response was not a PDF")
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
        and not settings.skip_browser_hook
        and not extension_capture
    ):
        from pzi.browser_pdf import download_pdf_with_browser

        pdf_bytes = download_pdf_with_browser(command=effective_browser_pdf_cmd, pdf_url=url)
        if not pdf_bytes:
            stage_errors.append("browser_pdf_cmd: no PDF returned")
        elif not is_pdf_bytes(pdf_bytes):
            stage_errors.append("browser_pdf_cmd: response was not a PDF")
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
        if not pdf_bytes:
            stage_errors.append("FlareSolverr: no PDF returned")
        elif not is_pdf_bytes(pdf_bytes):  # pragma: no cover — defensive
            stage_errors.append("FlareSolverr: response was not a PDF")
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
        needs_desktop_browser_fallback(url, hosts=desktop_fallback_hosts)
        and not extension_capture
    ):
        desktop_path, desktop_warning = fetch_pdf_via_desktop_browser_download(
            url=url,
            papers_dir=papers_dir,
            citekey=citekey,
            record=record,
            filename_format=filename_format,
            settings=settings,
        )
        if desktop_path is not None:
            return desktop_path, desktop_warning, None
        stage_errors.append(
            f"desktop browser download: {desktop_warning or 'no PDF appeared'}"
        )

    detail = f"all download methods failed for {url}"
    if stage_errors:
        detail = f"{detail} ({'; '.join(stage_errors)})"
    if not browser_pdf_cmd and not flaresolverr_url:
        detail = (
            f"{detail}; if this site is browser-protected, configure browser_pdf_cmd "
            "or attach from the browser extension"
        )
    return None, None, detail


#: Ask for the next PDF source, given the record so far and the URLs that have
#: already failed to download. Returns a record carrying a fresh ``pdf_url``, or
#: ``None`` when discovery has nothing left to offer.
NextPdfCandidate = Callable[
    ["NormalizedRecord", frozenset[str]], "NormalizedRecord | None"
]

#: How many distinct *sources* one acquisition may try. Discovery terminates on
#: its own (each round excludes one more URL), but a provider that keeps
#: inventing URLs should not keep a single command downloading forever.
MAX_PDF_SOURCE_ATTEMPTS = 4


class PdfSourceOutcome(NamedTuple):
    """What trying successive PDF sources produced."""

    local_pdf_path: str | None
    warning: str | None
    #: One entry per source that failed, in the order they were tried.
    errors: list[str]
    #: The record whose ``pdf_url`` produced the file — not necessarily the one
    #: passed in, since a later candidate may be what actually worked.
    record: NormalizedRecord


def fetch_and_store_pdf_trying_sources(
    *,
    url: str,
    record: NormalizedRecord,
    next_candidate: NextPdfCandidate | None = None,
    fetch_pdf: Callable[..., tuple[str | None, str | None, str | None]] | None = None,
    **fallback_kwargs: Any,
) -> PdfSourceOutcome:
    """Download *url*, falling back to the next discovered source on failure.

    Every rung of :func:`fetch_and_store_pdf_with_fallbacks` — direct, server
    browser, ``browser_pdf_cmd``, FlareSolverr, desktop download — retries the
    *same* URL. They are transport fallbacks, not source fallbacks, so a
    candidate the publisher 403s ended acquisition outright, with an
    open-access mirror one discovery step further down never consulted.

    ``next_candidate`` is what asks for that next source. Callers with no way to
    re-run discovery pass ``None`` and get the old single-attempt behaviour.

    Lazy on purpose: ``next_candidate`` runs only after a candidate has
    exhausted every transport, so the ordinary case where the first URL
    downloads costs no extra provider calls.

    This lives here rather than at any one call site because there are three —
    ``add``, ``pdf retry`` and ``promote`` — and a loop copied into each is how
    they drift.
    """
    downloader = fetch_pdf or fetch_and_store_pdf_with_fallbacks
    tried: list[str] = []
    errors: list[str] = []
    attempt_record = record
    attempt_url = url
    for _ in range(MAX_PDF_SOURCE_ATTEMPTS):
        local_pdf_path, warning, error = downloader(
            url=attempt_url, record=attempt_record, **fallback_kwargs
        )
        if local_pdf_path is not None:
            return PdfSourceOutcome(local_pdf_path, warning, errors, attempt_record)

        tried.append(attempt_url)
        if error is not None:
            errors.append(error)
        if next_candidate is None:
            break
        following = next_candidate(attempt_record, frozenset(tried))
        if following is None:
            break
        following_url = following.get("pdf_url")
        if not isinstance(following_url, str) or not following_url.strip():
            break
        if following_url in tried:  # pragma: no cover — discovery excludes these
            break
        attempt_record, attempt_url = following, following_url

    return PdfSourceOutcome(None, None, errors, record)


def _auto_browser_pdf_cmd_for_url(
    url: str,
    browser: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    settings: PdfFallbackSettings | None = None,
) -> str | None:
    """Return built-in browser fallback command for hosts that block direct PDF fetches."""
    from pzi.config import DEFAULT_DESKTOP_FALLBACK_HOSTS

    hostname = normalized_hostname(url)
    # `is None`, not truthiness: an explicitly configured
    # `desktop_fallback_hosts = []` means "never launch the browser for this",
    # and `or` re-expanded it to the built-in list — the fifth site of a class
    # fixed at four others.
    effective_hosts = (
        set(DEFAULT_DESKTOP_FALLBACK_HOSTS)
        if desktop_fallback_hosts is None
        else desktop_fallback_hosts
    )
    if hostname in effective_hosts:
        return _auto_browser_pdf_cmd(browser=browser, settings=settings)
    return None


def _auto_browser_pdf_cmd(
    browser: str | None = None, settings: PdfFallbackSettings | None = None
) -> str:
    settings = settings or PdfFallbackSettings.from_environment()
    env_cmd = settings.browser_pdf_cmd
    env_profile = settings.browser_profile
    env_browser = settings.browser
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
    # Chrome/Chromium on Linux honors $XDG_CONFIG_HOME (falling back to
    # ~/.config), so resolve its profile dir the same way rather than
    # hardcoding ~/.config.
    from pzi.config import xdg_config_home

    base = Path(xdg_config_home(str(Path.home()))) / "google-chrome"
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
