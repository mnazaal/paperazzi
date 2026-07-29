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
    host_header_allowed,
    origin_allowed,
    request_security_error,
    validated_content_length,
)
from pzi.pdf_attach_session import build_attach_session
from pzi.pdf_attach_session_store import AttachSessionStore

# === safe_header_filename ===


def test_safe_header_filename_strips_quotes_and_crlf() -> None:
    cleaned = http_binary_routes.safe_header_filename('ev"il\r\nSet-Cookie: x.pdf')
    assert '"' not in cleaned
    assert "\r" not in cleaned
    assert "\n" not in cleaned


def test_safe_header_filename_falls_back_when_empty() -> None:
    assert http_binary_routes.safe_header_filename("") == "download"
    assert http_binary_routes.safe_header_filename("   ") == "download"


def test_safe_header_filename_keeps_ordinary_name() -> None:
    assert http_binary_routes.safe_header_filename("smith2024graph.pdf") == "smith2024graph.pdf"


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
        "/inbox/drain",
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


def test_post_capture_concurrent_edit_returns_409(monkeypatch) -> None:
    # A concurrent external edit must surface as 409, not an opaque 500.
    from pzi.bib_repository import ConcurrentEditError

    def _raise(*_a, **_k):
        raise ConcurrentEditError("bib file was modified externally")

    monkeypatch.setattr(http_post_routes, "capture_to_bib", _raise)
    status, body = http_post_routes.process_post_request(
        "/capture", {"url": "https://example.com/paper"}, "/tmp/c.toml", "/tmp"
    )
    assert status == 409
    assert body["status"] == "error"
    assert any("modified externally" in e for e in body["errors"])


def test_post_update_concurrent_edit_returns_409(monkeypatch) -> None:
    from pzi.bib_repository import ConcurrentEditError

    def _raise(*_a, **_k):
        raise ConcurrentEditError("bib file was modified externally")

    monkeypatch.setattr(http_post_routes, "update_bib", _raise)
    status, body = http_post_routes.process_post_request(
        "/update", {"dry_run": False}, "/tmp/c.toml", "/tmp"
    )
    assert status == 409
    assert "modified externally" in body["error"]


def test_post_promote_concurrent_edit_returns_409(monkeypatch) -> None:
    from pzi.bib_repository import ConcurrentEditError

    def _raise(*_a, **_k):
        raise ConcurrentEditError("bib file was modified externally")

    monkeypatch.setattr(http_post_routes, "promote_bib", _raise)
    status, body = http_post_routes.process_post_request(
        "/promote", {"dry_run": False}, "/tmp/c.toml", "/tmp"
    )
    assert status == 409
    assert "modified externally" in body["error"]


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


def test_post_delete_honors_explicit_dry_run_even_with_force(tmp_path: Path) -> None:
    """An explicit `dry_run: true` is a request for a preview, force or not.

    `force` exists to authorize a destructive delete, not to demand one. Letting
    it override the flag turned a caller's preview into a real deletion.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{delete2024,\n  title = {Delete Me}\n}\n")
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, body = http_post_routes.process_post_request(
        "/delete",
        {"citekey": "delete2024", "force": True, "dry_run": True},
        str(cpath),
        str(tmp_path),
    )

    assert status == 200
    assert body["dry_run"] is True
    assert "delete2024" in bib_path.read_text()


def test_post_delete_with_force_and_no_dry_run_flag_really_deletes(
    tmp_path: Path,
) -> None:
    """Force alone still means "delete it" — the fix above must not break that."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{delete2024,\n  title = {Delete Me}\n}\n")
    cpath = tmp_path / "config.toml"
    cpath.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')

    status, body = http_post_routes.process_post_request(
        "/delete",
        {"citekey": "delete2024", "force": True},
        str(cpath),
        str(tmp_path),
    )

    assert status == 200
    assert body["dry_run"] is False
    assert "delete2024" not in bib_path.read_text()


def test_capture_body_jsonld_does_not_clobber_citation_authors_or_year() -> None:
    """JSON-LD is documented as a fallback for absent citation_* meta.

    It was applied after the citation_* fields and overwrote them, so a page
    carrying both had its citation_author and citation_publication_date
    replaced by whatever its JSON-LD blob said.
    """
    overrides = http_post_routes.record_overrides_from_capture_body({
        "embedded_authors": ["Vaswani, Ashish", "Shazeer, Noam"],
        "embedded_year": "2017",
        "embedded_jsonld_authors": ["Someone Else"],
        "embedded_jsonld_year": "1999",
    })

    assert overrides["fallback_authors"] == "Vaswani, Ashish and Shazeer, Noam"
    assert overrides["fallback_year"] == "2017"


def test_capture_body_jsonld_still_fills_in_when_citation_meta_is_absent() -> None:
    overrides = http_post_routes.record_overrides_from_capture_body({
        "embedded_jsonld_authors": ["Ada Lovelace"],
        "embedded_jsonld_year": "1843",
    })

    assert overrides["fallback_authors"] == "Ada Lovelace"
    assert overrides["fallback_year"] == "1843"


