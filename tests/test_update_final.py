"""Edge tests for update_service.py uncovered lines (53, 80, 94, 104).

Covers: empty venue string, arxiv_id without year, record with year but
no venue/doi, arxiv_id present with no doi, etc.
"""

from pathlib import Path

from pzi.add_service import add_record_to_bib
from pzi.update_service import (
    _changed_fields,
    _conservative_enrich,
    _needs_update,
    update_bib,
)


def _write_config(tmp_path: Path, bib_path: Path, *, extra: str = "") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
{extra}
""".strip()
    )
    return config_path


# ── _needs_update edge cases ─────────────────────────────────────

def test_needs_update_venue_is_non_string() -> None:
    """Line ~53: venue that is a non-string (e.g., int) triggers _needs_update."""
    assert _needs_update({"venue": 0}) is True


def test_needs_update_venue_is_empty_string() -> None:
    """Whitespace-only venue is not a valid venue."""
    assert _needs_update({"venue": "  "}) is True
    assert _needs_update({"venue": ""}) is True


def test_needs_update_venue_is_None() -> None:
    """None venue triggers update."""
    assert _needs_update({"venue": None}) is True


def test_needs_update_arxiv_no_doi() -> None:
    """arxiv_id present but doi missing → needs update (line ~94)."""
    assert _needs_update({"venue": "CVPR", "arxiv_id": "2401.12345"}) is True


def test_needs_update_no_year() -> None:
    """Missing year → needs update (line ~104)."""
    assert _needs_update({"venue": "CVPR", "doi": "10.1/foo"}) is True


def test_needs_update_no_year_no_venue() -> None:
    """No year at all, unknown venue type."""
    assert _needs_update({}) is True


def test_needs_update_fully_published() -> None:
    """All fields present → no update needed."""
    assert _needs_update({"venue": "CVPR", "doi": "10.1/foo", "year": 2024}) is False


# ── _conservative_enrich edge ────────────────────────────────────

def test_conservative_enrich_empty_list_preserved() -> None:
    """Empty list is treated as non-empty by 'current in (None, "", [], {})'."""
    existing = {"authors": []}
    incoming = {"authors": ["New Author"]}
    result = _conservative_enrich(existing, incoming)
    # Empty list counts as empty → should be filled
    assert result["authors"] == ["New Author"]


def test_conservative_enrich_empty_dict_preserved() -> None:
    """Empty dict is filled by incoming."""
    existing = {"custom": {}}
    incoming = {"custom": {"key": "value"}}
    result = _conservative_enrich(existing, incoming)
    assert result["custom"] == {"key": "value"}


# ── _changed_fields edge ─────────────────────────────────────────

def test_changed_fields_none_to_value() -> None:
    """None → some value is a change."""
    result = _changed_fields({"key": None}, {"key": "value"})
    assert "key" in result


def test_changed_fields_missing_key_in_existing() -> None:
    """Key only in updated counts as change."""
    result = _changed_fields({}, {"new": 1})
    assert result == ["new"]


# ── update_bib edge: record with year but missing venue/doi ─────

def test_update_bib_record_with_year_only(tmp_path: Path) -> None:
    """Record with year but no venue or doi → _needs_update returns True."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "year": 2024, "title": "Graph Parsers"},
        bib_selector=None,
        dry_run=False,
    )

    def fake_search(query, *, server_url):
        return [{"record": {"venue": "CVPR", "doi": "10.1/x"}}]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 1


def test_update_bib_record_with_venue_and_arxiv_no_doi(tmp_path: Path) -> None:
    """Record has venue and arxiv_id but no doi → needs_update True (line ~80)."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "venue": "ICLR", "arxiv_id": "2401.12345", "title": "T"},
        bib_selector=None,
        dry_run=False,
    )

    def fake_search(query, *, server_url):
        return [{"record": {"doi": "10.1/x", "year": 2024}}]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert "doi" in result["items"][0]["changed_fields"]


def test_update_bib_published_record_skipped(tmp_path: Path) -> None:
    """Fully published record is not updated."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "venue": "Nature", "doi": "10.1/x", "year": 2024},
        bib_selector=None,
        dry_run=False,
    )

    search_called = []

    def fake_search(query, *, server_url):
        search_called.append(query)
        return []

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert search_called == []  # search not called
    assert result["status"] == "ok"
    assert len(result["items"]) == 0


def test_update_bib_record_without_query(tmp_path: Path) -> None:
    """Record without doi, arxiv_id, or title → no query → skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "bogus", "venue": ""},
        bib_selector=None,
        dry_run=False,
    )

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=lambda q, **kw: [],
    )
    assert len(result["items"]) == 0
