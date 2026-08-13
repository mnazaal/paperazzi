"""Shared HTTP fetch utilities used across API client modules."""

from __future__ import annotations

import functools
import json
import time
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar
from urllib.request import Request

from pzi.metadata_cache import MetadataCache
from pzi.rate_limit import RateLimiter
from pzi.safe_http import SsrfBlocked, safe_urlopen

_T = TypeVar("_T")

DEFAULT_USER_AGENT = "pzi/1.0 (mailto:pzi)"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
#: Ceiling on a server-supplied ``Retry-After``. Long enough to respect a real
#: rate-limit window, short enough that no response can stall a command.
MAX_RETRY_AFTER = 30.0
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
    """Return sleep seconds from Retry-After header, falling back to exponential backoff.

    The header is remote input, so it is clamped: honoring it verbatim let a
    server — hostile, misconfigured, or simply sending ``Retry-After: 86400`` —
    park ``pzi add`` for a day inside a single call. A negative or absurd value
    is not a reason to wait, and past ``MAX_RETRY_AFTER`` seconds the retry is
    worth abandoning anyway.
    """
    raw = exc.headers.get("Retry-After") if hasattr(exc, "headers") else None
    if raw is not None:
        try:
            return max(0.0, min(float(raw), MAX_RETRY_AFTER))
        except (ValueError, TypeError):
            # Retry-After also permits an HTTP-date, which this does not parse;
            # fall through to backoff rather than guess at it.
            pass
    return min(2**attempt, 8)


def _expected_content_length(response) -> int | None:
    """Return the body length the server promised, when it is comparable.

    ``Content-Length`` counts *encoded* bytes, so it cannot be compared against
    what we hold once a ``Content-Encoding`` is in play.
    """
    getheader = getattr(response, "getheader", None)
    if getheader is None or getheader("Content-Encoding"):
        return None
    raw = getheader("Content-Length")
    if raw is None or not str(raw).strip().isdigit():
        return None
    return int(str(raw).strip())


def _read_limited(response, *, max_bytes: int) -> bytes:
    """Read response body up to max_bytes, failing before unbounded memory growth.

    Also reconciles what was read against ``Content-Length``: for a body with a
    known length, ``HTTPResponse.read(amt)`` clips to the bytes remaining and
    returns *short* rather than raising, so a connection cut mid-body is
    otherwise completely silent — and a half-downloaded PDF gets written to the
    library as the paper. (Chunked bodies raise ``IncompleteRead`` instead, which
    callers translate into a download error.)
    """
    chunks: list[bytes] = []
    total = 0
    limit = max(0, int(max_bytes))
    while True:
        chunk = response.read(min(READ_CHUNK_BYTES, limit - total + 1))
        if not chunk:
            expected = _expected_content_length(response)
            if expected is not None and total < expected:
                raise ValueError(
                    f"truncated response body: got {total} of {expected} bytes"
                )
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise ValueError(f"response body exceeds maximum size: {limit} bytes")
        chunks.append(chunk)


