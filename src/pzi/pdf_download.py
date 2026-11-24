#!/usr/bin/env python3
"""PDF download and local-source storage helpers."""

from __future__ import annotations

import errno
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
from pzi.fileio import fsync_parent_dir, write_all
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


def normalize_local_path(value: str) -> str:
    """The filesystem path a literal local-PDF argument names.

    The one place `~` is expanded for local input. The shell does not expand a
    quoted `~/x.pdf`, a `--from-file` line or an API value, and only the
    validator used to, so extraction and the copy opened the literal string.
    Idempotent: callers that receive an already-normalized path may call it again.
    """
    return str(Path(value.strip()).expanduser())


def copy_pdf_to_papers_dir(
    *,
    source_path: str,
    papers_dir: str,
    citekey: str,
    record: PdfRecord | None = None,
    filename_format: str | None = None,
) -> tuple[str | None, str | None]:
    """Copy a local PDF into the papers directory with citekey naming."""
    source_path = normalize_local_path(source_path)
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

    if not is_pdf_bytes(data):
        # No `pragma: no cover` here. It carried one claiming integration/browser
        # coverage, which was false — a plain unit call reaches this line — and
        # `pyproject.toml` excludes pragmas from the coverage gate, so the one
        # check that stops an HTML paywall page being stored as a paper's PDF
        # was exempt from the gate that would have noticed if it stopped working.
        return None, f"downloaded content from {url} is not a PDF"


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


def _holds(path: Path, data: bytes) -> bool:
    """Whether *path* already holds exactly *data*.

    Not ``exists()``: ``resolve_pdf_destination`` answered "free" a moment ago,
    and another writer may have stored a different PDF at that name since.
    Accepting the name then returns the other paper's bytes as ours.
    """
    try:
        return path.read_bytes() == data
    except OSError:
        return False


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
    if _holds(destination, data):
        return str(destination)

    # Bounded: every iteration must either return or move to a *new* name, and
    # an unbounded retry loop with no progress guarantee has no business on a
    # write path — that is exactly how the dangling-symlink case span forever.
    for _attempt in range(_MAX_PDF_COLLISION_SUFFIX):
        temp_fd, temp_name = tempfile.mkstemp(
            dir=str(destination.parent), prefix=".pdf-", suffix=".tmp"
        )
        # Bound before the write, not after: a write that raised (ENOSPC, EIO)
        # skipped the cleanup below entirely, leaking one `.pdf-*.tmp` into the
        # user's papers directory per failure.
        temp_path = Path(temp_name)
        try:
            try:
                os.fchmod(temp_fd, 0o600)
                write_all(temp_fd, data)
                # Mirror the bib writer (bib_repository._write_bib_with_backup):
                # flush content to disk before the link/replace makes it visible,
                # so a crash right after can't leave a zero-byte or truncated PDF
                # at a name entries already reference.
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)
            try:
                _link_or_replace(temp_path, destination)
                fsync_parent_dir(destination)
                return str(destination)
            except FileExistsError:
                try:
                    if destination.read_bytes() == data:
                        return str(destination)
                except OSError:
                    pass
                destination = resolve_pdf_destination(destination, data)
                if _holds(destination, data):
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


#: `os.link` failures that mean "this filesystem has no hardlinks", as opposed
#: to "that name is taken". exFAT and FAT have none at all; many CIFS mounts and
#: some FUSE filesystems refuse them too.
_NO_HARDLINK_ERRNOS = frozenset(
    code
    for code in (
        getattr(errno, name, None)
        for name in ("EPERM", "EOPNOTSUPP", "ENOTSUP", "ENOSYS", "EXDEV", "EMLINK")
    )
    if code is not None
)


def _link_or_replace(temp_path: Path, destination: Path) -> None:
    """Put *temp_path* at *destination*, refusing to overwrite an existing file.

    ``os.link`` is the primitive that gives "create if absent" atomically, which
    is what stops one paper's PDF replacing another's. Where the filesystem has
    no hardlinks it raises rather than answering the question, and only
    ``FileExistsError`` used to be handled — so on an exFAT or CIFS
    ``papers_dir``, ``pzi add`` could not store a PDF at all.

    The fallback claims the name with ``O_CREAT | O_EXCL``, which is atomic on
    every filesystem, and only then renames the finished temp file over its own
    placeholder. A check-then-rename let two writers both see a free name, and
    the second silently replaced the first writer's different PDF.
    """
    try:
        os.link(temp_path, destination)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _NO_HARDLINK_ERRNOS:
            raise
    claim = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(claim)
    try:
        os.replace(temp_path, destination)
    except BaseException:
        # Do not leave an empty placeholder squatting on the name.
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise


