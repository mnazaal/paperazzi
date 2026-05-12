"""Edge-case tests for uncovered branches in add_service.py."""

import urllib.error
from pathlib import Path

from pzi.add_service import (
    _attach_pdf_if_available,
    _attach_similarity_hint,
    _merge_record_sources,
    add_input_to_bib,
)

# ── config / bib helpers ────────────────────────────────────────────────────

def _write_config(tmp_path: Path, bib_path: Path, *, extra: str = "") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1969"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
{extra}
""".strip()
    )
    return config_path


def _pdf_config_and_bib(tmp_path: Path):
    bib_path = tmp_path / "library.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib = {"name": "ml", "path": str(bib_path), "papers_dir": str(tmp_path / "papers")}
    return config_path, bib_path, bib


# ═══════════════════════════════════════════════════════════════════════════════
#  add_input_to_bib  –  uncovered branches
# ═══════════════════════════════════════════════════════════════════════════════

# ── line 71: bib is None in add_input_to_bib ────────────────────────────────

def test_add_input_to_bib_ambiguous_bib_errors(tmp_path: Path) -> None:
    """Resolve returns None → _error_result (line 71)."""
    tmp_path / "library.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
translation_server_url = "http://127.0.0.1:1969"

[[bibs]]
name = "ml"
path = "/tmp/ml.bib"

[[bibs]]
name = "systems"
path = "/tmp/systems.bib"
""".strip()
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/foo",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "error"
    assert result["message"] == "could not resolve target bib"
    assert "no matching bib found" in result["errors"][0]


# ── lines 172-174: connection-error message generation ─────────────────────

def test_add_input_to_bib_connection_error_message(tmp_path: Path) -> None:
    """A ConnectionError (not HTTPError) → podman message (lines 172-174)."""
    bib_path = tmp_path / "library.bib"
    config_path = _write_config(tmp_path, bib_path)

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise ConnectionError("Connection refused")

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/foo",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
    )

    assert result["status"] == "error"
    assert "translation server not reachable" in result["errors"][0]
    assert "podman run" in result["errors"][0]


def test_add_input_to_bib_connection_error_urlerror(tmp_path: Path) -> None:
    """A plain URLError (not HTTPError) should also hit the conn-err branch."""
    bib_path = tmp_path / "library.bib"
    config_path = _write_config(tmp_path, bib_path)


    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise urllib.error.URLError("refused")

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/foo",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
    )

    assert result["status"] == "error"
    assert "translation server not reachable" in result["errors"][0]


# ── lines 366-375: DOI + raw_as_url + flaresolverr fallback ─────────────────

def test_add_input_to_bib_doi_flaresolverr_extracts_meta(tmp_path: Path) -> None:
    """DOI input whose raw_value is a URL; all lookups fail; flaresolverr succeeds."""
    bib_path = tmp_path / "library.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1969"
flaresolverr_url = "http://127.0.0.1:8191"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []  # must also fail so we reach flaresolverr

    def fake_crossref(doi: str) -> None:
        return None

    def fake_openalex(doi: str) -> None:
        return None

    def fake_s2(doi: str) -> None:
        return None

    cloudflare_html = """
<html>
<head>
<meta name="citation_title" content="Deep Learning Book">
<meta name="citation_author" content="Goodfellow, Ian">
<meta name="citation_publication_date" content="2016">
</head>
<body></body>
</html>
"""

    def fake_flaresolverr(url: str) -> str | None:
        return cloudflare_html

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://dl.acm.org/doi/10.5555/3327546.3327713",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        fetch_web=fake_fetch_web,
        fetch_crossref=fake_crossref,
        fetch_openalex=fake_openalex,
        fetch_s2=fake_s2,
        fetch_flaresolverr=fake_flaresolverr,
    )

    assert result["status"] == "ok"
    bib_text = bib_path.read_text()
    assert "Deep Learning Book" in bib_text


# ── lines 398->404, 400->404: url/pdf_url + flaresolverr (various outcomes) ─

def test_add_input_to_bib_url_flaresolverr_returns_none(tmp_path: Path) -> None:
    """URL input, translation returns nothing, flaresolverr returns None
    → ValueError from _fetch_record_for_input (line 398→404)."""
    bib_path = tmp_path / "library.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1969"
flaresolverr_url = "http://127.0.0.1:8191"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_flaresolverr(url: str) -> str | None:
        return None  # html is falsy → 398 → 404

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://cloudflare-protected.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )

    assert result["status"] == "error"
    assert "translation server" in result["errors"][0].lower()


def test_add_input_to_bib_url_flaresolverr_html_no_meta(tmp_path: Path) -> None:
    """URL input, flaresolverr returns HTML but extract_metadata_from_html returns None
    → ValueError (line 400→404)."""
    bib_path = tmp_path / "library.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1969"
flaresolverr_url = "http://127.0.0.1:8191"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    html_without_citation_meta = "<html><body>No citation meta here</body></html>"

    def fake_flaresolverr(url: str) -> str | None:
        return html_without_citation_meta  # html is truthy, but meta will be None

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://cloudflare-protected.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )

    assert result["status"] == "error"
    assert "translation server" in result["errors"][0].lower()


def test_add_input_to_bib_url_flaresolverr_html_none_meta_error(
    tmp_path: Path,
) -> None:
    """Verify that flaresolverr returning HTML with no meta results in
    the right error path (400→404, then ValueError caught by except)."""
    bib_path = tmp_path / "library.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1969"
flaresolverr_url = "http://127.0.0.1:8191"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    # HTML that yields None from extract_metadata_from_html
    def fake_flaresolverr(url: str) -> str | None:
        return "<html><head></head><body>no meta</body></html>"

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://example.com/no-meta",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )

    # The ValueError from _fetch_record_for_input should be caught
    assert result["status"] == "error"
    assert "no results" in result["errors"][0].lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  _merge_record_sources  –  line 440→439
# ═══════════════════════════════════════════════════════════════════════════════

def test_merge_record_sources_skips_none_values() -> None:
    """Overrides with None values should be skipped (line 440→439)."""
    base = {"title": "Original", "doi": "10.1234/foo"}
    overrides = {"title": None, "doi": None, "year": 2024}
    result = _merge_record_sources(base, overrides)
    assert result["title"] == "Original"  # None → not overridden
    assert result["doi"] == "10.1234/foo"
    assert result["year"] == 2024  # non-None → overridden


# ═══════════════════════════════════════════════════════════════════════════════
#  _add_local_pdf  –  uncovered branches
# ═══════════════════════════════════════════════════════════════════════════════

# ── lines 512-513: DOI lookup raises OSError/ValueError ─────────────────────

def test_add_local_pdf_doi_lookup_oserror(tmp_path: Path, monkeypatch) -> None:
    """PDF has DOI but fetch_record raises OSError → fallback (lines 512-513)."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": "10.1234/refused", "title": "Test", "text_sample": "x"},
    )
    monkeypatch.setattr(
        "pzi.add_service._fetch_record_for_input",
        lambda **kwargs: (_ for _ in ()).throw(OSError("server down")),
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert result["citekey"] is not None
    assert "10.1234/refused" in bib_path.read_text()


# ── lines 517-524: title search (success / exception / empty) ───────────────

def test_add_local_pdf_title_search_success(tmp_path: Path, monkeypatch) -> None:
    """PDF has title but no DOI; search succeeds → lines 517-518, 521-522."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": None, "title": "Machine Learning Advances", "text_sample": "x"},
    )

    search_calls: list[str] = []

    def fake_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        search_calls.append(query)
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Machine Learning Advances",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["status"] == "ok"
    assert search_calls == ["Machine Learning Advances"]
    assert "Machine Learning Advances" in bib_path.read_text()
    assert "Smith" in bib_path.read_text()


def test_add_local_pdf_title_search_valueerror(tmp_path: Path, monkeypatch) -> None:
    """PDF has title; search raises ValueError → except → lines 519-520, 523-524."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": None, "title": "Barely There", "text_sample": "x"},
    )

    def fake_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise ValueError("translation server error")

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["status"] == "ok"
    bib_text = bib_path.read_text()
    assert "Barely There" in bib_text


def test_add_local_pdf_title_search_empty(tmp_path: Path, monkeypatch) -> None:
    """PDF has title; search returns [] → else-branch → lines 523-524."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": None, "title": "Unknown Work", "text_sample": "x"},
    )

    def fake_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["status"] == "ok"
    bib_text = bib_path.read_text()
    assert "Unknown Work" in bib_text


