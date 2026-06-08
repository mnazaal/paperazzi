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

    def fake_urlopen(request, *, timeout):
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(fetch_helpers, "urlopen", fake_urlopen)

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
    monkeypatch.setattr(fetch_helpers, "urlopen", lambda *_args, **_kwargs: _LargeResponse())

    try:
        fetch_helpers.fetch_binary("https://example.com/huge.pdf", max_bytes=10)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert str(exc) == "response body exceeds maximum size: 10 bytes"


def test_fetch_text_rejects_response_over_max_bytes(monkeypatch) -> None:
    monkeypatch.setattr(fetch_helpers, "urlopen", lambda *_args, **_kwargs: _LargeResponse())

    try:
        fetch_helpers.fetch_text("https://example.com/huge", max_bytes=10)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert str(exc) == "response body exceeds maximum size: 10 bytes"
