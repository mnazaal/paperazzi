"""Pure HTTP security helpers for the local capture API."""

from __future__ import annotations

import hmac
import ipaddress
import threading
import time
from typing import TypedDict
from urllib.parse import urlsplit

from pzi.url_safety import safe_public_http_url as _shared_safe_public_http_url

DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1",
    "http://localhost",
    "chrome-extension://",
    "moz-extension://",
)
DEFAULT_MAX_BODY_BYTES = 64 * 1024 * 1024
AUTH_HEADER = "X-Pzi-Token"
DNS_LOOKUP_TIMEOUT_SECONDS = 0.25


class HttpSecurityConfig(TypedDict):
    auth_token: str | None
    allowed_origins: tuple[str, ...]
    max_body_bytes: int
    rate_limit_rpm: int
    listen_host: str


class RateLimiter:
    """In-memory token-bucket rate limiter keyed by client identifier.

    Buckets are cleaned up lazily during ``check()`` calls to prevent
    unbounded growth under sustained traffic.
    """

    _CLEANUP_INTERVAL = 200  # sweep expired entries every N check() calls

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()
        self._call_count = 0

    def check(self, client_id: str) -> tuple[bool, int, int]:
        """Return (allowed, remaining, reset_seconds)."""
        with self._lock:
            self._call_count += 1
            if self._call_count % self._CLEANUP_INTERVAL == 0:
                self._sweep_expired()

            now = time.time()
            window_start, count = self._buckets.get(client_id, (0.0, 0))
            if now - window_start >= self._window:
                window_start = now
                count = 0
            if count >= self._max:
                reset = int(window_start + self._window - now) + 1
                return False, 0, reset
            count += 1
            self._buckets[client_id] = (window_start, count)
            return True, self._max - count, int(window_start + self._window - now)

    def _sweep_expired(self) -> None:
        """Remove bucket entries whose window has fully expired."""
        now = time.time()
        expired = [
            cid
            for cid, (ws, _count) in self._buckets.items()
            if now - ws >= self._window
        ]
        for cid in expired:
            del self._buckets[cid]


def build_http_security_config(
    *,
    auth_token: str | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    rate_limit_rpm: int = 60,
    listen_host: str = "127.0.0.1",
) -> HttpSecurityConfig:
    """Normalize HTTP security knobs without touching request state."""
    # `is None` rather than falsiness: an explicitly empty list means "allow no
    # cross-origin requests", and `or` silently turned that into the permissive
    # default set — including `chrome-extension://`.
    configured = DEFAULT_ALLOWED_ORIGINS if allowed_origins is None else allowed_origins
    origins = tuple(
        origin.strip()
        for origin in configured
        if isinstance(origin, str) and origin.strip()
    )
    normalized_token = (
        auth_token.strip()
        if isinstance(auth_token, str) and auth_token.strip()
        else None
    )
    return {
        "auth_token": normalized_token,
        "allowed_origins": origins,
        "max_body_bytes": max(0, int(max_body_bytes)),
        "rate_limit_rpm": max(1, int(rate_limit_rpm)),
        "listen_host": listen_host.strip() or "127.0.0.1",
    }


def loopback_bind_host(value: str | None) -> bool:
    """Return True when a server bind host is limited to the local machine."""
    if value is None:
        return False
    host = value.strip().lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def safe_public_http_url(value: str, *, dns_timeout: float = DNS_LOOKUP_TIMEOUT_SECONDS) -> bool:
    """Return True for public http(s) URLs, rejecting localhost/private networks."""
    return _shared_safe_public_http_url(value, dns_timeout=dns_timeout)


def _host_only(value: str) -> str | None:
    """Strip a port (and IPv6 brackets) from a Host-header-shaped string.

    ``None`` when the value is not host-shaped at all. ``urlsplit`` raises
    ``ValueError("Invalid IPv6 URL")`` on an unbalanced bracket, and this runs
    inside the request gate — *before* the token is checked — so the exception
    reached the server as an unauthenticated 500 that any caller could trigger
    with one header.
    """
    try:
        parsed = urlsplit(f"//{value}")
    except ValueError:
        return None
    return (parsed.hostname or value).lower()


