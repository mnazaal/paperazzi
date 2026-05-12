"""Tests for extracted pure HTTP handler functions."""

from pathlib import Path

import pytest

from pzi import http_api


# === process_get_request ===


def test_process_get_health(tmp_path: Path) -> None:
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{tmp_path / "ml.bib"}"\ndefault=true\n'
    )
    status, body = http_api.process_get_request(
        "/health", str(cpath), str(tmp_path)
    )
    assert status == 200
    assert "config_ok" in body


def test_process_get_bibs(tmp_path: Path) -> None:
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{tmp_path / "ml.bib"}"\ndefault=true\n'
    )
    status, body = http_api.process_get_request(
        "/bibs", str(cpath), str(tmp_path)
    )
    assert status == 200
    assert body["bibs"][0]["name"] == "ml"


def test_process_get_bibs_error() -> None:
    status, body = http_api.process_get_request(
        "/bibs", "/nonexistent/config.toml", "/tmp"
    )
    assert status == 500
    assert body["status"] == "error"


def test_process_get_not_found() -> None:
    status, body = http_api.process_get_request(
        "/nope", "/tmp/c.toml", "/tmp"
    )
    assert status == 404
    assert "not found" in body["error"]


# === process_post_request tests skipped until extraction ===


@pytest.mark.skip(reason="process_post_request not yet extracted")
def test_post_capture_empty_url() -> None:
    pass


@pytest.mark.skip(reason="process_post_request not yet extracted")
def test_post_capture_non_dict() -> None:
    pass


@pytest.mark.skip(reason="process_post_request not yet extracted")
def test_post_attach_missing_citekey() -> None:
    pass


@pytest.mark.skip(reason="process_post_request not yet extracted")
def test_post_attach_missing_pdf_base64() -> None:
    pass


@pytest.mark.skip(reason="process_post_request not yet extracted")
def test_post_attach_non_dict() -> None:
    pass


@pytest.mark.skip(reason="process_post_request not yet extracted")
def test_post_unknown_path() -> None:
    pass