def test_add_local_pdf_title_search_oserror(tmp_path: Path, monkeypatch) -> None:
    """PDF has title; search raises OSError → except → lines 519-520, 523-524."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": None, "title": "Error Prone", "text_sample": "x"},
    )

    def fake_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise OSError("connection lost")

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["status"] == "ok"
    bib_text = bib_path.read_text()
    assert "Error Prone" in bib_text


# ── line 541: copy_pdf_to_papers_dir returns an error ───────────────────────

def test_add_local_pdf_copy_error(tmp_path: Path, monkeypatch) -> None:
    """copy_pdf_to_papers_dir returns (None, error) → line 541."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {
            "doi": "10.1234/copyerr",
            "title": "Copy Error",
            "text_sample": "x",
        },
    )

    def fake_fetch_record(**kwargs):
        return {"doi": "10.1234/copyerr", "title": "Copy Error", "year": 2024}

    monkeypatch.setattr("pzi.add_service._fetch_record_for_input", fake_fetch_record)
    monkeypatch.setattr(
        "pzi.add_service.copy_pdf_to_papers_dir",
        lambda **kwargs: (None, "disk full"),
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert "disk full" in result["warnings"]


# ── lines 542->546: copy_pdf_to_papers_dir returns (None, None) ────────────

def test_add_local_pdf_copy_returns_none_none(tmp_path: Path, monkeypatch) -> None:
    """copy_pdf_to_papers_dir returns (None, None) → elif skipped → line 542→546."""
    config_path, bib_path, bib = _pdf_config_and_bib(tmp_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {
            "doi": "10.1234/copynone",
            "title": "Copy None",
            "text_sample": "x",
        },
    )

    def fake_fetch_record(**kwargs):
        return {"doi": "10.1234/copynone", "title": "Copy None", "year": 2024}

    monkeypatch.setattr("pzi.add_service._fetch_record_for_input", fake_fetch_record)
    monkeypatch.setattr(
        "pzi.add_service.copy_pdf_to_papers_dir",
        lambda **kwargs: (None, None),
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
#  _attach_pdf_if_available  –  uncovered branches
# ═══════════════════════════════════════════════════════════════════════════════

def _bib_dict(papers_dir: str = "/tmp/papers") -> dict:
    return {"name": "test", "path": "/tmp/test.bib", "papers_dir": papers_dir}


# ── line 597: dry_run returns early ─────────────────────────────────────────

def test_attach_pdf_if_available_dry_run(tmp_path: Path) -> None:
    """dry_run=True → line 597: returns (record, [])."""
    bib = _bib_dict(str(tmp_path / "papers"))
    record: dict = {"pdf_url": "https://example.com/a.pdf"}

    result, warnings = _attach_pdf_if_available(
        record=record,  # type: ignore[arg-type]
        bib=bib,  # type: ignore[arg-type]
        dry_run=True,
        fetch_binary=None,
    )

    assert result is record
    assert warnings == []


# ── line 601: citekey missing ───────────────────────────────────────────────

def test_attach_pdf_if_available_no_citekey(tmp_path: Path) -> None:
    """No valid citekey → line 601: returns (record, [warning])."""
    bib = _bib_dict(str(tmp_path / "papers"))
    record: dict = {"pdf_url": "https://example.com/a.pdf"}

    result, warnings = _attach_pdf_if_available(
        record=record,  # type: ignore[arg-type]
        bib=bib,  # type: ignore[arg-type]
        dry_run=False,
        fetch_binary=None,
    )

    assert result is record
    assert len(warnings) == 1
    assert "cannot attach PDF before citekey generation" in warnings[0]


# ── line 618: fetch_and_store_pdf_with_fallbacks returns a warning ───────────

def test_attach_pdf_if_available_with_warning(tmp_path: Path, monkeypatch) -> None:
    """Success with a warning → line 618 (warnings.append)."""
    bib = _bib_dict(str(tmp_path / "papers"))
    record: dict = {
        "pdf_url": "https://example.com/a.pdf",
        "citekey": "smith2024test",
    }

    def fake_fetch_and_store(**kwargs):
        return "/tmp/path.pdf", "flaresolverr bypass used", None

    monkeypatch.setattr(
        "pzi.add_service.fetch_and_store_pdf_with_fallbacks",
        fake_fetch_and_store,
    )

    result, warnings = _attach_pdf_if_available(
        record=record,  # type: ignore[arg-type]
        bib=bib,  # type: ignore[arg-type]
        dry_run=False,
        fetch_binary=None,
    )

    assert result["local_pdf_path"] == "/tmp/path.pdf"
    assert "flaresolverr bypass used" in warnings


# ═══════════════════════════════════════════════════════════════════════════════
#  _attach_similarity_hint  –  uncovered branches
# ═══════════════════════════════════════════════════════════════════════════════

# ── lines 641-642: note already contains the hint → skip ────────────────────

def test_similarity_hint_skips_duplicate_hint(monkeypatch) -> None:
    """When note already contains the hint text, return record unchanged (641→642)."""
    monkeypatch.setattr(
        "pzi.add_service.find_exact_match",
        lambda record, existing: None,
    )
    monkeypatch.setattr(
        "pzi.add_service.compute_similarity_hint",
        lambda record, candidates: "jones2024deep",
    )

    record: dict = {"title": "Test", "note": "Possibly similar to jones2024deep"}
    result = _attach_similarity_hint(record, [])  # type: ignore[arg-type]

    # unchanged – the hint was already there
    assert result is record


# ── line 643: existing note + new hint → combined ──────────────────────────

def test_similarity_hint_appends_to_existing_note(monkeypatch) -> None:
    """When note exists but doesn't contain the hint, combine them (line 643)."""
    monkeypatch.setattr(
        "pzi.add_service.find_exact_match",
        lambda record, existing: None,
    )
    monkeypatch.setattr(
        "pzi.add_service.compute_similarity_hint",
        lambda record, candidates: "jones2024deep",
    )

    record: dict = {"title": "Test", "note": "Already reviewed"}
    result = _attach_similarity_hint(record, [])  # type: ignore[arg-type]

    assert result["note"] == "Already reviewed; Possibly similar to jones2024deep"
