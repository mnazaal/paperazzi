"""Pure attach-session primitives for browser-acquired PDFs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from pzi.token_compare import tokens_match


@dataclass(frozen=True)
class AttachSession:
    request_id: str
    token: str
    citekey: str
    bib: str | None
    expires_at: float
    max_bytes: int
    allowed_source_urls: tuple[str, ...]


def build_attach_session(
    *,
    request_id: str,
    token: str,
    citekey: str,
    bib: str | None,
    created_at: float,
    ttl_seconds: int,
    max_bytes: int,
    allowed_source_urls: Iterable[str],
) -> AttachSession:
    """Build immutable attach-session record from caller-supplied entropy/time."""
    return AttachSession(
        request_id=request_id,
        token=token,
        citekey=citekey,
        bib=bib,
        expires_at=created_at + max(0, ttl_seconds),
        max_bytes=max(0, max_bytes),
        allowed_source_urls=_unique_nonempty(allowed_source_urls),
    )


def validate_attach_request(
    session: AttachSession,
    *,
    request_id: str,
    token: str,
    citekey: str,
    bib: str | None,
    pdf_bytes: bytes,
    source_url: str | None,
    now: float,
    origin_candidate: str | None = None,
) -> str | None:
    """Return validation error string, or None when request is allowed.

    ``origin_candidate`` is the planned candidate the caller began from, when
    the bytes ended up somewhere else. Acquisition legitimately leaves the
    plan: a navigate-monitor or discover-from-page run follows the publisher's
    own redirect to a CDN, and the observed URL is on another host by design.
    Authorising on the observed URL alone refused those attaches, so a capture
    kept its metadata and silently lost the PDF.

    The plan is still what authorises — the claim must name a URL the plan
    contained — while ``source_url`` records where the bytes actually came
    from. This is defence in depth rather than a boundary: a caller holding the
    attach token knows the planned candidates. The gates that matter are the
    token, the request id, the citekey and bib match, the byte limit and the
    ``%PDF-`` magic; this one keeps out bytes from somewhere nobody planned.
    """
    if now > session.expires_at:
        return "attach session expired"
    if request_id != session.request_id:
        return "attach request_id mismatch"
    # `tokens_match`, not `compare_digest` directly: the latter raises
    # `TypeError` on a str containing non-ASCII, and the route has already
    # `claim()`ed the session by this point — so one accented character was a
    # 500 that skipped the `restore()` and destroyed a retryable session.
    if not tokens_match(token, session.token):
        return "invalid attach token"
    if citekey != session.citekey:
        return "attach citekey mismatch"
    if bib != session.bib:
        return "attach bib mismatch"
    if len(pdf_bytes) > session.max_bytes:
        return "PDF payload too large"
    if not pdf_bytes.startswith(b"%PDF-"):
        return "PDF payload must start with %PDF-"
    # The observed URL authorises when it is itself planned; otherwise the
    # caller must name the planned candidate it started from. Checking the
    # observed URL first keeps every existing caller's behaviour unchanged.
    if not _source_allowed(source_url, session.allowed_source_urls) and not _source_allowed(
        origin_candidate, session.allowed_source_urls
    ):
        return "source URL not allowed for attach session"
    return None


def _source_allowed(source_url: str | None, allowed_source_urls: tuple[str, ...]) -> bool:
    if not allowed_source_urls:
        return True
    if source_url is None:
        return False
    if source_url in allowed_source_urls:
        return True
    source_origin = _origin(source_url)
    return source_origin is not None and any(
        _origin(allowed_url) == source_origin for allowed_url in allowed_source_urls
    )


def _origin(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def _unique_nonempty(urls: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean = url.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return tuple(result)