def test_capture_body_jsonld_title_still_beats_og_title() -> None:
    """Title has no citation_* source, so JSON-LD must keep winning there."""
    overrides = http_post_routes.record_overrides_from_capture_body({
        "embedded_og_title": "OG Title",
        "embedded_jsonld_title": "JSON-LD Title",
    })

    assert overrides["fallback_title"] == "JSON-LD Title"


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
        "listen_host": "127.0.0.1",
    }


def test_host_header_allowed_loopback_bind_rejects_foreign_host() -> None:
    # DNS-rebinding guard: an attacker page can point its own domain's DNS at
    # 127.0.0.1 and issue a plain GET carrying Host: attacker.com but no
    # Origin header (Origin is only sent for CORS-relevant requests).
    assert not host_header_allowed("attacker.com", "127.0.0.1")
    assert not host_header_allowed("attacker.com:80", "127.0.0.1")


def test_host_header_allowed_loopback_bind_accepts_loopback_host() -> None:
    assert host_header_allowed("127.0.0.1", "127.0.0.1")
    assert host_header_allowed("127.0.0.1:8765", "127.0.0.1")
    assert host_header_allowed("localhost", "127.0.0.1")
    assert host_header_allowed("[::1]:8765", "127.0.0.1")


def test_host_header_allowed_explicit_lan_bind_accepts_that_host() -> None:
    assert host_header_allowed("192.168.1.5:8765", "192.168.1.5")
    assert not host_header_allowed("attacker.com", "192.168.1.5")
    # No implicit loopback carve-out once an operator explicitly binds LAN.
    assert not host_header_allowed("127.0.0.1", "192.168.1.5")


def test_host_header_allowed_missing_host_passes_through() -> None:
    # Real HTTP/1.1 clients always send Host; only hand-built requests omit
    # it, and this guard exists to stop DNS rebinding, not malformed clients.
    assert host_header_allowed(None, "127.0.0.1")
    assert host_header_allowed("", "127.0.0.1")


def test_request_security_error_rejects_dns_rebinding_host() -> None:
    security = build_http_security_config(auth_token=None, listen_host="127.0.0.1")

    assert request_security_error(
        method="GET",
        headers={"Host": "attacker.com"},
        security=security,
    ) == (403, "host not allowed")

    assert request_security_error(
        method="GET",
        headers={"Host": "127.0.0.1"},
        security=security,
    ) is None


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
        lambda config_path, home_dir: {
            "config": {"bibs": [{"name": "main", "path": "/tmp/main.bib",
                                 "papers_dir": "/tmp/papers", "default": True}]}
        },
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


def test_http_refuses_a_bib_path_that_is_not_a_configured_library(monkeypatch) -> None:
    """`bib` over HTTP must name a configured library, never an arbitrary path.

    On the CLI a direct `.bib` path is a documented convenience. Honouring it
    over HTTP let any request reaching the API — the extension, or any local
    process while auth is off — make pzi create and write a library anywhere the
    user can write.
    """
    monkeypatch.setattr(
        http_post_routes,
        "load_config_file",
        lambda config_path, home_dir: {
            "config": {"bibs": [{"name": "main", "path": "/tmp/main.bib",
                                 "papers_dir": "/tmp/papers", "default": True}]}
        },
    )

    status, body = http_post_routes.process_post_request(
        "/capture",
        {"url": "https://example.com/paper", "bib": "/tmp/attacker-chosen.bib"},
        "/tmp/c.toml",
        "/tmp",
    )

    assert status == 400
    assert "configured" in body["error"]


def test_http_still_accepts_a_configured_library_by_name_or_path(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        http_post_routes,
        "load_config_file",
        lambda config_path, home_dir: {
            "config": {"bibs": [{"name": "main", "path": "/tmp/main.bib",
                                 "papers_dir": "/tmp/papers", "default": True}]}
        },
    )
    def _boom(*args, **kwargs):
        raise AssertionError("reached the service")

    monkeypatch.setattr(http_post_routes, "capture_to_bib", _boom)

    # Getting as far as the service is the property under test: a configured
    # library, named either way, must not be rejected by the confinement check.
    for selector in ("main", "/tmp/main.bib"):
        try:
            http_post_routes.process_post_request(
                "/capture",
                {"url": "https://example.com/paper", "bib": selector},
                "/tmp/c.toml",
                "/tmp",
            )
            captured[selector] = "not-rejected"
        except AssertionError as exc:
            captured[selector] = "not-rejected" if "reached the service" in str(exc) else "?"

    assert captured == {"main": "not-rejected", "/tmp/main.bib": "not-rejected"}


# === /capture local-path confinement ===================================


def _capture_config(tmp_path: Path, *, capture_source_dirs: str = "") -> tuple[Path, Path]:
    """Config plus an empty papers_dir, for local-path capture tests."""
    papers = tmp_path / "papers"
    papers.mkdir()
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f"{capture_source_dirs}\n"
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\n'
        f'papers_dir="{papers}"\ndefault=true\n'
    )
    return cpath, papers


