"""Tests for the SSRF-hardened HTTP opener (redirect validation + IP pinning)."""

from __future__ import annotations

import email.message
import io
import urllib.error
import urllib.request

import pytest

from pzi import fetch_helpers, safe_http
from pzi.safe_http import SsrfBlocked, resolve_pinned_ip


def _addrinfo(*ips: str, port: int = 443):
    return [(2, 1, 6, "", (ip, port)) for ip in ips]


# === resolve_pinned_ip (DNS rebinding / IP pinning) ===


def test_resolve_pinned_ip_returns_public_address() -> None:
    ip = resolve_pinned_ip(
        "example.com", 443, getaddrinfo=lambda *a, **k: _addrinfo("93.184.216.34")
    )
    assert ip == "93.184.216.34"


@pytest.mark.parametrize(
    "addr",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "172.16.0.1", "::1"],
)
def test_resolve_pinned_ip_blocks_private_and_loopback(addr: str) -> None:
    with pytest.raises(SsrfBlocked):
        resolve_pinned_ip("evil.test", 443, getaddrinfo=lambda *a, **k: _addrinfo(addr))


def test_resolve_pinned_ip_blocks_when_any_address_is_private() -> None:
    # Rebinding defense: one public + one private must still be rejected.
    with pytest.raises(SsrfBlocked):
        resolve_pinned_ip(
            "evil.test", 443,
            getaddrinfo=lambda *a, **k: _addrinfo("93.184.216.34", "127.0.0.1"),
        )


def test_resolve_pinned_ip_blocks_empty_resolution() -> None:
    with pytest.raises(SsrfBlocked):
        resolve_pinned_ip("nowhere.test", 443, getaddrinfo=lambda *a, **k: [])


def test_resolve_pinned_ip_allows_trusted_host_on_private_ip() -> None:
    # A configured EZProxy host is trusted and may resolve to a private IP.
    ip = resolve_pinned_ip(
        "proxy.lib.university.edu", 443,
        getaddrinfo=lambda *a, **k: _addrinfo("10.0.0.5"),
        allow_host="proxy.lib.university.edu",
    )
    assert ip == "10.0.0.5"


def test_resolve_pinned_ip_allow_host_does_not_help_other_hosts() -> None:
    # The exception is scoped to the exact trusted host only.
    with pytest.raises(SsrfBlocked):
        resolve_pinned_ip(
            "evil.test", 443,
            getaddrinfo=lambda *a, **k: _addrinfo("10.0.0.5"),
            allow_host="proxy.lib.university.edu",
        )


def test_resolve_pinned_ip_propagates_dns_failure_as_oserror() -> None:
    def boom(*_a, **_k):
        raise OSError("temporary DNS failure")

    # DNS failure must stay retryable (OSError), not become a terminal SsrfBlocked.
    with pytest.raises(OSError) as exc:
        resolve_pinned_ip("flaky.test", 443, getaddrinfo=boom)
    assert not isinstance(exc.value, SsrfBlocked)


# === redirect re-validation ===


def _redirect(handler, newurl: str):
    req = urllib.request.Request("https://example.com/start")
    return handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", email.message.Message(), newurl
    )


def test_redirect_to_loopback_is_blocked() -> None:
    handler = safe_http._ValidatingRedirectHandler()
    with pytest.raises(SsrfBlocked):
        _redirect(handler, "http://127.0.0.1/admin")


def test_redirect_to_non_http_scheme_is_blocked() -> None:
    handler = safe_http._ValidatingRedirectHandler()
    with pytest.raises(SsrfBlocked):
        _redirect(handler, "file:///etc/passwd")


def test_redirect_to_public_url_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(safe_http, "classify_public_http_url", lambda _url, **_kw: "public")
    handler = safe_http._ValidatingRedirectHandler()
    result = _redirect(handler, "https://example.org/next")
    assert isinstance(result, urllib.request.Request)


def test_redirect_names_dns_timeout_distinctly(monkeypatch) -> None:
    # A DNS timeout on a redirect hop (e.g. a slow doi.org resolver) must not
    # read the same as "resolved to a non-public address" — the retry loop
    # and the operator both need to tell the two apart.
    monkeypatch.setattr(safe_http, "classify_public_http_url", lambda *a, **k: "dns-timeout")
    handler = safe_http._ValidatingRedirectHandler()
    with pytest.raises(SsrfBlocked, match="DNS lookup timed out for slow.example"):
        _redirect(handler, "https://slow.example/next")


def test_redirect_drops_api_key_when_host_changes(monkeypatch) -> None:
    monkeypatch.setattr(safe_http, "classify_public_http_url", lambda *a, **k: "public")
    handler = safe_http._ValidatingRedirectHandler()
    req = urllib.request.Request(
        "https://api.semanticscholar.org/start",
        headers={"x-api-key": "secret", "User-Agent": "pzi"},
    )
    result = handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", email.message.Message(),
        "https://other-host.example/next",
    )
    assert result is not None
    assert "X-api-key" not in result.headers
    assert result.headers.get("User-agent") == "pzi"


def test_redirect_keeps_api_key_on_same_host(monkeypatch) -> None:
    monkeypatch.setattr(safe_http, "classify_public_http_url", lambda *a, **k: "public")
    handler = safe_http._ValidatingRedirectHandler()
    req = urllib.request.Request(
        "https://api.semanticscholar.org/start",
        headers={"x-api-key": "secret"},
    )
    result = handler.redirect_request(
        req, io.BytesIO(b""), 302, "Found", email.message.Message(),
        "https://api.semanticscholar.org/next",
    )
    assert result is not None
    assert result.headers.get("X-api-key") == "secret"


# === SsrfBlocked is terminal (no retry) in fetch helpers ===


def test_fetch_text_does_not_retry_ssrf_block(monkeypatch) -> None:
    calls = {"n": 0}

    def blocked(_request, *, timeout):
        calls["n"] += 1
        raise SsrfBlocked("blocked non-public address")

    monkeypatch.setattr(fetch_helpers, "safe_urlopen", blocked)
    with pytest.raises(SsrfBlocked):
        fetch_helpers.fetch_text("https://example.com/x", max_retries=2)
    assert calls["n"] == 1  # blocked immediately, no retries


def test_fetch_text_does_not_retry_wrapped_ssrf_block(monkeypatch) -> None:
    # urllib's do_open re-wraps a connect-time SsrfBlocked in a plain URLError
    # (URLError is an OSError); the block must still be terminal, not retried.
    calls = {"n": 0}

    def blocked(_request, *, timeout):
        calls["n"] += 1
        raise urllib.error.URLError(SsrfBlocked("blocked non-public address"))

    monkeypatch.setattr(fetch_helpers, "safe_urlopen", blocked)
    with pytest.raises(urllib.error.URLError):
        fetch_helpers.fetch_text("https://example.com/x", max_retries=2)
    assert calls["n"] == 1  # detected through the reason chain, no retries


def test_ssrf_blocked_is_a_urlerror() -> None:
    assert issubclass(SsrfBlocked, urllib.error.URLError)
