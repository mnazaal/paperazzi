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


class RateLimiter:
    """In-memory token-bucket rate limiter keyed by client identifier."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, client_id: str) -> tuple[bool, int, int]:
        """Return (allowed, remaining, reset_seconds)."""
        with self._lock:
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


def build_http_security_config(
    *,
    auth_token: str | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    rate_limit_rpm: int = 60,
) -> HttpSecurityConfig:
    """Normalize HTTP security knobs without touching request state."""
    origins = tuple(
        origin.strip()
        for origin in (allowed_origins or DEFAULT_ALLOWED_ORIGINS)
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


def origin_allowed(origin: str | None, allowed_origins: tuple[str, ...]) -> bool:
    """Return whether Origin is acceptable for local API access."""
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
        allowed_parts = urlsplit(normalized_allowed)
        value_parts = urlsplit(value)
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
    """Pure request gate: origin + optional bearer/header token."""
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
    if supplied is None or not hmac.compare_digest(supplied, token):
        return 401, "invalid API token"
    return None


def validated_content_length(value: str | None, *, max_body_bytes: int) -> int | tuple[int, str]:
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
