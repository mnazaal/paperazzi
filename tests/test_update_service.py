"""Edge-case coverage for update_service."""

from pathlib import Path

from pzi.add_service import add_record_to_bib
from pzi.update_service import (
    _changed_fields,
    _changed_fields_for_candidate,
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


# ── _needs_update ────────────────────────────────────────────────

def test_needs_update_no_venue_returns_true() -> None:
    """A record without venue needs enrichment."""
    assert _needs_update({"title": "Foo"}) is True
    assert _needs_update({}) is True


def test_needs_update_empty_venue_returns_true() -> None:
    """A record with whitespace-only venue needs enrichment."""
    assert _needs_update({"venue": "  "}) is True
    assert _needs_update({"venue": ""}) is True


def test_needs_update_venue_non_string_returns_true() -> None:
    """Venue that isn't a string triggers enrichment."""
    assert _needs_update({"venue": 42}) is True
    assert _needs_update({"venue": None}) is True


def test_needs_update_arxiv_without_doi_returns_true() -> None:
    """arxiv_id present and doi missing → needs update."""
    assert _needs_update({"venue": "CVPR", "arxiv_id": "2401.12345"}) is True


def test_needs_update_no_year_returns_true() -> None:
    """Missing year → needs update."""
    assert _needs_update({"venue": "CVPR", "doi": "10.1/foo"}) is True


def test_needs_update_published_record_returns_false() -> None:
    """Fully-published record (venue + doi + year) should be skipped."""
    assert (
        _needs_update({"venue": "CVPR", "doi": "10.1/foo", "year": 2024}) is False
    )


def test_needs_update_arxiv_with_doi_and_year_returns_false() -> None:
    """arxiv_id with doi AND year → fully published, no update needed."""
    assert (
        _needs_update(
            {"venue": "CVPR", "doi": "10.1/foo", "year": 2024, "arxiv_id": "2401.12345"}
        )
        is False
    )


# ── _conservative_enrich ─────────────────────────────────────────

def test_conservative_enrich_fills_missing_fields() -> None:
    """Empty current values are replaced by incoming."""
    existing = {"title": "Old", "doi": "", "year": None}
    incoming = {"title": "New", "doi": "10.1/bar", "year": 2025, "venue": "Nature"}
    result = _conservative_enrich(existing, incoming)
    assert result["doi"] == "10.1/bar"
    assert result["year"] == 2025
    assert result["venue"] == "Nature"
    # existing non-empty value is preserved
    assert result["title"] == "Old"


def test_conservative_enrich_skips_user_owned_fields() -> None:
    """Don't overwrite tags, local_pdf_path, citekey, note."""
    existing = {
        "tags": ["ml"],
        "local_pdf_path": "/papers/a.pdf",
        "citekey": "ck1",
        "note": "my note",
    }
    incoming = {
        "tags": ["cv"],
        "local_pdf_path": "/papers/b.pdf",
        "citekey": "ck2",
        "note": "new note",
    }
    result = _conservative_enrich(existing, incoming)
    assert result["tags"] == ["ml"]
    assert result["local_pdf_path"] == "/papers/a.pdf"
    assert result["citekey"] == "ck1"
    assert result["note"] == "my note"


def test_conservative_enrich_does_not_overwrite_non_empty() -> None:
    """Non-empty current values are preserved."""
    existing = {"title": "Preserved"}
    incoming = {"title": "Overwritten?"}
    result = _conservative_enrich(existing, incoming)
    assert result["title"] == "Preserved"


def test_conservative_enrich_none_updated() -> None:
    """None current value gets updated."""
    existing = {"title": None}  # type: ignore[dict-item]
    incoming = {"title": "Filled"}
    result = _conservative_enrich(existing, incoming)
    assert result["title"] == "Filled"


# ── _changed_fields / _changed_fields_for_candidate ──────────────

def test_changed_fields_returns_sorted_keys() -> None:
    """Only keys with different values are returned, sorted."""
    existing = {"a": 1, "b": 2, "c": 3}
    updated = {"a": 1, "b": 99, "c": 3, "d": 4}
    result = _changed_fields(existing, updated)
    assert result == ["b", "d"]


def test_changed_fields_empty() -> None:
    """No changes → empty list."""
    assert _changed_fields({"x": 1}, {"x": 1}) == []


def test_changed_fields_for_candidate_skips_user_owned() -> None:
    """User-owned fields shouldn't show up as changes."""
    existing = {"title": "T", "tags": ["a"], "citekey": "ck"}
    candidate = {"title": "T", "tags": ["b"], "citekey": "ck2", "venue": "Nature"}
    changed = _changed_fields_for_candidate(existing, candidate)
    assert "tags" not in changed
    assert "citekey" not in changed
    assert "venue" in changed


# ── update_bib integration ───────────────────────────────────────

def test_update_bib_lookup_failure_recorded(tmp_path: Path) -> None:
    """When fetch_search raises, the note records the error."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _failing_search(query: str, *, server_url: str) -> list[dict]:
        raise OSError("connection refused")

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_failing_search,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert result["items"][0]["applied"] is False
    assert "lookup failed" in result["items"][0]["note"]


def test_update_bib_value_error_lookup_recorded(tmp_path: Path) -> None:
    """ValueError from search is also caught."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "arxiv_id": "2401.12345",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _value_error_search(query: str, *, server_url: str) -> list[dict]:
        raise ValueError("bad input")

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_value_error_search,
    )
    assert "lookup failed" in result["items"][0]["note"]


