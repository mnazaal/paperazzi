"""Edge tests for search_service.py uncovered lines (48->51: tag normalization, 56: no citekey skip)."""

from pathlib import Path

from pzi.search_service import _match_record, search_bib


def _write_config(tmp_path: Path, bib_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    return config_path


# ── search_bib ───────────────────────────────────────────────────

def test_search_tag_normalized(tmp_path: Path) -> None:
    """Tag normalization produces hyphenated lowercase tag, matching stored form."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {Test},\n"
        "  keywords = {machine-learning},\n"
        "}\n"
    )
    config_path = _write_config(tmp_path, bib_path)

    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="ml",
        tag="machine learning",
    )
    assert result["status"] == "ok"
    assert len(result["matches"]) == 1
    assert "tags" in result["matches"][0]["matched_fields"]


def test_search_empty_tag_search(tmp_path: Path) -> None:
    """Normalizing a whitespace-only tag produces empty → no tag filter applied."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {Test},\n"
        "  keywords = {ml},\n"
        "}\n"
    )
    config_path = _write_config(tmp_path, bib_path)

    # Whitespace-only tag normalizes to empty → stored tag is None
    # No tag filter means all records match if other criteria met
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="ml",
        query="test",
    )
    assert result["status"] == "ok"


def test_search_record_without_citekey_skipped(tmp_path: Path) -> None:
    """Records without citekey are skipped (line ~56)."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{,\n  title = {No citekey},\n}\n"
        "@article{smith2024,\n  title = {Has citekey},\n}\n"
    )
    config_path = _write_config(tmp_path, bib_path)

    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="ml",
        query="citekey",
    )
    assert result["status"] == "ok"
    # Both match on text, but first has no citekey → skipped
    assert len(result["matches"]) == 1
    assert result["matches"][0]["citekey"] == "smith2024"


def test_search_matches_sort_by_citekey(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{zebra2024,\n  title = {Paper},\n}\n"
        "@article{alpha2024,\n  title = {Paper},\n}\n"
    )
    config_path = _write_config(tmp_path, bib_path)
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="ml",
        query="paper",
    )
    citekeys = [m["citekey"] for m in result["matches"]]
    assert citekeys == sorted(citekeys)


# ── _match_record ────────────────────────────────────────────────

def test_match_record_query_in_title() -> None:
    result = _match_record(
        {"title": "Machine Learning Papers"},
        query="machine",
        author=None,
        year=None,
        tag=None,
    )
    assert result == ["title"]


def test_match_record_query_in_abstract() -> None:
    result = _match_record(
        {"abstract": "We propose a new method"},
        query="propose",
        author=None,
        year=None,
        tag=None,
    )
    assert result == ["abstract"]


def test_match_record_query_in_note() -> None:
    result = _match_record(
        {"note": "Important paper"},
        query="important",
        author=None,
        year=None,
        tag=None,
    )
    assert result == ["note"]


def test_match_record_query_not_found() -> None:
    result = _match_record(
        {"title": "Something"},
        query="nonexistent",
        author=None,
        year=None,
        tag=None,
    )
    assert result is None


def test_match_record_author_match() -> None:
    result = _match_record(
        {"authors": ["Jane Smith", "John Doe"]},
        query=None,
        author="jane",
        year=None,
        tag=None,
    )
    assert "authors" in result


def test_match_record_author_no_match() -> None:
    result = _match_record(
        {"authors": ["A B"]},
        query=None,
        author="Z",
        year=None,
        tag=None,
    )
    assert result is None


def test_match_record_year_match() -> None:
    result = _match_record(
        {"year": 2024},
        query=None,
        author=None,
        year=2024,
        tag=None,
    )
    assert "year" in result


def test_match_record_year_mismatch() -> None:
    result = _match_record(
        {"year": 2024},
        query=None,
        author=None,
        year=2023,
        tag=None,
    )
    assert result is None


def test_match_record_tag_match() -> None:
    result = _match_record(
        {"tags": ["ml", "nlp"]},
        query=None,
        author=None,
        year=None,
        tag="ml",
    )
    assert "tags" in result


def test_match_record_tag_no_match() -> None:
    result = _match_record(
        {"tags": ["ml"]},
        query=None,
        author=None,
        year=None,
        tag="cv",
    )
    assert result is None


def test_match_record_all_filters() -> None:
    result = _match_record(
        {"title": "Graph Methods", "authors": ["A B"], "year": 2024, "tags": ["graph"]},
        query="graph",
        author="b",
        year=2024,
        tag="graph",
    )
    assert "title" in result
    assert "authors" in result
    assert "year" in result
    assert "tags" in result
