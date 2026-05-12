"""Edge tests for http_api.py uncovered lines (122-123, 180, 198, 338-343)."""

from io import BytesIO

from pzi.http_api import (
    _capture_payload,
    _handle_get,
    _handle_options,
    _handle_post,
    _health_payload,
    build_http_security_config,
    origin_allowed,
    process_get_request,
    process_post_request,
    request_security_error,
    validated_content_length,
)


class FakeRequestHandler:
    """Minimal fake BaseHTTPRequestHandler for testing handler functions."""

    def __init__(self, path: str = "/", headers: dict | None = None, body: bytes = b""):
        self.path = path
        self._headers = headers or {}
        self.rfile = BytesIO(body)
        self.responses: list[tuple[int, dict]] = []

    def send_response(self, status: int) -> None:
        self._status = status

    def send_header(self, key: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass

    @property
    def wfile(self) -> BytesIO:
        self._wfile = BytesIO()
        return self._wfile

    @wfile.setter
    def wfile(self, val) -> None:
        self._wfile = val

    def headers(self) -> dict:
        return self._headers

    def __iter__(self):
        return iter(self._headers.items())


# A more complete fake for the handler methods


class FakeHandler:
    def __init__(self, path: str = "/", headers: dict | None = None, body: bytes = b""):
        self.path = path
        self._hdrs = headers or {}
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self._status = None
        self._sent_headers: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self._status = status

    def send_header(self, key: str, value: str) -> None:
        self._sent_headers.append((key, value))

    def end_headers(self) -> None:
        pass

    @property
    def headers(self) -> dict:
        return self._hdrs

    def get_all(self, key: str) -> str | None:
        return self._hdrs.get(key)


# ── origin_allowed ───────────────────────────────────────────────

def test_origin_allowed_none() -> None:
    """None origin is always allowed."""
    assert origin_allowed(None, ("http://localhost",)) is True


def test_origin_allowed_whitespace() -> None:
    """Whitespace-only origin is allowed."""
    assert origin_allowed("  ", ("http://localhost",)) is True


def test_origin_allowed_exact_match() -> None:
    assert origin_allowed("http://localhost", ("http://localhost", "chrome-extension://")) is True


def test_origin_allowed_prefix_match() -> None:
    """chrome-extension:// prefix matches any extension ID."""
    assert origin_allowed("chrome-extension://abc123", ("chrome-extension://",)) is True


def test_origin_allowed_no_match() -> None:
    assert origin_allowed("http://evil.com", ("http://localhost",)) is False


# ── request_security_error ───────────────────────────────────────

def test_request_security_error_origin_blocked() -> None:
    sec = build_http_security_config(allowed_origins=["http://localhost"])
    result = request_security_error(
        method="GET",
        headers={"Origin": "http://evil.com"},
        security=sec,
    )
    assert result == (403, "origin not allowed")


def test_request_security_error_options_bypasses_token() -> None:
    """OPTIONS requests skip token check even with bad origin."""
    sec = build_http_security_config(auth_token="secret")
    result = request_security_error(
        method="OPTIONS",
        headers={"Origin": "http://localhost"},
        security=sec,
    )
    assert result is None  # origin allowed + OPTIONS skips token


def test_request_security_error_no_token_required() -> None:
    """No auth_token set → no token check."""
    sec = build_http_security_config(auth_token=None)
    result = request_security_error(
        method="GET",
        headers={"Origin": "http://localhost"},
        security=sec,
    )
    assert result is None


def test_request_security_error_missing_token() -> None:
    """Token required but not supplied → 401."""
    sec = build_http_security_config(auth_token="secret")
    result = request_security_error(
        method="GET",
        headers={"Origin": "http://localhost"},
        security=sec,
    )
    assert result == (401, "invalid API token")


def test_request_security_error_wrong_token() -> None:
    sec = build_http_security_config(auth_token="secret")
    result = request_security_error(
        method="GET",
        headers={"Origin": "http://localhost", "X-Pzi-Token": "wrong"},
        security=sec,
    )
    assert result == (401, "invalid API token")


def test_request_security_error_valid_token_header() -> None:
    sec = build_http_security_config(auth_token="secret")
    result = request_security_error(
        method="GET",
        headers={"Origin": "http://localhost", "X-Pzi-Token": "secret"},
        security=sec,
    )
    assert result is None


def test_request_security_error_bearer_token() -> None:
    """Authorization: Bearer <token> also works."""
    sec = build_http_security_config(auth_token="secret")
    result = request_security_error(
        method="GET",
        headers={"Origin": "http://localhost", "Authorization": "Bearer secret"},
        security=sec,
    )
    assert result is None


# ── validated_content_length ─────────────────────────────────────

def test_validated_content_length_none() -> None:
    assert validated_content_length(None, max_body_bytes=1000) == 0


def test_validated_content_length_negative() -> None:
    result = validated_content_length("-5", max_body_bytes=1000)
    assert result == (400, "invalid Content-Length")


def test_validated_content_length_too_large() -> None:
    result = validated_content_length("2000", max_body_bytes=1000)
    assert result == (413, "request body too large")


def test_validated_content_length_not_int() -> None:
    result = validated_content_length("abc", max_body_bytes=1000)
    assert result == (400, "invalid Content-Length")


def test_validated_content_length_ok() -> None:
    assert validated_content_length("500", max_body_bytes=1000) == 500


# ── _capture_payload ─────────────────────────────────────────────

def test_capture_payload_formats_result() -> None:
    result = {
        "status": "ok",
        "bib_name": "main",
        "citekey": "smith2024",
        "action": "insert",
        "pdf_path": "/tmp/smith2024.pdf",
        "dry_run": False,
        "message": "added",
        "warnings": ["w1"],
        "errors": [],
    }
    payload = _capture_payload(result)
    assert payload["citekey"] == "smith2024"
    assert payload["bib"] == "main"
    assert payload["status"] == "ok"


# ── _health_payload ──────────────────────────────────────────────

def test_health_payload(tmp_path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'translation_server_url = "http://localhost:1969"\n'
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        "bibs = []\n"
    )
    payload = _health_payload(str(config), str(tmp_path))
    assert "status" in payload
    assert "config_ok" in payload


# ── _handle_post errors ──────────────────────────────────────────

def test_handle_post_invalid_json(tmp_path, monkeypatch) -> None:

    sec = build_http_security_config()
    handler = FakeHandler(
        path="/capture",
        headers={"Origin": "http://127.0.0.1", "Content-Length": "10"},
        body=b"not json!!!",
    )
    monkeypatch.setattr("pzi.http_api._respond", lambda req, status, data, sec: None)
    _handle_post(handler, str(tmp_path / "c.toml"), str(tmp_path), sec)
    # Should have called _respond with 400 — just verifying no crash


def test_handle_post_body_too_large(tmp_path, monkeypatch) -> None:

    captured = {}

    def capture_respond(req, status, data, sec):
        captured["status"] = status
        captured["data"] = data

    monkeypatch.setattr("pzi.http_api._respond", capture_respond)

    sec = build_http_security_config(max_body_bytes=10)
    handler = FakeHandler(
        path="/capture",
        headers={"Origin": "http://127.0.0.1", "Content-Length": "100"},
    )
    _handle_post(handler, str(tmp_path / "c.toml"), str(tmp_path), sec)
    assert captured["status"] == 413


def test_handle_post_not_found_path(tmp_path, monkeypatch) -> None:
    captured = {}

    def capture_respond(req, status, data, sec):
        captured["status"] = status

    monkeypatch.setattr("pzi.http_api._respond", capture_respond)

    sec = build_http_security_config()
    handler = FakeHandler(
        path="/nonexistent",
        headers={"Origin": "http://127.0.0.1"},
    )
    _handle_post(handler, str(tmp_path / "c.toml"), str(tmp_path), sec)
    assert captured["status"] == 404


# ── _handle_get not found ────────────────────────────────────────

def test_handle_get_not_found(tmp_path, monkeypatch) -> None:
    captured = {}

    def capture_respond(req, status, data, sec):
        captured["status"] = status

    monkeypatch.setattr("pzi.http_api._respond", capture_respond)

    sec = build_http_security_config()
    handler = FakeHandler(
        path="/nonexistent",
        headers={"Origin": "http://127.0.0.1"},
    )
    _handle_get(handler, str(tmp_path / "c.toml"), str(tmp_path), sec)
    assert captured["status"] == 404

# ── process_post_request (replaces _handle_capture / _handle_attach_pdf_bytes) ──


def test_post_capture_non_dict() -> None:
    status, body = process_post_request("/capture", [], "/tmp/c.toml", "/tmp")
    assert status == 400
    assert "object" in body["error"]


def test_post_capture_missing_url() -> None:
    status, body = process_post_request("/capture", {}, "/tmp/c.toml", "/tmp")
    assert status == 400
    assert "url" in body["error"]


def test_post_attach_non_dict() -> None:
    status, body = process_post_request(
        "/attach-pdf-bytes", "not dict", "/tmp/c.toml", "/tmp"
    )
    assert status == 400


def test_post_attach_missing_citekey() -> None:
    status, body = process_post_request(
        "/attach-pdf-bytes", {"pdf_base64": "xxx"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "citekey" in body["error"]


def test_post_attach_missing_pdf_base64() -> None:
    status, body = process_post_request(
        "/attach-pdf-bytes", {"citekey": "x"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "pdf_base64" in body["error"]


def test_post_unknown_path() -> None:
    status, body = process_post_request("/nope", {}, "/tmp/c.toml", "/tmp")
    assert status == 404


# ── _handle_options ──────────────────────────────────────────────

def test_handle_options_blocked_origin(tmp_path, monkeypatch) -> None:
    captured = {}

    def capture_respond(req, status, data, sec):
        captured["status"] = status

    monkeypatch.setattr("pzi.http_api._respond", capture_respond)

    sec = build_http_security_config(allowed_origins=["http://localhost"])
    handler = FakeHandler(
        path="/",
        headers={"Origin": "http://evil.com"},
    )
    _handle_options(handler, sec)
    assert captured["status"] == 403