def _fetch_with_retries(
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    max_retries: int,
    allow_host: str | None = None,
    extract: Callable[[Any], _T],
) -> _T:
    """Run one GET with the shared retry policy and hand the response to *extract*.

    The policy is: retry transient network errors with exponential backoff
    (0s, 2s, 4s, capped at 8); retry HTTP 429 honouring Retry-After; do not
    retry any other HTTPError; treat an SSRF block as terminal, since
    re-attempting a blocked target is always blocked.

    *extract* receives the **live response**, not its bytes, because
    :func:`fetch_binary` needs the Content-Type header as well as the body.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = Request(url, headers=headers, method="GET")
            # Passed only when set, so the text path's call is byte-identical to
            # what it was before this helper existed -- allow_host is a
            # binary-fetch concept (the configured EZProxy host).
            extra = {} if allow_host is None else {"allow_host": allow_host}
            with safe_urlopen(request, timeout=timeout, **extra) as response:
                return extract(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < max_retries:
                time.sleep(_retry_after_delay(exc, attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if _is_ssrf_block(exc):
                raise
            last_error = exc
            if attempt < max_retries:
                time.sleep(min(2**attempt, 8))

    raise last_error  # type: ignore[misc]


#: The only host `api_key` belongs to. It is the Semantic Scholar key, and one
#: shared fetcher is handed to every provider — so without this gate the same
#: `x-api-key` header went to Crossref, OpenAlex, DBLP and OpenReview, handing
#: a credential to four services that never asked for one and cannot use it.
_API_KEY_HOSTS = frozenset({"api.semanticscholar.org"})


def _is_api_key_host(url: str) -> bool:
    return (urllib.parse.urlsplit(url).hostname or "").lower() in _API_KEY_HOSTS


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
    if api_key and _is_api_key_host(url):
        headers["x-api-key"] = api_key

    return _fetch_with_retries(
        url,
        headers=headers,
        timeout=timeout,
        max_retries=max_retries,
        extract=lambda response: _read_limited(response, max_bytes=max_bytes).decode("utf-8"),
    )


#: Bodies a provider returns with HTTP 200 that are refusals, not results.
#: Semantic Scholar answers a quota refusal this way, so the status code cannot
#: be the whole cache-worthiness test.
_TRANSIENT_ERROR_MARKERS = (
    "rate limit",
    "too many requests",
    "quota",
    "temporarily unavailable",
)


def _is_transient_error_body(text: str) -> bool:
    """True when a 200 body is a provider refusal rather than a record.

    Deliberately narrow: it only fires on a small JSON object whose sole
    meaningful key is `error`/`message`, so a paper whose *abstract* discusses
    rate limiting is not mistaken for a refusal.
    """
    stripped = text.strip()
    if not stripped.startswith("{") or len(stripped) > 512:
        return False
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    message = parsed.get("error") or parsed.get("message")
    if not isinstance(message, str):
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _TRANSIENT_ERROR_MARKERS)


def build_metadata_fetch_text(
    config: Mapping[str, Any],
    *,
    api_key: str | None = None,
    inner: Callable[..., str] | None = None,
    cache: MetadataCache | None = None,
    rate_limiter: RateLimiter | None = None,
) -> Callable[..., str]:
    """Compose ``fetch_text`` with opt-in disk caching and per-host rate limiting.

    Returns a ``FetchText``-shaped callable: ``fetch(url, **kwargs)`` where the
    keyword args (e.g. ``user_agent``) pass through to the underlying fetch.  A
    cache hit short-circuits both the network call and the rate gate; misses are
    spaced per host and then cached.  Caching is active only when
    ``metadata_cache_ttl`` > 0; the rate limiter always applies.

    ``inner`` / ``cache`` / ``rate_limiter`` are injectable for tests.
    """
    base = inner or functools.partial(fetch_text, api_key=api_key)
    if cache is None:
        ttl = int(config.get("metadata_cache_ttl", 0) or 0)
        if ttl > 0:
            cache_dir = Path(str(config.get("pzi_data_home", "."))) / "metadata-cache"
            cache = MetadataCache(cache_dir, ttl)
    limiter = rate_limiter if rate_limiter is not None else RateLimiter()

    def fetch(url: str, **kwargs: Any) -> str:
        # Everything other than the URL that changes the response: the bound
        # API key, and the polite-pool identity the caller passes per request.
        # Keyed on the URL alone, an anonymous answer and an authenticated one
        # shared a cache entry.
        scope = f"{api_key or ''}\0{kwargs.get('user_agent') or ''}"
        if cache is not None:
            hit = cache.get(url, scope)
            if hit is not None:
                return hit
        limiter.wait(url)
        text = base(url, **kwargs)
        # Semantic Scholar reports a quota refusal as HTTP *200* with an
        # `error` body, so caching every 200 froze a transient rate-limit into
        # a permanent one: three lookups, one network call, the same
        # "rate limit exceeded" returned for the whole TTL. A failure is not a
        # result and must not be stored.
        if cache is not None and not _is_transient_error_body(text):
            cache.set(url, text, scope)
        return text

    return fetch


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
    return _fetch_with_retries(
        url,
        headers=headers,
        timeout=timeout,
        max_retries=max_retries,
        allow_host=allow_host,
        # Content-Type is read off the live response, before the body, which is
        # why `extract` takes the response rather than bytes.
        extract=lambda response: (
            _read_limited(response, max_bytes=max_bytes),
            response.headers.get("Content-Type"),
        ),
    )
