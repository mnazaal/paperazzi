import pytest

from pzi.add_service import add_record_to_bib
from pzi.search_service import search_bib


@pytest.fixture
def seeded_bib(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    records = [
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "authors": ["Smith, Jane", "Doe, John"],
            "year": 2024,
            "doi": "10.1/graph",
            "tags": ["graphs", "ml"],
            "abstract": "Parsing graphs is important for search.",
            "note": "Possibly similar to smith2023graph",
        },
        {
            "citekey": "doe2023vision",
            "title": "Vision Transformers",
            "authors": ["Doe, John"],
            "year": 2023,
            "doi": "10.1/vision",
            "tags": ["cv", "ml"],
            "abstract": "Transformers for vision tasks.",
        },
        {
            "citekey": "lee2022systems",
            "title": "Operating Systems",
            "authors": ["Lee, Alice"],
            "year": 2022,
            "tags": ["systems"],
        },
    ]
    for record in records:
        add_record_to_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record=record,
            bib_selector=None,
            dry_run=False,
        )
    return config_path, bib_path


def test_query_matches_title(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="Graph Parsers",
    )
    assert result["status"] == "ok"
    assert [m["citekey"] for m in result["matches"]] == ["smith2024graph"]
    assert "title" in result["matches"][0]["matched_fields"]

def test_query_matches_abstract(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="vision tasks",
    )
    assert [m["citekey"] for m in result["matches"]] == ["doe2023vision"]
    assert "abstract" in result["matches"][0]["matched_fields"]

def test_query_matches_note(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="smith2023graph",
    )
    assert [m["citekey"] for m in result["matches"]] == ["smith2024graph"]
    assert "note" in result["matches"][0]["matched_fields"]

def test_query_case_insensitive(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="VISION",
    )
    assert [m["citekey"] for m in result["matches"]] == ["doe2023vision"]

def test_query_no_match_returns_empty(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="quantum computing",
    )
    assert result["matches"] == []


def test_author_matches_single_author(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        author="Lee",
    )
    assert [m["citekey"] for m in result["matches"]] == ["lee2022systems"]
    assert "authors" in result["matches"][0]["matched_fields"]

def test_author_matches_multiple_records(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        author="Doe",
    )
    assert [m["citekey"] for m in result["matches"]] == [
        "doe2023vision",
        "smith2024graph",
    ]

def test_author_case_insensitive(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        author="jane",
    )
    assert [m["citekey"] for m in result["matches"]] == ["smith2024graph"]


def test_year_exact_match(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        year=2022,
    )
    assert [m["citekey"] for m in result["matches"]] == ["lee2022systems"]
    assert "year" in result["matches"][0]["matched_fields"]

def test_year_no_match(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        year=1999,
    )
    assert result["matches"] == []


def test_tag_exact_match(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        tag="graphs",
    )
    assert [m["citekey"] for m in result["matches"]] == ["smith2024graph"]

def test_tag_no_match(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        tag="nlp",
    )
    assert result["matches"] == []


def test_query_and_author_and(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="Transformers",
        author="Doe",
    )
    assert [m["citekey"] for m in result["matches"]] == ["doe2023vision"]
    assert set(result["matches"][0]["matched_fields"]) == {
        "title",
        "authors",
        "abstract",
    }

def test_author_and_year_no_overlap(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        author="Doe",
        year=2022,
    )
    assert result["matches"] == []

def test_author_and_year_overlap(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        author="Doe",
        year=2023,
    )
    assert [m["citekey"] for m in result["matches"]] == ["doe2023vision"]


def test_empty_bib(tmp_path):
    bib_path = tmp_path / "empty.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "empty"
path = "{bib_path}"
default = true
""".strip()
    )
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="anything",
    )
    assert result["matches"] == []


def test_bad_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="x",
    )
    assert result["status"] == "error"


def test_query_empty_string_treated_as_none(seeded_bib, tmp_path):
    config_path, _bib = seeded_bib
    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query="",
    )
    # Empty string query should match every record because "" in any string is True
    # But that's acceptable behavior for empty query
    assert len(result["matches"]) == 3
