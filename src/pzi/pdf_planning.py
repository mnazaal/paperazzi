"""Pure PDF acquisition planning helpers — content-type checks, path planning,
and filename matching."""

from __future__ import annotations

import configparser
import os
import shlex
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from pzi.format_templates import format_pdf_filename

PdfRecord = Mapping[str, object]


def is_pdf_bytes(data: bytes) -> bool:
    """Return True when content looks like a PDF by file signature."""
    return data.startswith(b"%PDF-")


def plan_pdf_path(
    *,
    papers_dir: str,
    citekey: str,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
) -> str:
    """Return deterministic destination path for a citekey PDF."""
    if filename_format and record is not None:
        filename = format_pdf_filename(filename_format, {**record, "citekey": citekey})
    else:
        filename = f"{citekey}.pdf"
    # Prevent path traversal: only use the final basename component.
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name in (".", ".."):
        safe_name = f"{citekey}.pdf"
    return str(Path(papers_dir) / safe_name)


def is_pdf_content_type(content_type: str | None) -> bool | None:
    """Classify HTTP Content-Type signal for PDF downloads.

    Returns True for explicit PDF, False for explicit non-PDF, and None when
    content type is missing or ambiguous.
    """
    if content_type is None:
        return None
    ct_lower = content_type.lower()
    if "application/pdf" in ct_lower:
        return True
    if any(non_pdf in ct_lower for non_pdf in ("text/html", "application/json", "text/plain")):
        return False
    return None


def normalized_hostname(url: str) -> str | None:
    """Return lowercase hostname without leading www., or None for invalid URLs."""
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def needs_desktop_browser_fallback(url: str, *, hosts: set[str] | None = None) -> bool:
    """Return True for hosts where direct PDF download is often blocked."""
    hostname = normalized_hostname(url)
    if hostname is None:
        return False
    return hostname in (hosts or {"biorxiv.org", "medrxiv.org"})


def candidate_matches_requested_pdf_name(
    *,
    filename: str,
    url: str,
    citekey: str,
    record: PdfRecord | None = None,
) -> bool:
    """Return True when browser-downloaded filename correlates with request."""
    haystack = filename_match_text(filename)
    tokens = requested_pdf_match_tokens(url=url, citekey=citekey, record=record)
    if any(token in haystack for token in tokens):
        return True
    return haystack in requested_pdf_domain_tokens(url)


def requested_pdf_match_tokens(
    *,
    url: str,
    citekey: str,
    record: PdfRecord | None = None,
) -> set[str]:
    """Return strong filename tokens for matching requested browser downloads."""
    tokens: set[str] = set()
    for raw in (citekey, url_basename(url)):
        token = filename_match_text(raw)
        if len(token) >= 8:
            tokens.add(token)
    doi = record.get("doi") if record else None
    if isinstance(doi, str):
        doi_tail = doi.rstrip("/").split("/")[-1]
        token = filename_match_text(doi_tail)
        if len(token) >= 8:
            tokens.add(token)
    return tokens


def requested_pdf_domain_tokens(url: str) -> set[str]:
    """Return weak hostname tokens; only exact filename matches may use these."""
    tokens: set[str] = set()
    try:
        hostname = (urlsplit(url).hostname or "").lower()
        for part in hostname.split("."):
            part = part.strip()
            if part and part not in _GENERIC_HOSTNAME_PARTS and len(part) >= 5:
                tokens.add(part)
    except ValueError:
        pass
    return tokens


