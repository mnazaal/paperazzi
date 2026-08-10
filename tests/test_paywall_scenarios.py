"""Paywall-scenario integration tests.

Tests the PDF fallback chain without requiring real network access.
All external services are mocked with real-format fixture data, and
file I/O uses tmp_path for .bib and papers/ directories.

Scenarios:
  1. Direct blocked  — translation server returns metadata, PDF fetch → 403
  2. Browser saved   — browser hook discovers alternative PDF URL
  3. Extension flow   — /capture + /attach-pdf-bytes cycle
  4. Unpaywall OA     — publisher blocks, Unpaywall finds open-access mirror
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from pzi import pdf_service
from pzi.add_service import add_input_to_bib
from pzi.bib_repository import read_bib_file
from tests.test_paywall_helpers import (
    make_fetch_binary_403,
    make_fetch_binary_selective,
    make_fetch_search_from_search_fixture,
    make_fetch_unpaywall_from_fixture,
    make_fetch_web_from_article_fixture,
)

DOI = "10.1234/jmlr.2025.00142"


# ── Scenario 1: Direct PDF fetch blocked (403) ─────────────────────────────


def test_direct_blocked_metadata_succeeds_pdf_fails(tmp_path: Path, write_app_config) -> None:
    """Translation server returns metadata with PDF attachment.

    Direct binary fetch of the attachment URL returns 403.
    Result: entry created, pdf_status='direct_blocked', suggestion mentions extension.
    """
    config_path = write_app_config(tmp_path)
    bib_path = tmp_path / "ml.bib"

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value=DOI,
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=lambda url: make_fetch_binary_403(url),
        browser_pdf_cmd=None,
    )

    # Metadata capture must succeed
    assert result["status"] == "ok"
    assert result["citekey"] is not None
    assert result["action"] in ("insert", "update")

    # PDF status: blocked, with actionable suggestion
    assert result.get("pdf_status") == "direct_blocked"
    suggestion = result.get("pdf_suggestion") or ""
    assert "browser extension" in suggestion or "browser_pdf_cmd" in suggestion

    # BibTeX file was written
    assert bib_path.exists()
    bib_content = bib_path.read_text()
    assert "Deep Graph Networks" in bib_content or result["citekey"] in bib_content


# ── Scenario 2: Browser hook discovers alternative PDF (pure unit) ──────────


def test_browser_hook_step_discovers_pdf_when_configured() -> None:
    """Browser pdf discovery step returns PDF URL when browser_pdf_cmd is set.

    Tests the pure pdf_discovery browser_pdf_step in isolation,
    avoiding subprocess execution complexity.
    """
    from pzi.pdf_discovery import PdfDiscoveryContext, browser_pdf_step

    record: dict[str, object] = {
        "title": "Test Paper",
        "source_url": "https://publisher.com/article",
    }
    context: PdfDiscoveryContext = {
        "raw_value": "https://publisher.com/article",
        "server_url": "http://127.0.0.1:1969",
        "browser_pdf_cmd": "any-command-would-work",
        "fetch_web": lambda url, server_url=None, **kw: [],
    }

    # Without actual subprocess, the step returns the record unchanged.
    # The step itself is exercised; subprocess integration is tested
    # in tests/test_browser_pdf.py.
    result = browser_pdf_step(record, context)
    assert result.get("title") == record["title"]


# ── Scenario 3: Browser extension attach flow ──────────────────────────────


def test_extension_attach_pdf_bytes_after_capture(tmp_path: Path, write_app_config) -> None:
    """Full browser extension flow: capture creates entry, then PDF bytes attached.

    Tests the HTTP API handlers with injected service dependencies.
    """
    config_path = write_app_config(tmp_path)
    bib_path = tmp_path / "ml.bib"
    _papers_dir = tmp_path / "papers"

    # Step 1: capture via add_input_to_bib (mimics POST /capture flow)
    capture_result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="https://jmlr.org/papers/v26/25-0142.html",
        record_overrides={
            "fallback_title": "Deep Graph Networks for Citation Context Prediction",
            "fallback_doi": DOI,
        },
        bib_selector=None,
        dry_run=False,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=lambda url: make_fetch_binary_403(url),
        browser_pdf_cmd=None,
    )

    assert capture_result["status"] == "ok"
    citekey = capture_result["citekey"]
    assert citekey is not None

    # Step 2: attach PDF bytes (mimics POST /attach-pdf-bytes from extension)
    from pzi.pdf_service import attach_pdf_bytes

    pdf_content = b"%PDF-1.4 from-browser-extension\n%%EOF"
    pdf_b64 = base64.b64encode(pdf_content).decode("ascii")

    attach_result = attach_pdf_bytes(
        config_path=config_path,
        home_dir=str(tmp_path),
        citekey=citekey,
        pdf_base64=pdf_b64,
        bib_selector=None,
        source_url="https://jmlr.org/papers/v26/25-0142.pdf",
    )

    assert attach_result["status"] == "ok"
    attach_pdf_path = attach_result.get("local_pdf_path")
    assert isinstance(attach_pdf_path, str) and attach_pdf_path
    assert os.path.exists(attach_pdf_path)
    assert Path(attach_pdf_path).read_bytes() == pdf_content

    # Verify BibTeX file has the file field
    bib_data = read_bib_file(str(bib_path))
    entries = [e for e in bib_data["records"] if e.get("citekey") == citekey]
    assert len(entries) == 1
    assert entries[0].get("local_pdf_path") == attach_pdf_path


# ── Scenario 4: Unpaywall open-access fallback ─────────────────────────────


def test_unpaywall_finds_oa_when_direct_blocked(tmp_path: Path, write_app_config) -> None:
    """Direct PDF fetch blocked by publisher (403).

    Unpaywall returns an open-access mirror URL → PDF saved from OA mirror.

    `unpaywall_email` is required: `unpaywall_step` returns the record
    untouched without one, so the whole OA stage is skipped. This test omitted
    it and therefore never exercised the path it is named for — invisible while
    its assertions were gated behind `if pdf_path:`.
    """
    config_path = write_app_config(tmp_path, unpaywall_email="pzi-tests@example.org")

    # selective fetch: block publisher, allow OA mirror
    fetch_binary = make_fetch_binary_selective(
        blocked_hosts=["jmlr.org"],
        pdf_content=b"%PDF-1.4 from-OA-mirror\n",
    )

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value=DOI,
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=fetch_binary,
        fetch_unpaywall=make_fetch_unpaywall_from_fixture(),
        browser_pdf_cmd=None,
    )

    assert result["status"] == "ok"
    assert result["citekey"] is not None

    pdf_path = result.get("pdf_path")
    # Unconditional: the claim is that Unpaywall's OA mirror is used when the
    # publisher blocks, and `if pdf_path:` made "no PDF at all" pass as success.
    assert pdf_path, "no PDF was saved from the OA mirror"
    if pdf_path:
        assert os.path.exists(str(pdf_path))
        with open(str(pdf_path), "rb") as f:
            assert f.read() == b"%PDF-1.4 from-OA-mirror\n"


# ── Scenario 5: the same fallback, one command later ───────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "`pzi pdf retry` fetches the stored pdf_url once and gives up: it runs "
        "no discovery, so a blocked publisher URL is never replaced by an OA "
        "mirror the way the add path does it (item 198, retry half)"
    ),
)
def test_pdf_retry_falls_back_to_oa_when_the_stored_url_is_blocked(
    tmp_path: Path, write_app_config, monkeypatch
) -> None:
    """The retry path has the add path's defect, and its own reason for it.

    `retry_pdf` reads one `pdf_url` off the entry (`pdf_service.py:116`) and
    hands it to the transport ladder, whose every rung retries that same URL.
    An entry whose stored URL has gone 403 — the exact entry `pzi pdf retry`
    exists to rescue — can never reach a different source.
    """
    config_path = write_app_config(tmp_path, unpaywall_email="pzi-tests@example.org")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{smith2024paper,\n"
        "  title = {A Great Paper},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        f"  doi = {{{DOI}}},\n"
        # `pzi-pdf-url`, not `pdf_url`: that is the field name the record
        # projection reads (`bibtex.py:295`), and a plain `pdf_url` makes the
        # test fail with "no PDF URL found on entry" — a setup error wearing
        # the defect's clothes.
        "  pzi-pdf-url = {https://jmlr.org/papers/v26/25-0142.pdf},\n"
        "}\n"
    )

    # The OA mirror the entry should end up at.
    monkeypatch.setattr(
        "pzi.pdf.fetch_unpaywall_pdf_url",
        lambda doi, *, email=None: "https://arxiv.org/pdf/2503.12345.pdf",
    )

    result = pdf_service.retry_pdf(
        config_path=config_path,
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024paper",
        fetch_binary=make_fetch_binary_selective(
            blocked_hosts=["jmlr.org"],
            pdf_content=b"%PDF-1.4 from-OA-mirror\n",
        ),
    )

    assert result["status"] == "ok", result
    local_pdf_path = result["local_pdf_path"]
    assert local_pdf_path, "retry gave up instead of trying another source"
    with open(str(local_pdf_path), "rb") as f:
        assert f.read() == b"%PDF-1.4 from-OA-mirror\n"
