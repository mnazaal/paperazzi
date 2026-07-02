"""HTTP handler error-path tests (auth, CORS, body limits).

Tests the boundary-layer error handling in http_api.py without
requiring a running translation-server or real network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pzi.http_api import run_server, server_exposure_error
from pzi.http_post_routes import process_post_request
from pzi.http_security import (
    AUTH_HEADER,
    RateLimiter,
    build_http_security_config,
    request_security_error,
    validated_content_length,
)

# ══════════════════════════════════════════════════════════════════════════════
# capture_input_from_http_body edge cases
# ══════════════════════════════════════════════════════════════════════════════


def test_empty_body_rejected() -> None:
    status, body = process_post_request("/capture", {}, "/t/c.toml", "/t")
    assert status == 400
    assert "url required" in body["error"]


def test_body_not_dict_rejected() -> None:
    status, body = process_post_request("/capture", "bad", "/t/c.toml", "/t")
    assert status == 400
    assert "must be a JSON object" in body["error"]


def test_unknown_endpoint_404() -> None:
    status, body = process_post_request("/nonexistent", {}, "/t/c.toml", "/t")
    assert status == 404


# ══════════════════════════════════════════════════════════════════════════════
# CORS origin validation
# ══════════════════════════════════════════════════════════════════════════════


def test_non_local_origin_rejected() -> None:
    sec = build_http_security_config(allowed_origins=("http://127.0.0.1",))
    err = request_security_error(
        method="POST", headers={"Origin": "https://evil.example.com"}, security=sec,
    )
    assert err is not None
    assert err[0] == 403


def test_local_origin_allowed() -> None:
    sec = build_http_security_config(allowed_origins=("http://127.0.0.1", "http://localhost"))
    err = request_security_error(
        method="GET", headers={"Origin": "http://127.0.0.1"}, security=sec,
    )
    assert err is None


def test_extension_origin_allowed() -> None:
    sec = build_http_security_config(
        allowed_origins=("chrome-extension://", "moz-extension://"),
    )
    err = request_security_error(
        method="GET", headers={"Origin": "moz-extension://abc123"}, security=sec,
    )
    assert err is None


# ══════════════════════════════════════════════════════════════════════════════
# Auth token validation (pure security layer)
# ══════════════════════════════════════════════════════════════════════════════


def test_request_security_rejects_when_auth_required_and_missing() -> None:
    sec = build_http_security_config(auth_token="secret")
    err = request_security_error(
        method="POST", headers={}, security=sec,
    )
    assert err is not None
    assert err[0] == 401


def test_request_security_rejects_wrong_token() -> None:
    sec = build_http_security_config(auth_token="secret")
    err = request_security_error(
        method="POST", headers={AUTH_HEADER: "wrong"}, security=sec,
    )
    assert err is not None
    assert err[0] == 401


def test_request_security_passes_with_correct_token() -> None:
    sec = build_http_security_config(auth_token="secret")
    err = request_security_error(
        method="POST", headers={AUTH_HEADER: "secret"}, security=sec,
    )
    assert err is None


def test_request_security_passes_when_no_auth_configured() -> None:
    sec = build_http_security_config(auth_token=None)
    err = request_security_error(
        method="POST", headers={}, security=sec,
    )
    assert err is None


def test_server_exposure_guard_allows_loopback_without_token() -> None:
    sec = build_http_security_config(auth_token=None)

    assert server_exposure_error("127.0.0.1", sec) is None
    assert server_exposure_error("localhost", sec) is None


def test_server_exposure_guard_rejects_non_loopback_without_token() -> None:
    sec = build_http_security_config(auth_token=None)

    assert server_exposure_error("0.0.0.0", sec) == (
        "refusing to serve unauthenticated API on a non-loopback host; "
        "set api_auth_token or bind to 127.0.0.1/localhost"
    )


def test_server_exposure_guard_allows_non_loopback_with_token() -> None:
    sec = build_http_security_config(auth_token="secret")

    assert server_exposure_error("0.0.0.0", sec) is None


def test_run_server_rejects_non_loopback_without_token_before_binding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="refusing to serve unauthenticated API"):
        run_server(
            config_path=str(tmp_path / "config.toml"),
            home_dir=str(tmp_path),
            host="0.0.0.0",
            port=0,
            security=build_http_security_config(auth_token=None),
        )


def test_request_security_rejects_bad_origin() -> None:
    sec = build_http_security_config(auth_token=None, allowed_origins=("http://127.0.0.1",))
    err = request_security_error(
        method="POST", headers={"Origin": "https://evil.example.com"}, security=sec,
    )
    assert err is not None
    assert err[0] == 403


def test_request_security_passes_options_always() -> None:
    sec = build_http_security_config(auth_token="secret", allowed_origins=("http://127.0.0.1",))
    err = request_security_error(
        method="OPTIONS", headers={}, security=sec,
    )
    assert err is None


def test_validated_content_length_too_large() -> None:
    err = validated_content_length("1000000", max_body_bytes=10)
    assert isinstance(err, tuple)
    assert err[0] == 413


def test_validated_content_length_missing_is_zero() -> None:
    result = validated_content_length(None, max_body_bytes=1000)
    assert result == 0


def test_validated_content_length_valid() -> None:
    result = validated_content_length("5000", max_body_bytes=10000)
    assert result == 5000


# ══════════════════════════════════════════════════════════════════════════════
# Body size enforcement
# ══════════════════════════════════════════════════════════════════════════════


def test_content_length_enforced() -> None:
    err = validated_content_length(value="1000000", max_body_bytes=10)
    assert isinstance(err, tuple)
    assert err[0] == 413


def test_content_length_missing() -> None:
    result = validated_content_length(value=None, max_body_bytes=1000)
    assert result == 0


def test_content_length_valid() -> None:
    result = validated_content_length(value="5000", max_body_bytes=10000)
    assert result == 5000


# ══════════════════════════════════════════════════════════════════════════════
# Rate limiter
# ══════════════════════════════════════════════════════════════════════════════


def test_rate_limiter_allows_first_request() -> None:
    rl = RateLimiter(max_requests=5, window_seconds=60)
    allowed, remaining, _ = rl.check("client-1")
    assert allowed is True
    assert remaining == 4


def test_rate_limiter_blocks_after_exhaustion() -> None:
    rl = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        allowed, _, _ = rl.check("client-1")
        assert allowed is True
    allowed, remaining, _ = rl.check("client-1")
    assert allowed is False
    assert remaining == 0


def test_rate_limiter_separate_clients() -> None:
    rl = RateLimiter(max_requests=2, window_seconds=60)
    rl.check("client-1")
    rl.check("client-1")
    blocked, _, _ = rl.check("client-1")
    assert blocked is False
    allowed, _, _ = rl.check("client-2")
    assert allowed is True
