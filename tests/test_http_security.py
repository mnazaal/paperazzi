from pzi.http_security import (
    AUTH_HEADER,
    RateLimiter,
    build_http_security_config,
    origin_allowed,
    request_security_error,
    validated_content_length,
)


def test_build_http_security_config_strips_token_and_origins() -> None:
    security = build_http_security_config(
        auth_token="  secret  ",
        allowed_origins=[" http://localhost/ ", "", "  "],
        max_body_bytes=-1,
        rate_limit_rpm=0,
    )

    assert security == {
        "auth_token": "secret",
        "allowed_origins": ("http://localhost/",),
        "max_body_bytes": 0,
        "rate_limit_rpm": 1,
    }


def test_origin_allowed_accepts_extension_prefixes() -> None:
    assert origin_allowed("chrome-extension://abc123", ("chrome-extension://",))
    assert origin_allowed("moz-extension://abc123", ("moz-extension:",))
    assert not origin_allowed("http://evil.example", ("http://localhost",))


def test_request_security_error_allows_extension_origin_when_no_token_configured() -> None:
    security = build_http_security_config(auth_token=None)

    assert request_security_error(
        method="GET",
        headers={"Origin": "chrome-extension://abc123"},
        security=security,
    ) is None


def test_request_security_error_accepts_header_or_bearer_token() -> None:
    security = build_http_security_config(auth_token="secret")

    assert request_security_error(
        method="POST",
        headers={AUTH_HEADER: "secret"},
        security=security,
    ) is None
    assert request_security_error(
        method="POST",
        headers={"Authorization": "Bearer secret"},
        security=security,
    ) is None


def test_validated_content_length_bounds_body_size() -> None:
    assert validated_content_length(None, max_body_bytes=5) == 0
    assert validated_content_length("5", max_body_bytes=5) == 5
    assert validated_content_length("6", max_body_bytes=5) == (413, "request body too large")
    assert validated_content_length("bad", max_body_bytes=5) == (400, "invalid Content-Length")


def test_rate_limiter_tracks_remaining_and_reset() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60)

    assert limiter.check("client")[:2] == (True, 1)
    assert limiter.check("client")[:2] == (True, 0)
    assert limiter.check("client")[:2] == (False, 0)
