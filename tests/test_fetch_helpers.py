import pytest

from pzi import fetch_helpers
from pzi.fetch_helpers import _read_limited


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
        _read_limited(response, max_bytes=1_000_000)


def test_read_limited_accepts_body_matching_content_length() -> None:
    body = b"%PDF-1.4 complete"
    response = _LengthResponse(body, {"Content-Length": str(len(body))})
    assert _read_limited(response, max_bytes=1_000_000) == body


def test_read_limited_skips_reconciliation_without_content_length() -> None:
    body = b"%PDF-1.4 no length header"
    assert _read_limited(_LengthResponse(body, {}), max_bytes=1_000_000) == body


def test_read_limited_skips_reconciliation_for_encoded_bodies() -> None:
    """Content-Length counts encoded bytes, so it cannot be compared against
    the decoded length when a Content-Encoding is in play."""
    body = b"decoded-longer-than-encoded-length"
    response = _LengthResponse(
        body, {"Content-Length": "8", "Content-Encoding": "gzip"}
    )
    assert _read_limited(response, max_bytes=1_000_000) == body