def test_update_bib_no_results_skipped(tmp_path: Path) -> None:
    """When search returns empty, the record is skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
        },
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


def test_update_bib_no_changed_fields_skipped(tmp_path: Path) -> None:
    """When candidate has no new fields, record is skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "venue": "CVPR",
            "doi": "10.1/foo",
            "year": 2024,
            "authors": ["Smith, Jane"],
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "CVPR",
                    "doi": "10.1/foo",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )
    assert len(result["items"]) == 0


def test_update_bib_applies_changes_when_not_dry_run(tmp_path: Path) -> None:
    """Dry-run=False actually writes to the bib file."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "venue": "NeurIPS",
                    "doi": "10.9/neu",
                    "year": 2024,
                },
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert result["items"][0]["applied"] is True
    assert "venue" in result["items"][0]["changed_fields"]
    assert "doi" in result["items"][0]["changed_fields"]
    text = bib_path.read_text()
    assert "journal = {NeurIPS}" in text
    assert "doi = {10.9/neu}" in text


def test_update_bib_conservative_enrich_respects_existing_values(
    tmp_path: Path,
) -> None:
    """Existing non-empty fields are not overwritten."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "My Title",
            "year": 2024,
            "arxiv_id": "2401.12345",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Different Title",
                    "venue": "NeurIPS",
                    "year": 2025,
                },
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )
    assert result["status"] == "ok"
    text = bib_path.read_text()
    # Title was already present → conservative enrichment keeps it
    assert "My Title" in text


def test_update_bib_config_error(tmp_path: Path) -> None:
    """Config loading errors propagate."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
    )
    assert result["status"] == "error"
    assert len(result["errors"]) > 0


def test_update_bib_ambigous_selection(tmp_path: Path) -> None:
    """Multiple bibs without explicit selector."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[bibs]]
name = "ml"
path = "/tmp/a.bib"

[[bibs]]
name = "systems"
path = "/tmp/b.bib"
""".strip()
    )

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
    )
    assert result["status"] == "error"
    assert len(result["errors"]) > 0


def test_update_bib_record_without_citekey_skipped(tmp_path: Path) -> None:
    """Records missing citekey are silently skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text("@article{},\n")

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=lambda q, **kw: [],
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 0


def test_update_bib_published_record_not_enriched(tmp_path: Path) -> None:
    """A fully-published record (venue + doi + year) is skipped by _needs_update."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "venue": "CVPR",
            "doi": "10.1/foo",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    search_called = []

    def _search(query: str, *, server_url: str) -> list[dict]:
        search_called.append(query)
        return []

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_search,
    )
    # Published record → _needs_update returns False → no search
    assert search_called == []
    assert len(result["items"]) == 0


def test_update_bib_entry_disappeared_during_update(tmp_path: Path) -> None:
    """If update_bib_entry can't find the entry, note is set."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    # First, we'll mutate the bib to remove the entry after initial read
    # The update_bib reads records first, then later calls update_bib_entry.
    # We need to simulate this race by: removing the entry's citekey before the update.

    bib_path.read_text()

    def _search(query: str, *, server_url: str) -> list[dict]:
        # Remove the entry right before update_bib_entry would be called
        bib_path.write_text("@article{unrelated,\n  title = {Other}\n}\n")
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "venue": "NeurIPS",
                    "doi": "10.9/neu",
                    "year": 2024,
                },
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )
    assert len(result["items"]) == 1
    assert "disappeared" in result["items"][0]["note"]


