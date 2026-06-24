"""Tests for extracted pure HTTP handler functions."""

import base64
from pathlib import Path

from pzi import http_binary_routes, http_get_routes, http_post_routes, http_status
from pzi.capture_models import AuthHints, CaptureInput, CaptureOptions, PageArtifact, PdfCandidate
from pzi.http_get_routes import process_get_request
from pzi.http_security import (
    AUTH_HEADER,
    RateLimiter,
    build_http_security_config,
    origin_allowed,
    request_security_error,
    validated_content_length,
)
from pzi.pdf_attach_session import build_attach_session
from pzi.pdf_attach_session_store import AttachSessionStore

# === process_get_request ===


def test_pdf_file_response_is_planned_by_binary_route_module(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    pdf_path = papers_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        f"""
@article{{a2024,
  title = {{A}},
  file = {{{pdf_path}}}
}}
""".strip()
    )
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\npapers_dir="{papers_dir}"\ndefault=true\n'
    )

    status, response = http_binary_routes.build_pdf_file_response(
        config_path=str(cpath),
        home_dir=str(tmp_path),
        citekey="a2024",
        bib_selector=None,
    )

    assert status == 200
    assert response.path == pdf_path
    assert response.content_type == "application/pdf"
    assert response.filename == "a2024.pdf"


def test_raw_export_response_is_planned_by_binary_route_module(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{a2024, title = {A}}\n")
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, response = http_binary_routes.build_export_bytes_response(
        config_path=str(cpath),
        home_dir=str(tmp_path),
        fmt="bibtex",
        bib_selector=None,
    )

    assert status == 200
    assert response.content_type == "application/x-bibtex"
    assert response.filename == "ml.bib"
    assert b"a2024" in response.content


def test_raw_export_response_rejects_unsupported_format(tmp_path: Path) -> None:
    cpath = tmp_path / "config.toml"
    cpath.write_text("")

    status, response = http_binary_routes.build_export_bytes_response(
        config_path=str(cpath),
        home_dir=str(tmp_path),
        fmt="xlsx",
        bib_selector=None,
    )

    assert status == 400
    assert response["error"] == "unsupported format: xlsx"


def test_get_route_tables_cover_declared_json_routes() -> None:
    exact_paths = {route.path for route in http_get_routes.GET_ROUTES}
    prefix_paths = {route.prefix for route in http_get_routes.GET_PREFIX_ROUTES}

    assert exact_paths == {"/health", "/bibs", "/search", "/entries", "/tags", "/export"}
    assert prefix_paths == {"/detail/", "/tags/"}


def test_post_route_table_covers_declared_json_routes() -> None:
    paths = {route.path for route in http_post_routes.POST_ROUTES}

    assert paths == {
        "/capture",
        "/attach-pdf-bytes",
        "/attach-pdf-raw",
        "/tags/add",
        "/tags/remove",
        "/update",
        "/promote",
        "/browser/discover",
        "/browser/download",
        "/delete",
    }


def test_http_status_maps_service_results_by_contract() -> None:
    assert http_status.status_for_service_result({"status": "ok"}) == 200
    assert http_status.status_for_service_result(
        {"status": "error", "errors": ["config file not found"]}
    ) == 400
    assert http_status.status_for_service_result(
        {"status": "error", "message": "citekey not found: x"}
    ) == 404
    assert http_status.status_for_service_result(
        {"status": "error", "errors": ["browser session not available"]}
    ) == 503
    assert http_status.status_for_service_result(
        {"status": "error", "errors": ["boom"]}, default_error_status=500
    ) == 500


def test_process_get_health(tmp_path: Path) -> None:
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{tmp_path / "ml.bib"}"\ndefault=true\n'
    )
    status, body = process_get_request(
        "/health", str(cpath), str(tmp_path)
    )
    assert status == 200
    assert "config_ok" in body


def test_process_get_bibs(tmp_path: Path) -> None:
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{tmp_path / "ml.bib"}"\ndefault=true\n'
    )
    status, body = process_get_request(
        "/bibs", str(cpath), str(tmp_path)
    )
    assert status == 200
    assert body["bibs"][0]["name"] == "ml"


