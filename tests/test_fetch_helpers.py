import pytest

from pzi import fetch_helpers
from pzi.fetch_helpers import read_limited


class _FakeResponse:
    headers = {"Content-Type": "application/pdf"}
    _body = b"%PDF-1.7\n"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body)
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


def test_fetch_binary_sends_browser_friendly_pdf_headers(monkeypatch) -> None:
    seen = {}

    def fake_urlopen(request, *, timeout, allow_host=None):
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        seen["allow_host"] = allow_host
        return _FakeResponse()

    monkeypatch.setattr(fetch_helpers, "safe_urlopen", fake_urlopen)

    data, content_type = fetch_helpers.fetch_binary("https://example.com/paper.pdf")

    assert data.startswith(b"%PDF-")
    assert content_type == "application/pdf"
    assert seen["headers"]["User-agent"] == fetch_helpers.DEFAULT_USER_AGENT
    assert seen["headers"]["Accept"] == "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"


class _LargeResponse:
    headers = {"Content-Type": "application/pdf"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        if size is None or size < 0:
            return b"x" * 1024
        return b"x" * size


def test_fetch_binary_rejects_response_over_max_bytes(monkeypatch) -> None:
    monkeypatch.setattr(fetch_helpers, "safe_urlopen", lambda *_args, **_kwargs: _LargeResponse())

    try:
        fetch_helpers.fetch_binary("https://example.com/huge.pdf", max_bytes=10)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert str(exc) == "response body exceeds maximum size: 10 bytes"


def test_fetch_text_rejects_response_over_max_bytes(monkeypatch) -> None:
    monkeypatch.setattr(fetch_helpers, "safe_urlopen", lambda *_args, **_kwargs: _LargeResponse())

    try:
        fetch_helpers.fetch_text("https://example.com/huge", max_bytes=10)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert str(exc) == "response body exceeds maximum size: 10 bytes"


# ---------------------------------------------------------------------------
# build_metadata_fetch_text
# ---------------------------------------------------------------------------


class _RecordingLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def wait(self, url: str) -> None:
        self.calls.append(url)


def test_build_metadata_fetch_text_rate_limits_and_passes_kwargs() -> None:
    seen: list[tuple[str, dict]] = []

    def inner(url, **kwargs):
        seen.append((url, kwargs))
        return "BODY"

    limiter = _RecordingLimiter()
    fetch = fetch_helpers.build_metadata_fetch_text({}, inner=inner, rate_limiter=limiter)

    assert fetch("http://x/api", user_agent="ua") == "BODY"
    assert seen == [("http://x/api", {"user_agent": "ua"})]
    assert limiter.calls == ["http://x/api"]


def test_build_metadata_fetch_text_cache_hit_short_circuits(tmp_path) -> None:
    from pzi.metadata_cache import MetadataCache

    calls = {"n": 0}

    def inner(url, **kwargs):
        calls["n"] += 1
        return "BODY"

    limiter = _RecordingLimiter()
    cache = MetadataCache(tmp_path, 60)
    fetch = fetch_helpers.build_metadata_fetch_text(
        {}, inner=inner, cache=cache, rate_limiter=limiter
    )

    assert fetch("http://x/api") == "BODY"  # miss: inner + cache.set
    assert fetch("http://x/api") == "BODY"  # hit: no inner, no rate gate
    assert calls["n"] == 1
    assert limiter.calls == ["http://x/api"]


def test_build_metadata_fetch_text_enables_cache_from_config(tmp_path) -> None:
    calls = {"n": 0}

    def inner(url, **kwargs):
        calls["n"] += 1
        return "BODY"

    limiter = _RecordingLimiter()
    config = {"metadata_cache_ttl": 60, "pzi_data_home": str(tmp_path)}
    fetch = fetch_helpers.build_metadata_fetch_text(config, inner=inner, rate_limiter=limiter)

    assert fetch("http://x/api") == "BODY"
    assert fetch("http://x/api") == "BODY"
    assert calls["n"] == 1  # second call served from the config-enabled cache


# --- truncated-response detection ------------------------------------------
# A body with a known Content-Length that is cut short does not raise from
# HTTPResponse.read(amt): it clips to the remaining length and returns short.
# Without reconciling against Content-Length the truncation is silent, and a
# half-downloaded PDF gets stored as the paper.


class _LengthResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = body
        self._pos = 0
        self.headers = headers

    def read(self, amt: int) -> bytes:
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def getheader(self, name: str, default=None):
        return self.headers.get(name, default)


def test_read_limited_rejects_body_shorter_than_content_length() -> None:
    response = _LengthResponse(b"%PDF-1.4 only-40-bytes-of-900", {"Content-Length": "900"})
    with pytest.raises(ValueError, match="truncated"):
        read_limited(response, max_bytes=1_000_000)


def test_read_limited_accepts_body_matching_content_length() -> None:
    body = b"%PDF-1.4 complete"
    response = _LengthResponse(body, {"Content-Length": str(len(body))})
    assert read_limited(response, max_bytes=1_000_000) == body


def test_read_limited_skips_reconciliation_without_content_length() -> None:
    body = b"%PDF-1.4 no length header"
    assert read_limited(_LengthResponse(body, {}), max_bytes=1_000_000) == body


def test_read_limited_skips_reconciliation_for_encoded_bodies() -> None:
    """Content-Length counts encoded bytes, so it cannot be compared against
    the decoded length when a Content-Encoding is in play."""
    body = b"decoded-longer-than-encoded-length"
    response = _LengthResponse(
        body, {"Content-Length": "8", "Content-Encoding": "gzip"}
    )
    assert read_limited(response, max_bytes=1_000_000) == body


def test_a_typeerror_inside_a_fetcher_is_not_swallowed() -> None:
    """A bug inside a provider must surface, not trigger a narrower retry.

    The old capability probe called the fetcher and caught `TypeError` to detect
    a narrower signature, so a genuine TypeError raised *inside* the fetcher was
    indistinguishable from one raised by the call itself — the provider was
    silently re-invoked with fewer arguments and its plausible-looking result
    used.
    """
    import pytest

    from pzi.add_planning import _call_metadata_fetcher

    calls: list[dict] = []

    def buggy_fetcher(doi: str, *, contact_email=None, errors=None):
        calls.append({"contact_email": contact_email})
        raise TypeError("bug inside the provider")

    with pytest.raises(TypeError, match="bug inside the provider"):
        _call_metadata_fetcher(buggy_fetcher, "10.1/x", contact_email="a@b.c", errors=[])

    assert len(calls) == 1, "fetcher must not be retried after an internal TypeError"


def test_accepts_keyword_detects_narrow_and_wide_seams() -> None:
    from pzi.protocols import accepts_keyword

    assert accepts_keyword(lambda url, *, user_agent=None: url, "user_agent")
    assert accepts_keyword(lambda url, **kwargs: url, "user_agent")
    assert not accepts_keyword(lambda url: url, "user_agent")


def test_retry_after_is_clamped_to_a_sane_ceiling() -> None:
    """`Retry-After` is remote input: honoring it verbatim stalls the command.

    A server answering `Retry-After: 86400` would park `pzi add` for a day
    inside one call.
    """
    from pzi.fetch_helpers import MAX_RETRY_AFTER, _retry_after_delay

    class _Exc:
        headers = {"Retry-After": "86400"}

    assert _retry_after_delay(_Exc(), 1) == MAX_RETRY_AFTER


def test_retry_after_honors_a_short_server_delay() -> None:
    from pzi.fetch_helpers import _retry_after_delay

    class _Exc:
        headers = {"Retry-After": "5"}

    assert _retry_after_delay(_Exc(), 1) == 5.0


def test_retry_after_never_returns_a_negative_delay() -> None:
    from pzi.fetch_helpers import _retry_after_delay

    class _Exc:
        headers = {"Retry-After": "-10"}

    assert _retry_after_delay(_Exc(), 1) == 0.0


def test_retry_after_falls_back_to_backoff_for_an_http_date() -> None:
    """The header also permits a date form, which is not parsed as a number."""
    from pzi.fetch_helpers import _retry_after_delay

    class _Exc:
        headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}

    assert _retry_after_delay(_Exc(), 1) == 2.0