def url_basename(url: str) -> str:
    """Return path basename from URL, or empty string when URL is invalid."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    return Path(path).name


def filename_match_text(value: str) -> str:
    """Normalize filename-ish text for PDF candidate matching."""
    text = value.lower().strip()
    if text.endswith(".pdf"):
        text = text[:-4]
    return "".join(ch for ch in text if ch.isalnum())


_GENERIC_HOSTNAME_PARTS = {
    "www",
    "com",
    "org",
    "net",
    "edu",
    "gov",
    "io",
    "co",
    "uk",
    "de",
    "fr",
    "jp",
}


def resolve_pdf_destination(destination: Path, data: bytes) -> Path:
    """Return existing identical path or first free suffixed path."""
    candidate = destination
    n = 0
    while True:
        if not candidate.exists():
            return candidate
        try:
            if candidate.read_bytes() == data:
                return candidate
        except OSError:
            pass
        n += 1
        candidate = destination.with_stem(f"{destination.stem}-{n}")


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

    while True:
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


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    total = 0
    while total < len(view):
        written = os.write(fd, view[total:])
        if written <= 0:
            raise OSError("short write while storing PDF")
        total += written


# ---------------------------------------------------------------------------
# Browser PDF command planning (merged from pdf_browser_plan.py)
# ---------------------------------------------------------------------------


def build_browser_pdf_command(
    *,
    env_cmd: str | None,
    env_profile: str | None,
    env_browser: str | None,
    requested_browser: str | None,
    python_executable: str,
    firefox_profile: Path | None,
    chrome_profile: Path | None,
) -> str:
    """Build browser hook command from explicit inputs."""
    if env_cmd:
        return env_cmd

    effective_env_browser = env_browser or "firefox"
    if env_profile:
        return _profile_command(
            python_executable=python_executable,
            browser=effective_env_browser,
            profile=Path(env_profile).expanduser(),
        )

    preferred = requested_browser or effective_env_browser or "firefox"
    if preferred == "firefox":
        if firefox_profile is not None:
            return _profile_command(
                python_executable=python_executable,
                browser="firefox",
                profile=firefox_profile,
            )
        if chrome_profile is not None:
            return _profile_command(
                python_executable=python_executable,
                browser="chrome",
                profile=chrome_profile,
            )
    else:
        if chrome_profile is not None:
            return _profile_command(
                python_executable=python_executable,
                browser="chrome",
                profile=chrome_profile,
            )
        if firefox_profile is not None:
            return _profile_command(
                python_executable=python_executable,
                browser="firefox",
                profile=firefox_profile,
            )

    return (
        f"{shlex.quote(python_executable)} -m pzi.browser_pdf_hook --browser chromium "
        "--headful --challenge-timeout 120"
    )


def _profile_command(*, python_executable: str, browser: str, profile: Path) -> str:
    return (
        f"{shlex.quote(python_executable)} -m pzi.browser_pdf_hook "
        f"--browser {shlex.quote(browser)} "
        f"--profile {shlex.quote(str(profile))} "
        "--headful --challenge-timeout 120"
    )


def parse_firefox_default_profile(text: str, *, base_dir: Path) -> Path | None:
    """Parse Firefox profiles.ini text and return profile marked Default=1."""
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return None

    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        if parser.get(section, "Default", fallback="0") != "1":
            continue
        path = parser.get(section, "Path", fallback="")
        if not path:
            continue
        is_relative = parser.get(section, "IsRelative", fallback="1") == "1"
        if is_relative:
            return base_dir / path
        return Path(path).expanduser()
    return None


def choose_firefox_profile(
    *,
    default_from_ini: Path | None,
    default_exists: Callable[[Path], bool],
    profile_dirs: Iterable[Path],
    modified_time: Callable[[Path], float],
) -> Path | None:
    """Choose best Firefox profile path from pure inputs."""
    if default_from_ini is not None and default_exists(default_from_ini):
        return default_from_ini

    dirs = list(profile_dirs)
    default_release_dirs = [
        path for path in dirs if path.name.endswith(".default-release")
    ]
    if default_release_dirs:
        return max(default_release_dirs, key=modified_time)

    fallback_dirs = [
        path for path in dirs if "default" in path.name.lower() or "." in path.name
    ]
    return sorted(fallback_dirs)[0] if fallback_dirs else None
