from pzi import fetch_helpers


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