def test_update_bib_record_without_query_skipped(tmp_path: Path) -> None:
    """Records without doi, arxiv_id, or title produce no query → skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "bogus",
            "venue": "",
            "year": 2025,
        },
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


def test_update_bib_change_field_list_dry_run_vs_applied(tmp_path: Path) -> None:
    """Dry-run reports changed_fields but applied=False."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {"venue": "X", "doi": "10.1/x", "year": 2024},
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_search,
    )
    assert len(result["items"]) == 1
    assert not result["items"][0]["applied"]
    assert len(result["items"][0]["changed_fields"]) >= 1


def test_update_bib_dry_run_includes_preserved_source_diff(tmp_path: Path) -> None:
    """Update dry-run reports exact preserve-source diff and leaves file unchanged."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        "% keep me\n"
        "@article{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  eprint = {2401.12345}\n"
        "}\n"
    )
    before = bib_path.read_text()

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {"venue": "NeurIPS", "doi": "10.1/neu", "year": 2024},
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_search,
    )

    assert bib_path.read_text() == before
    item = result["items"][0]
    assert item["applied"] is False
    assert item["diff"].startswith(f"--- {bib_path} (before)\n")
    assert "+  journal = {NeurIPS}" in item["diff"]
    assert "+  doi = {10.1/neu}" in item["diff"]


def test_update_bib_uses_best_translation_result_not_first(tmp_path: Path) -> None:
    """Update picks best metadata candidate by pure score, not result order."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1234/right",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "record": {
                    "doi": "10.1234/wrong",
                    "venue": "Wrong Venue",
                    "year": 2023,
                }
            },
            {
                "record": {
                    "doi": "10.1234/right",
                    "venue": "Right Venue",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                }
            },
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )

    assert result["status"] == "ok"
    item = result["items"][0]
    assert item["metadata_diagnostics"][0].startswith("selected result 2/")
    assert "doi=10.1234/right" in item["metadata_diagnostics"][0]


def test_update_bib_reports_low_confidence_metadata_warning(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1234/right",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {"doi": "10.1234/wrong", "venue": "Wrong Venue", "year": 2024},
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_search,
    )

    assert result["items"][0]["metadata_warnings"] == [
        "metadata confidence low: selected result score=-41 below 0; verify captured metadata"
    ]


def test_update_bib_uses_configured_metadata_confidence_threshold(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
metadata_confidence_min_score = 60

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1234/right",
        },
        bib_selector=None,
        dry_run=False,
    )

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {"doi": "10.1234/right", "venue": "Venue", "year": 2024},
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_search,
    )

    assert result["items"][0]["metadata_warnings"] == [
        "metadata confidence low: selected result score=59 below 60; verify captured metadata"
    ]

def test_update_bib_unexpected_error_isolated_per_record(tmp_path: Path) -> None:
    """A non-OSError/ValueError failure on one record must not abort the pass.

    Only the lookup *call* is narrowly caught inside the helper; anything else
    (here a RuntimeError) propagates to the loop's per-record guard, which
    records an "update failed" item and lets later records still enrich.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    for citekey, arxiv in [("alpha2024", "2401.00001"), ("beta2024", "2401.00002")]:
        add_record_to_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record={"citekey": citekey, "title": citekey, "arxiv_id": arxiv},
            bib_selector=None,
            dry_run=False,
        )

    def _search(query: str, *, server_url: str) -> list[dict]:
        if query == "2401.00001":
            raise RuntimeError("kaboom")  # not OSError/ValueError
        return [
            {
                "item_type": "journalArticle",
                "record": {"venue": "NeurIPS", "doi": "10.9/beta", "year": 2024},
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )

    assert result["status"] == "ok"
    by_key = {item["citekey"]: item for item in result["items"]}
    assert "update failed" in by_key["alpha2024"]["note"]
    assert by_key["alpha2024"]["applied"] is False
    # The second record still enriched despite the first record blowing up.
    assert by_key["beta2024"]["applied"] is True
    assert "doi = {10.9/beta}" in bib_path.read_text()


# ── from test_update_final.py ──

"""Edge tests for update_service.py uncovered lines (53, 80, 94, 104).
Covers: empty venue string, arxiv_id without year, record with year but
no venue/doi, arxiv_id present with no doi, etc.
"""

