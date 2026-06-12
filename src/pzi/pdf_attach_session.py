"""Pure attach-session primitives for browser-acquired PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from collections.abc import Iterable
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AttachSession:
    request_id: str
    token: str
    citekey: str
    bib: str | None
    created_at: float
    expires_at: float
    max_bytes: int
    allowed_source_urls: tuple[str, ...]
    used: bool = False


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
        created_at=created_at,
        expires_at=created_at + max(0, ttl_seconds),
        max_bytes=max(0, max_bytes),
        allowed_source_urls=_unique_nonempty(allowed_source_urls),
        used=False,
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
) -> str | None:
    """Return validation error string, or None when request is allowed."""
    if session.used:
        return "attach session already used"
    if now > session.expires_at:
        return "attach session expired"
    if request_id != session.request_id:
        return "attach request_id mismatch"
    if not compare_digest(token, session.token):
        return "invalid attach token"
    if citekey != session.citekey:
        return "attach citekey mismatch"
    if bib != session.bib:
        return "attach bib mismatch"
    if len(pdf_bytes) > session.max_bytes:
        return "PDF payload too large"
    if not pdf_bytes.startswith(b"%PDF-"):
        return "PDF payload must start with %PDF-"
    if not _source_allowed(source_url, session.allowed_source_urls):
        return "source URL not allowed for attach session"
    return None


def mark_attach_session_used(session: AttachSession) -> AttachSession:
    """Return copy marked used."""
    return AttachSession(**{**session.__dict__, "used": True})


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