# ── ProviderBreaker (moved here from check_service for item 576) ────────
#
# It lives in this module, not in `check_service` where it was written, because
# `update --promote` walks the same providers over the same library and needs the
# same guard — and `promote_planning` is core, so it cannot import a service.


def test_the_breaker_trips_only_after_consecutive_failures() -> None:
    from pzi.fetch_helpers import ProviderBreaker

    breaker = ProviderBreaker(threshold=3)
    breaker.record_failure("s2", "timeout")
    breaker.record_failure("s2", "timeout")
    assert not breaker.is_open("s2"), "two failures is not yet a dead provider"
    breaker.record_failure("s2", "timeout")
    assert breaker.is_open("s2")


def test_a_provider_that_recovers_is_never_tripped() -> None:
    """Consecutive, not cumulative — a flaky source keeps being asked."""
    from pzi.fetch_helpers import ProviderBreaker

    breaker = ProviderBreaker(threshold=3)
    for _ in range(10):
        breaker.record_failure("crossref", "timeout")
        breaker.record_failure("crossref", "timeout")
        breaker.record_answer("crossref")
    assert not breaker.is_open("crossref")


def test_a_tripped_provider_is_reported_once_for_the_run() -> None:
    from pzi.fetch_helpers import ProviderBreaker

    breaker = ProviderBreaker(threshold=2)
    for _ in range(50):
        breaker.record_failure("dblp", "connection refused")
    assert len(breaker.tripped) == 1
    assert "dblp" in breaker.tripped
    assert "not retried" in breaker.tripped["dblp"]
