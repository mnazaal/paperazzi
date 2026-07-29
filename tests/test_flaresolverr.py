"""Tests for FlareSolverr client and service integration."""

import json

from pzi.flaresolverr import fetch_html_via_flaresolverr, fetch_pdf_via_flaresolverr


def _make_post_json(response_html: str):
    def post_json(endpoint: str, payload: object) -> str:
        return json.dumps({"status": "ok", "solution": {"response": response_html}})
    return post_json


def _make_error_post_json(status: str = "error"):
    def post_json(endpoint: str, payload: object) -> str:
        return json.dumps({"status": status})
    return post_json


def test_fetch_html_via_flaresolverr_success():
    html = "<html><body>Hello</body></html>"
    result = fetch_html_via_flaresolverr(
        "https://example.com",
        server_url="http://127.0.0.1:8191",
        post_json=_make_post_json(html),
    )
    assert result == html


def test_fetch_html_via_flaresolverr_rejects_non_http_endpoint():
    calls: list[str] = []

    def spy_post(endpoint: str, payload: object) -> str:
        calls.append(endpoint)
        return json.dumps({"status": "ok", "solution": {"response": "x"}})

    result = fetch_html_via_flaresolverr(
        "https://example.com",
        server_url="file:///etc/passwd",
        post_json=spy_post,
    )
    assert result is None
    assert calls == []  # guarded before any request is issued


def test_fetch_html_via_flaresolverr_error_status():
    result = fetch_html_via_flaresolverr(
        "https://example.com",
        server_url="http://127.0.0.1:8191",
        post_json=_make_error_post_json(),
    )
    assert result is None


def test_fetch_html_via_flaresolverr_exception():
    def bad_post(endpoint: str, payload: object) -> str:
        raise ConnectionError("refused")

    result = fetch_html_via_flaresolverr(
        "https://example.com",
        server_url="http://127.0.0.1:8191",
        post_json=bad_post,
    )
    assert result is None


def test_fetch_html_via_flaresolverr_builds_endpoint():
    captured = {}

    def capturing_post(endpoint: str, payload: object) -> str:
        captured["endpoint"] = endpoint
        return json.dumps({"status": "ok", "solution": {"response": ""}})

    fetch_html_via_flaresolverr(
        "https://example.com",
        server_url="http://127.0.0.1:8191/",
        post_json=capturing_post,
    )
    assert captured["endpoint"] == "http://127.0.0.1:8191/v1"


def test_fetch_pdf_via_flaresolverr_returns_pdf_bytes(monkeypatch) -> None:
    """The success path, actually exercised.

    This test previously asserted `result is None` while its name and comments
    claimed to check PDF parsing. It passed only because conftest's autouse
    socket guard blocks non-loopback connects and the resulting error is
    swallowed as an OSError — so it proved nothing about the code under test and
    would have kept passing if the parsing broke entirely.
    """
    import json

    import pzi.safe_http
    from pzi.flaresolverr import fetch_pdf_via_flaresolverr

    def fake_post(endpoint: str, payload: object) -> str:
        return json.dumps({
            "status": "ok",
            "solution": {
                "url": "https://example.com/paper.pdf",
                "status": 200,
                "headers": {"content-type": "application/pdf"},
                "response": "",
                "cookies": [
                    {
                        "name": "cf_clearance",
                        "value": "test_cookie_value",
                        "domain": ".example.com",
                        "path": "/",
                        "expiry": None,
                        "httpOnly": True,
                        "secure": True,
                    }
                ],
                "userAgent": "Mozilla/5.0",
            }
        })

    seen: dict[str, object] = {}

    def fake_build_safe_opener(*, extra_handlers):
        seen["jar"] = extra_handlers[0].cookiejar
        return _FakeOpener(b"%PDF-1.4 solved")

    monkeypatch.setattr(pzi.safe_http, "build_safe_opener", fake_build_safe_opener)

    result = fetch_pdf_via_flaresolverr(
        "https://example.com/paper.pdf",
        server_url="http://127.0.0.1:8191",
        post_json=fake_post,
    )

    assert result == b"%PDF-1.4 solved"
    # The clearance cookie FlareSolverr returned is what makes the retry work,
    # so it must reach the opener's jar.
    jar = seen["jar"]
    assert [c.name for c in jar] == ["cf_clearance"]


def test_fetch_pdf_via_flaresolverr_returns_none_on_error() -> None:
    import json

    from pzi.flaresolverr import fetch_pdf_via_flaresolverr

    def fake_post(endpoint: str, payload: object) -> str:
        return json.dumps({"status": "error", "message": "failed"})

    result = fetch_pdf_via_flaresolverr(
        "https://example.com/paper.pdf",
        server_url="http://127.0.0.1:8191",
        post_json=fake_post,
    )
    assert result is None


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._chunks = [payload]

    def read(self, _n: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeOpener:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def open(self, _request, timeout=None):
        return _FakeResponse(self._payload)


def test_download_with_cookies_skips_malformed_cookies(monkeypatch) -> None:
    """A cookie we cannot read is one to skip, not to crash on.

    `name` and `value` were the only fields read with `[]`, so one malformed
    cookie raised KeyError out of the whole PDF fallback chain — skipping the
    desktop fallback that runs after this step. `"domain": null` raised
    AttributeError for the same reason, and a non-dict entry raised TypeError.
    """
    import pzi.safe_http
    from pzi.flaresolverr import _download_with_cookies

    captured: dict[str, object] = {}

    def fake_build_safe_opener(*, extra_handlers):
        captured["jar"] = extra_handlers[0].cookiejar
        return _FakeOpener(b"%PDF-1.4 ok")

    monkeypatch.setattr(pzi.safe_http, "build_safe_opener", fake_build_safe_opener)

    result = _download_with_cookies(
        "https://example.com/paper.pdf",
        [
            {"name": "good", "value": "v1", "domain": ".example.com"},
            {"name": "missing_value"},
            {"value": "missing_name"},
            "not-a-dict",
            {"name": "null_domain", "value": "v2", "domain": None},
        ],
        "UA/1.0",
    )

    assert result == b"%PDF-1.4 ok"
    # The readable cookies survived; the malformed ones were dropped.
    assert sorted(c.name for c in captured["jar"]) == ["good", "null_domain"]


def test_fetch_pdf_via_flaresolverr_returns_none_on_malformed_cookies() -> None:
    """The boundary must absorb a response shape this code does not anticipate."""
    def post_json(_endpoint: str, _payload: dict) -> str:
        return json.dumps({
            "status": "ok",
            "solution": {"cookies": [{"no_name_key": "x"}], "userAgent": "UA"},
        })

    assert fetch_pdf_via_flaresolverr(
        "https://example.com/paper.pdf",
        server_url="http://127.0.0.1:8191",
        post_json=post_json,
    ) is None


def test_fetch_pdf_via_flaresolverr_returns_none_when_cookies_is_not_a_list() -> None:
    def post_json(_endpoint: str, _payload: dict) -> str:
        return json.dumps({
            "status": "ok",
            "solution": {"cookies": {"name": "x"}, "userAgent": "UA"},
        })

    assert fetch_pdf_via_flaresolverr(
        "https://example.com/paper.pdf",
        server_url="http://127.0.0.1:8191",
        post_json=post_json,
    ) is None
