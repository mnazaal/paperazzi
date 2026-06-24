"""Shared HTTP fetch utilities used across API client modules."""

from __future__ import annotations

import time
import urllib.error
from urllib.request import Request

from pzi.safe_http import SsrfBlocked, safe_urlopen

DEFAULT_USER_AGENT = "pzi/1.0 (mailto:pzi)"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024


def _is_ssrf_block(exc: BaseException) -> bool:
    """Detect an SSRF block, even after urllib wraps it in another URLError.

    ``URLError`` subclasses ``OSError``, so ``AbstractHTTPHandler.do_open``
    re-wraps a :class:`~pzi.safe_http.SsrfBlocked` raised at connect time inside
    a plain ``URLError(reason=SsrfBlocked)``.  Walk the ``reason`` chain so the
    block is treated as terminal rather than a retryable network error.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while isinstance(cur, BaseException) and id(cur) not in seen:
        if isinstance(cur, SsrfBlocked):
            return True
        seen.add(id(cur))
        reason = getattr(cur, "reason", None)
        cur = reason if isinstance(reason, BaseException) else None
    return False


def _retry_after_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Return sleep seconds from Retry-After header, falling back to exponential backoff."""
    raw = exc.headers.get("Retry-After") if hasattr(exc, "headers") else None
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return min(2**attempt, 8)


def _read_limited(response, *, max_bytes: int) -> bytes:
    """Read response body up to max_bytes, failing before unbounded memory growth."""
    chunks: list[bytes] = []
    total = 0
    limit = max(0, int(max_bytes))
    while True:
        chunk = response.read(min(READ_CHUNK_BYTES, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"response body exceeds maximum size: {limit} bytes")
        chunks.append(chunk)


def fetch_text(
    url: str,
    *,
    api_key: str | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> str:
    """Fetch a URL and return the response body decoded as UTF-8 text.

    Retries on transient network errors (URLError, TimeoutError) with
    exponential backoff.  Retries on HTTP 429 (Too Many Requests) using
    the Retry-After header when present.  Does NOT retry on other
    HTTPError (4xx/5xx status).
    """
    headers: dict[str, str] = {"User-Agent": user_agent}
    if api_key:
        headers["x-api-key"] = api_key

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = Request(url, headers=headers, method="GET")
            with safe_urlopen(request, timeout=timeout) as response:
                return _read_limited(response, max_bytes=max_bytes).decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                delay = _retry_after_delay(exc, attempt)
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if _is_ssrf_block(exc):
                raise  # terminal: re-attempting a blocked target is always blocked
            last_error = exc
            if attempt < max_retries:
                time.sleep(min(2**attempt, 8))  # 0s, 2s, 4s (capped at 8)

    raise last_error  # type: ignore[misc]


def fetch_binary(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    allow_host: str | None = None,
) -> tuple[bytes, str | None]:
    """Fetch a URL and return (raw_bytes, content_type).

    Retries on transient network errors with exponential backoff.
    Retries on HTTP 429 using Retry-After header.  Does NOT retry on
    other HTTPError (4xx/5xx status).  ``allow_host`` permits a single
    explicitly-trusted host (configured EZProxy) on a private IP.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = Request(url, headers=headers, method="GET")
            with safe_urlopen(request, timeout=timeout, allow_host=allow_host) as response:
                content_type = response.headers.get("Content-Type")
                return _read_limited(response, max_bytes=max_bytes), content_type
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                delay = _retry_after_delay(exc, attempt)
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if _is_ssrf_block(exc):
                raise  # terminal: re-attempting a blocked target is always blocked
            last_error = exc
            if attempt < max_retries:
                time.sleep(min(2**attempt, 8))  # pragma: no cover

    raise last_error  # type: ignore[misc]
