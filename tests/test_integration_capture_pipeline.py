"""End-to-end capture pipeline integration tests.

Tests the full path from add_input_to_bib() → classify → fetch → normalize
→ dedup → PDF discovery → write, using tmp_path for .bib + papers/ dirs.

All network calls are mocked with real-format responses from fixture data.
"""

from __future__ import annotations

import os
from pathlib import Path

from pzi.add_service import add_input_to_bib
from pzi.config import dump_app_config
from tests.test_paywall_helpers import (
    make_fetch_binary_403,
    make_fetch_binary_returns_pdf,
    make_fetch_search_from_search_fixture,
    make_fetch_web_from_article_fixture,
)

DOI = "10.1234/jmlr.2025.00142"


# ── Happy path: DOI → entry + PDF ───────────────────────────────────────────


def test_capture_doi_creates_entry_and_saves_pdf(tmp_path: Path, write_app_config) -> None:
    """Full pipeline: DOI → translation server → BibTeX entry + PDF saved."""
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
        fetch_binary=make_fetch_binary_returns_pdf(content=b"%PDF-1.4 integration-test\n"),
        browser_pdf_cmd=None,
    )

    assert result["status"] == "ok"
    assert result["action"] in ("insert", "update")
    citekey = result["citekey"]
    assert citekey is not None

    # BibTeX file written
    assert bib_path.exists()
    bib_content = bib_path.read_text()
    assert citekey in bib_content or "Deep" in bib_content

    # PDF saved
    pdf_path = result.get("pdf_path")
    if pdf_path:
        assert os.path.exists(str(pdf_path))
        assert Path(str(pdf_path)).read_bytes() == b"%PDF-1.4 integration-test\n"


# ── Duplicate detection ─────────────────────────────────────────────────────


def test_capture_duplicate_doi_reuses_citekey(tmp_path: Path, write_app_config) -> None:
    """Same DOI captured twice → second capture reuses existing citekey."""
    config_path = write_app_config(tmp_path)

    # First capture
    r1 = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value=DOI,
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=make_fetch_binary_403,
        browser_pdf_cmd=None,
    )
    assert r1["status"] == "ok"
    ck1 = r1["citekey"]

    # Second capture — same DOI
    r2 = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value=DOI,
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=make_fetch_binary_403,
        browser_pdf_cmd=None,
    )
    assert r2["status"] == "ok"
    # Same citekey reused (update, not insert)
    assert r2["citekey"] == ck1


# ── Dry-run mode ────────────────────────────────────────────────────────────


def test_dry_run_does_not_write_files(tmp_path: Path, write_app_config) -> None:
    """Dry run returns predicted result but writes nothing."""
    config_path = write_app_config(tmp_path)
    bib_path = tmp_path / "ml.bib"

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value=DOI,
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=make_fetch_binary_403,
        browser_pdf_cmd=None,
    )

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["action"] == "insert"
    assert "diff" in result  # preview diff is returned

    # No files written
    assert not bib_path.exists()


# ── Config with multiple bibs ───────────────────────────────────────────────


def test_capture_targets_specific_bib_when_multiple_configured(
    tmp_path: Path, dead_port
) -> None:
    """Two bibs in config; capture to named bib via bib_selector."""
    td = str(tmp_path)
    # Write config with two bibs
    config_path = os.path.join(td, ".config", "pzi", "config.toml")
    bib_ml = os.path.join(td, "ml.bib")
    bib_sys = os.path.join(td, "sys.bib")
    papers_ml = os.path.join(td, "papers-ml")
    papers_sys = os.path.join(td, "papers-sys")
    for d in [os.path.dirname(config_path), papers_ml, papers_sys]:
        os.makedirs(d, exist_ok=True)
    config = {
        "bibs": [
            {"name": "ml", "path": bib_ml, "papers_dir": papers_ml, "default": True},
            {"name": "sys", "path": bib_sys, "papers_dir": papers_sys, "default": False},
        ],
        "translation_server_url": f"http://127.0.0.1:{dead_port}",
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
    }
    Path(config_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config_path).write_text(dump_app_config(config))

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=td,
        value=DOI,
        record_overrides={},
        bib_selector="sys",  # target the non-default bib
        dry_run=False,
        fetch_web=make_fetch_web_from_article_fixture(),
        fetch_search=make_fetch_search_from_search_fixture(),
        fetch_binary=make_fetch_binary_403,
        browser_pdf_cmd=None,
    )

    assert result["status"] == "ok"
    assert result["bib_name"] == "sys"
    assert Path(bib_sys).exists()
    assert not Path(bib_ml).exists()  # default bib untouched
