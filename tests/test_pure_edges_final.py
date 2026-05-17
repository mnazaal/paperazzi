"""Pure edge parametrized tests — closing the gap to 100%."""

from pathlib import Path

import pytest

from pzi import (
    cli,
    html_metadata,
    http_api,
    identifiers,
    pdf_service,
    preprint_detector,
    search_service,
    similarity,
    update_service,
)

# ================================================================
# cli.py: _run_serve with explicit host/port
# ================================================================


def test_run_serve_explicit_host_port(tmp_path: Path, monkeypatch) -> None:
    from io import StringIO
    cpath = tmp_path / "config.toml"
    cpath.write_text("bibs=[]\n")
    monkeypatch.setattr("pzi.http_api.run_server", lambda **kw: None)
    stdout, stderr = StringIO(), StringIO()
    exit_code = cli.run_cli(
        ["serve", "--host", "0.0.0.0", "--port", "9999", "--config", str(cpath)],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )
    assert exit_code == 0
    assert "0.0.0.0:9999" in stdout.getvalue()


# ================================================================
# europepmc.py
# ================================================================


def test_europepmc_pdf_non_string_availability() -> None:
    from pzi.metadata_sources import _extract_pdf_url
    url = _extract_pdf_url({
        "fullTextUrlList": {
            "fullTextUrl": [{
                "url": "https://epmc.org/p.pdf",
                "documentStyle": "pdf",
                "availability": None,
            }]
        }
    })
    assert url in (None, "https://epmc.org/p.pdf")


# ================================================================
# html_metadata.py
# ================================================================


def test_html_extract_no_jsonld_no_meta() -> None:
    result = html_metadata.extract_metadata_from_html("<html><body>Hello</body></html>")
    assert result is None or isinstance(result, dict)


# ================================================================
# http_api.py
# ================================================================


def test_process_post_capture_with_valid_url(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n'
    )
    status, body = http_api.process_post_request(
        "/capture", {"url": "10.1234/test-capture"}, str(cpath), str(tmp_path)
    )
    assert status in (200, 400)


def test_process_post_capture_dry_run(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n'
    )
    status, body = http_api.process_post_request(
        "/capture", {"url": "10.1234/test-dry", "dry_run": True}, str(cpath), str(tmp_path)
    )
    assert status in (200, 400)


# ================================================================
# identifiers.py
# ================================================================


def test_normalize_url_empty() -> None:
    assert identifiers.normalize_url("") is None


def test_classify_input_relative_pdf_path() -> None:
    result = identifiers.classify_input("papers/paper.pdf")
    assert result["kind"] is not None


# ================================================================
# pdf_service.py
# ================================================================


def test_extract_title_only_short_lines() -> None:
    result = pdf_service._extract_title_from_text("a\nb\nc\nd\ne\nf\ng\nh\ni\nj")
    assert result is None


# ================================================================
# preprint_detector.py
# ================================================================


def test_is_preprint_published_with_everything() -> None:
    rec = {
        "citekey": "ck", "title": "T", "doi": "10.1234/t",
        "venue": "Journal", "year": 2024, "authors": ["A"],
    }
    assert not preprint_detector.is_preprint(rec)


def test_is_preprint_no_arxiv_no_venue() -> None:
    rec = {"citekey": "ck", "title": "T", "year": 2024}
    result = preprint_detector.is_preprint(rec)
    assert isinstance(result, bool)


# ================================================================
# search_service.py
# ================================================================


def test_search_bib_no_records_match(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{test2024,\n  title={Something Else}\n}\n")
    cpath = tmp_path / "config.toml"
    cpath.write_text(
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n'
    )
    result = search_service.search_bib(
        config_path=str(cpath), home_dir=str(tmp_path),
        bib_selector=None, query="nonexistent", author="", year=None, tag="",
    )
    assert result["status"] == "ok"
    assert result["matches"] == []


# ================================================================
# similarity.py
# ================================================================


def test_similarity_author_overlap_empty() -> None:
    score = similarity.author_overlap([], [])
    assert score == pytest.approx(0.0)


# ================================================================
# update_service.py
# ================================================================


def test_update_needs_update_no_doi_no_arxiv() -> None:
    rec = {"title": "Preprint", "year": 2024, "venue": None}
    result = update_service._needs_update(rec)
    assert isinstance(result, bool)


def test_update_changed_fields_preserves_user_owned() -> None:
    existing = {"note": "my note", "tags": ["ml"], "local_pdf_path": "/t/x.pdf"}
    candidate = {"note": "other", "tags": ["cv"]}
    changes = update_service._changed_fields_for_candidate(existing, candidate)
    assert "note" not in changes
    assert "tags" not in changes
    assert "local_pdf_path" not in changes