def test_process_get_bibs_error() -> None:
    status, body = process_get_request(
        "/bibs", "/nonexistent/config.toml", "/tmp"
    )
    assert status == 400
    assert body["status"] == "error"


def test_process_get_entries_clamps_negative_offset(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        """
@article{a2024,
  title = {A}
}

@article{b2024,
  title = {B}
}
""".strip()
    )
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, body = process_get_request(
        "/entries?offset=-1&limit=1", str(cpath), str(tmp_path)
    )

    assert status == 200
    assert body["offset"] == 0
    assert [entry["citekey"] for entry in body["entries"]] == ["a2024"]


def test_process_get_entries_uses_listing_service_sort_and_summary_fields(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    pdf_path = papers_dir / "a.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        f"""
@article{{old2020,
  title = {{Old}},
  author = {{Ada Lovelace}},
  year = {{2020}},
  doi = {{10.1/old}}
}}

@inproceedings{{new2024,
  title = {{New}},
  author = {{Grace Hopper}},
  year = {{2024}},
  file = {{{pdf_path}}}
}}
""".strip()
    )
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\npapers_dir="{papers_dir}"\ndefault=true\n'
    )

    status, body = process_get_request(
        "/entries?sort=year&limit=2", str(cpath), str(tmp_path)
    )

    assert status == 200
    assert body["sort"] == "year"
    assert [entry["citekey"] for entry in body["entries"]] == ["new2024", "old2020"]
    assert "entry_type" in body["entries"][0]
    assert body["entries"][0]["has_pdf"] is True
    assert body["entries"][1]["doi"] == "10.1/old"


def test_process_get_not_found() -> None:
    status, body = process_get_request(
        "/nope", "/tmp/c.toml", "/tmp"
    )
    assert status == 404
    assert "not found" in body["error"]


def test_process_get_tags_without_citekey_lists_all_tags(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        """
@article{a2024,
  title = {A},
  keywords = {ml, graphs}
}

@article{b2024,
  title = {B},
  keywords = {graphs, nlp}
}
""".strip()
    )
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, body = process_get_request("/tags", str(cpath), str(tmp_path))

    assert status == 200
    assert body["citekey"] is None
    assert body["tags"] == ["graphs", "ml", "nlp"]


# === process_post_request (pure dispatch, no network) ===


def test_post_capture_missing_url() -> None:
    status, body = http_post_routes.process_post_request(
        "/capture", {"not_url": "x"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "url required" in body["error"]


def test_post_capture_non_dict() -> None:
    status, body = http_post_routes.process_post_request(
        "/capture", "not a dict", "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "must be a JSON object" in body["error"]


def test_post_capture_private_url_rejected() -> None:
    status, body = http_post_routes.process_post_request(
        "/capture", {"url": "http://127.0.0.1/test.pdf"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "public http(s) URL" in body["error"]


def test_capture_input_from_http_body_maps_capture_hints() -> None:
    capture = http_post_routes.capture_input_from_http_body(
        {
            "url": " https://example.com/paper ",
            "bib": "ml",
            "page_title": "Graph Parsers",
            "cookies": "sid=123",
            "page_html": "<html></html>",
            "pdf_url_candidates": ["https://example.com/a.pdf"],
        },
        pdf_candidates=["https://example.com/a.pdf"],
    )

    assert capture == CaptureInput(
        value="https://example.com/paper",
        record_overrides={"fallback_title": "Graph Parsers"},
        bib_selector="ml",
        page_artifact=PageArtifact(html="<html></html>", source="http"),
        pdf_candidates=(PdfCandidate("https://example.com/a.pdf", source="http"),),
        auth_hints=AuthHints(cookies="sid=123"),
    )


def test_capture_options_from_http_body_uses_config_page_metadata_cmd() -> None:
    assert http_post_routes.capture_options_from_http_body(
        {"dry_run": True, "force_new": True},
        config={
            "page_metadata_cmd": "config-tool --json",
            "page_metadata_timeout_seconds": 8,
        },
    ) == CaptureOptions(
        dry_run=True,
        force_new=True,
        page_metadata_cmd="config-tool --json",
        page_metadata_timeout_seconds=8,
    )


def test_post_attach_missing_citekey() -> None:
    status, body = http_post_routes.process_post_request(
        "/attach-pdf-bytes", {"pdf_base64": "AAAA"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "citekey required" in body["error"]


def test_post_attach_missing_pdf_base64() -> None:
    status, body = http_post_routes.process_post_request(
        "/attach-pdf-bytes", {"citekey": "smith2024"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "pdf_base64 required" in body["error"]


def test_post_attach_non_dict() -> None:
    status, body = http_post_routes.process_post_request(
        "/attach-pdf-bytes", [], "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "must be a JSON object" in body["error"]


def test_post_attach_raw_missing_citekey() -> None:
    # json.loads path — citekey comes from query params in real handler,
    # but process_post_request on /attach-pdf-raw with dict body falls through
    # to _handle_attach_pdf_raw_post which requires citekey
    status, body = http_post_routes.process_post_request(
        "/attach-pdf-raw",
        {"pdf_bytes": b"%PDF-1.4 test"},
        "/tmp/c.toml",
        "/tmp",
    )
    assert status == 400
    assert "citekey required" in (body.get("error") or "")


def test_post_attach_raw_missing_pdf_bytes() -> None:
    status, body = http_post_routes.process_post_request(
        "/attach-pdf-raw",
        {"citekey": "smith2024"},
        "/tmp/c.toml",
        "/tmp",
    )
    assert status == 400
    assert "pdf_bytes" in (body.get("error") or "")


def test_post_tags_add_missing_args() -> None:
    status, body = http_post_routes.process_post_request(
        "/tags/add", {"notags": True}, "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "error" in body


def test_post_tags_remove_non_dict() -> None:
    status, body = http_post_routes.process_post_request(
        "/tags/remove", "bad", "/tmp/c.toml", "/tmp"
    )
    assert status == 400
    assert "must be a JSON object" in body["error"]


def test_post_unknown_path() -> None:
    status, body = http_post_routes.process_post_request(
        "/nope", {}, "/tmp/c.toml", "/tmp"
    )
    assert status == 404
    assert "not found" in body["error"]


def test_post_browser_discover_rejects_private_page_url() -> None:
    status, body = http_post_routes.process_post_request(
        "/browser/discover",
        {"page_url": "http://127.0.0.1/admin"},
        "/tmp/c.toml",
        "/tmp",
        browser_manager=object(),
    )

    assert status == 400
    assert "public http(s) URL" in body["error"]


def test_post_browser_download_rejects_private_pdf_url() -> None:
    status, body = http_post_routes.process_post_request(
        "/browser/download",
        {"pdf_url": "http://127.0.0.1/secret.pdf"},
        "/tmp/c.toml",
        "/tmp",
        browser_manager=object(),
    )

    assert status == 400
    assert "public http(s) URL" in body["error"]


def test_post_browser_download_rejects_large_pdf_before_base64() -> None:
    class FakeBrowserManager:
        def download_pdf_bytes(self, _url: str) -> bytes:
            return b"%PDF-1.4\n" + (b"x" * 1025)

    status, body = http_post_routes.process_post_request(
        "/browser/download",
        {"pdf_url": "https://example.com/paper.pdf"},
        "/tmp/c.toml",
        "/tmp",
        browser_manager=FakeBrowserManager(),
        max_browser_pdf_bytes=1024,
    )

    assert status == 413
    assert "PDF too large" in body["error"]


def test_post_browser_download_accepts_duck_typed_browser_manager() -> None:
    class FakeBrowserManager:
        def download_pdf_bytes(self, _url: str) -> bytes:
            return b"%PDF-1.4 ok"

    status, body = http_post_routes.process_post_request(
        "/browser/download",
        {"pdf_url": "https://example.com/paper.pdf"},
        "/tmp/c.toml",
        "/tmp",
        browser_manager=FakeBrowserManager(),
    )

    assert status == 200
    assert base64.b64decode(body["pdf_base64"]) == b"%PDF-1.4 ok"


def test_post_delete_defaults_to_dry_run(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{delete2024,\n  title = {Delete Me}\n}\n")
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, body = http_post_routes.process_post_request(
        "/delete", {"citekey": "delete2024"}, str(cpath), str(tmp_path)
    )

    assert status == 200
    assert body["dry_run"] is True
    assert "delete2024" in bib_path.read_text()


def test_post_delete_requires_force_for_destructive_delete(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{delete2024,\n  title = {Delete Me}\n}\n")
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, body = http_post_routes.process_post_request(
        "/delete",
        {"citekey": "delete2024", "dry_run": False},
        str(cpath),
        str(tmp_path),
    )

    assert status == 400
    assert "force" in body["error"]
    assert "delete2024" in bib_path.read_text()


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


def test_attach_session_max_bytes_does_not_exceed_http_body_limit() -> None:
    security = build_http_security_config()

    assert http_post_routes.MAX_BROWSER_PDF_BYTES <= security["max_body_bytes"]


def test_rate_limiter_tracks_remaining_and_reset() -> None:
    limiter = RateLimiter(max_requests=2, window_seconds=60)

    assert limiter.check("client")[:2] == (True, 1)
    assert limiter.check("client")[:2] == (True, 0)
    assert limiter.check("client")[:2] == (False, 0)


def test_post_capture_emits_pdf_request_and_stores_attach_session(monkeypatch) -> None:
    store = AttachSessionStore(clock=lambda: 100.0)

    monkeypatch.setattr(
        http_post_routes,
        "load_config_file",
        lambda config_path, home_dir: {"config": {}},
    )
    monkeypatch.setattr(
        http_post_routes,
        "capture_to_bib",
        lambda *args, **kwargs: {
            "status": "ok",
            "bib_name": "main",
            "citekey": "poborchaya2022analysis",
            "action": "inserted",
            "pdf_path": None,
            "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
            "pdf_status": "direct_blocked",
            "pdf_error": None,
            "pdf_suggestion": None,
            "dry_run": False,
            "message": "captured",
            "warnings": [],
            "errors": [],
        },
    )

    status, body = http_post_routes.process_post_request(
        "/capture",
        {
            "url": "https://ieeexplore.ieee.org/document/9840963",
            "bib": "main",
            "browser": "chrome-extension",
            "pdf_url_candidates": [
                "https://ieeexplore.ieee.org/document/9840963",
                "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
            ],
        },
        "/tmp/c.toml",
        "/tmp",
        attach_session_store=store,
        request_id_factory=lambda: "req-1",
        token_factory=lambda: "tok-1",
        time_factory=lambda: 100.0,
    )

    assert status == 200
    assert body["pdf_request"]["request_id"] == "req-1"
    assert body["pdf_request"]["attach"]["token"] == "tok-1"
    assert [c["kind"] for c in body["pdf_request"]["candidates"]] == [
        "pdf_gateway",
        "article_page",
    ]
    session = store.get("req-1")
    assert session is not None
    assert session.citekey == "poborchaya2022analysis"
    assert session.bib == "main"


def test_post_capture_uses_configured_api_url_for_pdf_attach_request(monkeypatch) -> None:
    store = AttachSessionStore(clock=lambda: 100.0)

    monkeypatch.setattr(
        http_post_routes,
        "load_config_file",
        lambda config_path, home_dir: {"config": {"api_url": "http://127.0.0.1:9876"}},
    )
    monkeypatch.setattr(
        http_post_routes,
        "capture_to_bib",
        lambda *args, **kwargs: {
            "status": "ok",
            "bib_name": "main",
            "citekey": "smith2024paper",
            "action": "inserted",
            "pdf_path": None,
            "pdf_url": "https://example.com/paper.pdf",
            "pdf_status": "direct_blocked",
            "dry_run": False,
            "message": "captured",
            "warnings": [],
            "errors": [],
        },
    )

    status, body = http_post_routes.process_post_request(
        "/capture",
        {
            "url": "https://example.com/paper",
            "pdf_url_candidates": ["https://example.com/paper.pdf"],
        },
        "/tmp/c.toml",
        "/tmp",
        attach_session_store=store,
        request_id_factory=lambda: "req-1",
        token_factory=lambda: "tok-1",
        time_factory=lambda: 100.0,
    )

    assert status == 200
    assert body["pdf_request"]["attach"]["url"].startswith(
        "http://127.0.0.1:9876/attach-pdf-raw?"
    )


def test_post_attach_raw_with_request_id_requires_valid_attach_token(monkeypatch) -> None:
    store = AttachSessionStore(clock=lambda: 200.0)
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib="main",
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=20,
        allowed_source_urls=["https://example.com/a.pdf"],
    )
    store.put(session)
    called = {}

    def fake_attach_pdf_raw_bytes(**kwargs):
        called["kwargs"] = kwargs
        return {
            "status": "ok",
            "bib_name": "main",
            "citekey": "smith2024",
            "local_pdf_path": "/tmp/smith2024.pdf",
            "source_url": "https://example.com/a.pdf",
            "message": "attached PDF",
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(
        http_post_routes,
        "attach_pdf_raw_bytes",
        fake_attach_pdf_raw_bytes,
    )

    bad_status, bad_body = http_post_routes.process_post_request(
        "/attach-pdf-raw",
        {
            "request_id": "req-1",
            "attach_token": "wrong",
            "citekey": "smith2024",
            "bib": "main",
            "source_url": "https://example.com/a.pdf",
            "pdf_bytes": b"%PDF-1.7 test",
        },
        "/tmp/c.toml",
        "/tmp",
        attach_session_store=store,
        time_factory=lambda: 200.0,
    )
    assert bad_status == 403
    assert bad_body["error"] == "invalid attach token"

    ok_status, ok_body = http_post_routes.process_post_request(
        "/attach-pdf-raw",
        {
            "request_id": "req-1",
            "attach_token": "tok-1",
            "citekey": "smith2024",
            "bib": "main",
            "source_url": "https://example.com/a.pdf",
            "pdf_bytes": b"%PDF-1.7 test",
        },
        "/tmp/c.toml",
        "/tmp",
        attach_session_store=store,
        time_factory=lambda: 200.0,
    )
    assert ok_status == 200
    assert ok_body["status"] == "ok"
    assert called["kwargs"]["citekey"] == "smith2024"
    assert store.get("req-1") is None


def test_post_attach_bytes_with_request_id_requires_valid_attach_token(monkeypatch) -> None:
    store = AttachSessionStore(clock=lambda: 200.0)
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib="main",
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=20,
        allowed_source_urls=["https://example.com/a.pdf"],
    )
    store.put(session)
    called = {}

    def fake_attach_pdf_bytes(**kwargs):
        called["kwargs"] = kwargs
        return {
            "status": "ok",
            "bib_name": "main",
            "citekey": "smith2024",
            "local_pdf_path": "/tmp/smith2024.pdf",
            "source_url": "https://example.com/a.pdf",
            "message": "attached PDF",
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(http_post_routes, "attach_pdf_bytes", fake_attach_pdf_bytes)

    bad_status, bad_body = http_post_routes.process_post_request(
        "/attach-pdf-bytes",
        {
            "request_id": "req-1",
            "attach_token": "wrong",
            "citekey": "smith2024",
            "bib": "main",
            "source_url": "https://example.com/a.pdf",
            "pdf_base64": "JVBERi0xLjQgdGVzdA==",
        },
        "/tmp/c.toml",
        "/tmp",
        attach_session_store=store,
        time_factory=lambda: 200.0,
    )

    assert bad_status == 403
    assert bad_body["error"] == "invalid attach token"

    ok_status, ok_body = http_post_routes.process_post_request(
        "/attach-pdf-bytes",
        {
            "request_id": "req-1",
            "attach_token": "tok-1",
            "citekey": "smith2024",
            "bib": "main",
            "source_url": "https://example.com/a.pdf",
            "pdf_base64": "JVBERi0xLjQgdGVzdA==",
        },
        "/tmp/c.toml",
        "/tmp",
        attach_session_store=store,
        time_factory=lambda: 200.0,
    )

    assert ok_status == 200
    assert ok_body["status"] == "ok"
    assert called["kwargs"]["citekey"] == "smith2024"
    assert store.get("req-1") is None