def test_capture_refuses_a_local_path_when_no_source_dirs_configured(
    tmp_path: Path,
) -> None:
    """The SSRF guard was skipped for a schemeless value, so a bare path got in.

    `add_local_pdf` would read the file — sending extracted text to metadata
    providers — and copy it into `papers_dir`, from where `GET /pdf/<citekey>`
    serves it, laundering around the read-side confinement.
    """
    secret = tmp_path / "private.pdf"
    secret.write_bytes(b"%PDF-1.4\nsensitive\n")
    cpath, papers = _capture_config(tmp_path)

    status, body = http_post_routes.process_post_request(
        "/capture", {"url": str(secret)}, str(cpath), str(tmp_path),
    )

    assert status == 400
    assert "capture_source_dirs" in body["error"]
    # Nothing was ingested.
    assert list(papers.iterdir()) == []


def test_capture_refuses_a_local_path_outside_the_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "drop"
    allowed.mkdir()
    secret = tmp_path / "private.pdf"
    secret.write_bytes(b"%PDF-1.4\nsensitive\n")
    cpath, papers = _capture_config(
        tmp_path, capture_source_dirs=f'capture_source_dirs = ["{allowed}"]'
    )

    status, body = http_post_routes.process_post_request(
        "/capture", {"url": str(secret)}, str(cpath), str(tmp_path),
    )

    assert status == 400
    assert "outside" in body["error"]
    assert list(papers.iterdir()) == []


def test_capture_refuses_a_traversal_out_of_an_allowed_dir(tmp_path: Path) -> None:
    """`..` is collapsed before the containment test, not after."""
    allowed = tmp_path / "drop"
    allowed.mkdir()
    secret = tmp_path / "private.pdf"
    secret.write_bytes(b"%PDF-1.4\nsensitive\n")
    cpath, papers = _capture_config(
        tmp_path, capture_source_dirs=f'capture_source_dirs = ["{allowed}"]'
    )

    status, body = http_post_routes.process_post_request(
        "/capture",
        {"url": str(allowed / ".." / "private.pdf")},
        str(cpath),
        str(tmp_path),
    )

    assert status == 400
    assert list(papers.iterdir()) == []


def test_capture_refuses_a_symlink_pointing_out_of_an_allowed_dir(
    tmp_path: Path,
) -> None:
    """Symlinks are resolved before the containment test."""
    allowed = tmp_path / "drop"
    allowed.mkdir()
    secret = tmp_path / "private.pdf"
    secret.write_bytes(b"%PDF-1.4\nsensitive\n")
    (allowed / "innocent.pdf").symlink_to(secret)
    cpath, papers = _capture_config(
        tmp_path, capture_source_dirs=f'capture_source_dirs = ["{allowed}"]'
    )

    status, _body = http_post_routes.process_post_request(
        "/capture", {"url": str(allowed / "innocent.pdf")}, str(cpath), str(tmp_path),
    )

    assert status == 400
    assert list(papers.iterdir()) == []


# === /inbox/drain confinement ==========================================


def _inbox_config(tmp_path: Path, *, inbox_line: str = "") -> Path:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f"{inbox_line}\n"
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n'
    )
    return cpath


def test_inbox_drain_is_closed_when_no_inbox_path_configured(tmp_path: Path) -> None:
    """Draining rewrites the named file in place, so an unvalidated path let any
    loopback-reachable client truncate a file the user can write."""
    victim = tmp_path / "notes.txt"
    victim.write_text("important\nlines\n")
    cpath = _inbox_config(tmp_path)

    status, body = http_post_routes.process_post_request(
        "/inbox/drain", {"file": str(victim)}, str(cpath), str(tmp_path),
    )

    assert status == 400
    assert "inbox_path" in body["error"]
    assert victim.read_text() == "important\nlines\n"
    assert not (tmp_path / "notes.txt.lock").exists()


def test_inbox_drain_refuses_a_file_that_is_not_the_configured_one(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("")
    victim = tmp_path / "notes.txt"
    victim.write_text("important\nlines\n")
    cpath = _inbox_config(tmp_path, inbox_line=f'inbox_path = "{inbox}"')

    status, body = http_post_routes.process_post_request(
        "/inbox/drain", {"file": str(victim)}, str(cpath), str(tmp_path),
    )

    assert status == 400
    assert "configured inbox_path" in body["error"]
    assert victim.read_text() == "important\nlines\n"


def test_inbox_drain_rejects_a_non_numeric_delay(tmp_path: Path) -> None:
    """It used to coerce silently to 0.0 — including `true`, since bool is an int."""
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("")
    cpath = _inbox_config(tmp_path, inbox_line=f'inbox_path = "{inbox}"')

    for bad in ("soon", True, -1):
        status, body = http_post_routes.process_post_request(
            "/inbox/drain", {"file": str(inbox), "delay": bad}, str(cpath), str(tmp_path),
        )
        assert status == 400, bad
        assert "delay" in body["error"], bad