def host_header_allowed(host: str | None, listen_host: str) -> bool:
    """Return whether a request's Host header matches the server's bind host.

    Guards against DNS rebinding: an attacker-controlled page can point its
    own domain's DNS at 127.0.0.1 and issue a plain-GET request that carries
    no Origin header (Origin is only sent for CORS-relevant requests), so the
    Origin check alone lets it through. A missing Host header is allowed
    through unchecked — real HTTP/1.1 clients (browsers) always send one, so
    this only affects hand-built requests without one, never the attack this
    guards against.

    A loopback bind (the default) accepts only a loopback Host. An explicit
    non-loopback bind accepts exactly that configured host — no separate
    override key, per the "no new escape hatch" design decision.
    """
    if host is None or not host.strip():
        return True
    request_host = _host_only(host.strip())
    if request_host is None:
        # Not a host at all. A browser never sends this, so refusing costs
        # nothing and the alternative — letting it through unchecked, as a
        # missing header is — would hand the guard a bypass.
        return False
    if loopback_bind_host(listen_host):
        return loopback_bind_host(request_host)
    return request_host == _host_only(listen_host)


def origin_allowed(origin: str | None, allowed_origins: tuple[str, ...]) -> bool:
    """Return whether Origin is acceptable for local API access.

    Never raises. Besides the gate itself, the 500 handler sends CORS headers —
    so an ``Origin`` that made this throw faulted the error path too, and the
    caller got zero bytes rather than a diagnostic.
    """
    if origin is None or not origin.strip():
        return True
    value = origin.strip().rstrip("/")
    for allowed in allowed_origins:
        normalized_allowed = allowed.strip().rstrip("/")
        if not normalized_allowed:
            continue
        if normalized_allowed in {"chrome-extension:", "moz-extension:"}:
            if value.startswith(normalized_allowed + "//"):
                return True
            continue
        if normalized_allowed in {"chrome-extension://", "moz-extension://"}:
            if value.startswith(normalized_allowed):
                return True
            continue
        if value == normalized_allowed:
            return True
        try:
            allowed_parts = urlsplit(normalized_allowed)
            value_parts = urlsplit(value)
        except ValueError:
            continue
        if (
            allowed_parts.scheme in {"chrome-extension", "moz-extension"}
            and value_parts.scheme == allowed_parts.scheme
            and value_parts.netloc == allowed_parts.netloc
        ):
            return True
    return False


def request_security_error(
    *, method: str, headers: dict[str, str], security: HttpSecurityConfig
) -> tuple[int, str] | None:
    """Pure request gate: host + origin + optional bearer/header token."""
    host = headers.get("Host") or headers.get("host")
    if not host_header_allowed(host, security["listen_host"]):
        return 403, "host not allowed"
    origin = headers.get("Origin") or headers.get("origin")
    if not origin_allowed(origin, security["allowed_origins"]):
        return 403, "origin not allowed"
    if method.upper() == "OPTIONS":
        return None
    token = security["auth_token"]
    if token is None:
        return None
    supplied = headers.get(AUTH_HEADER) or headers.get(AUTH_HEADER.lower())
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        supplied = auth.removeprefix("Bearer ")
    if supplied is None or not _tokens_match(supplied, token):
        return 401, "invalid API token"
    return None


def _tokens_match(supplied: str, token: str) -> bool:
    """Constant-time comparison that a non-ASCII candidate cannot crash.

    ``hmac.compare_digest`` raises ``TypeError`` on a str containing non-ASCII,
    so one header value turned an unauthenticated request into a 500. Comparing
    the UTF-8 bytes keeps the timing property and answers what was always the
    right answer: a token that is not the token is invalid, not an error.
    """
    return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))


def validated_content_length(
    value: str | None,
    *,
    max_body_bytes: int,
    transfer_encoding: str | None = None,
) -> int | tuple[int, str]:
    """Decide how many body bytes to read, or the error to answer with.

    A chunked request carries no ``Content-Length``, and reading zero bytes for
    one silently turned a real body into an empty one: the request was then
    processed as ``{}`` and answered 200. pzi's clients always send a length, so
    the honest answer is 411 rather than a wrong success.
    """
    if transfer_encoding and "chunked" in transfer_encoding.lower():
        return 411, "chunked request bodies are not supported; send Content-Length"
    if value is None or not value.strip():
        return 0
    try:
        length = int(value)
    except ValueError:
        return 400, "invalid Content-Length"
    if length < 0:
        return 400, "invalid Content-Length"
    if length > max_body_bytes:
        return 413, "request body too large"
    return length
