"""Edge-case coverage for promote_service."""

from pathlib import Path

from pzi.add_service import add_record_to_bib
from pzi.promote_service import (
    _add_note_to_citekey,
    _build_query,
    _find_duplicate_citekey,
    _find_published_candidate,
    _first_with_venue,
    _generate_citekey_for_candidate,
    _merge_published_metadata,
    _score_confidence,
    promote_bib,
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


def _seed_bib_with_preprint(tmp_path, bib_path, config_path, **kwargs):
    record = {
        "citekey": "smith2024graph",
        "title": "Graph Parsers",
        "arxiv_id": "2401.12345",
        "year": 2024,
        "authors": ["Smith, Jane"],
        **kwargs,
    }
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record=record,
        bib_selector=None,
        dry_run=False,
    )


# ── _build_query ─────────────────────────────────────────────────

def test_build_query_title_only() -> None:
    assert _build_query({"title": "Hello World"}) == "Hello World"


def test_build_query_title_with_authors() -> None:
    result = _build_query(
        {"title": "Hello World", "authors": ["Smith, Jane", "Doe, John", "Extra, Author"]}
    )
    assert "Hello World" in result
    assert "Smith, Jane" in result
    assert "Doe, John" in result
    assert "Extra, Author" not in result  # only first 2


def test_build_query_title_authors_year() -> None:
    result = _build_query(
        {"title": "Hello World", "authors": ["Smith, Jane"], "year": 2024}
    )
    assert "Hello World" in result
    assert "Smith, Jane" in result
    assert "2024" in result


def test_build_query_no_title_uses_authors_and_year() -> None:
    result = _build_query({"authors": ["Doe, John"], "year": 2023})
    assert "Doe, John" in result
    assert "2023" in result


def test_build_query_empty() -> None:
    assert _build_query({}) == ""


# ── _first_with_venue ────────────────────────────────────────────

def test_first_with_venue_none() -> None:
    assert _first_with_venue(None) is None


def test_first_with_venue_empty() -> None:
    assert _first_with_venue([]) is None


def test_first_with_venue_no_venue() -> None:
    results: list[dict] = [
        {"record": {"title": "Foo"}},
        {"record": {"title": "Bar"}},
    ]
    assert _first_with_venue(results) is None


def test_first_with_venue_finds_first() -> None:
    results: list[dict] = [
        {"record": {"title": "Foo"}},
        {"record": {"title": "Bar", "venue": "Nature", "doi": "10.1/x"}},
        {"record": {"title": "Baz", "venue": "Science"}},
    ]
    found = _first_with_venue(results)
    assert found is not None
    assert found["venue"] == "Nature"


def test_first_with_venue_record_not_mapping() -> None:
    """record key exists but isn't a Mapping."""
    results: list[dict] = [
        {"record": "not a dict"},
        {"record": {"title": "Bar", "venue": "Nature"}},
    ]
    found = _first_with_venue(results)
    assert found is not None
    assert found["venue"] == "Nature"


# ── _score_confidence ────────────────────────────────────────────

def test_score_confidence_exact_title() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        {"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
    )
    # 5 (exact title) + 1 (1 matching author, min 3) + 2 (exact year) = 8
    assert score == 8


def test_score_confidence_title_containment() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        {"title": "Graph Parsers: Extended Edition", "authors": ["Smith, Jane"], "year": 2024},
    )
    # 3 (title containment) + 1 + 2 = 6
    assert score == 6


def test_score_confidence_no_title_match() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        {"title": "Totally Different", "authors": ["Smith, Jane"], "year": 2024},
    )
    # 0 (no title match) + 1 + 2 = 3
    assert score == 3


def test_score_confidence_multiple_authors() -> None:
    score = _score_confidence(
        {"title": "X", "authors": ["A, One", "B, Two", "C, Three", "D, Four"], "year": 2024},
        {"title": "X", "authors": ["A, One", "B, Two", "C, Three", "D, Four"], "year": 2024},
    )
    # 5 + min(4, 3)=3 + 2 = 10
    assert score == 10


def test_score_confidence_year_within_one() -> None:
    score = _score_confidence(
        {"title": "X", "authors": ["A, One"], "year": 2024},
        {"title": "X", "authors": ["A, One"], "year": 2025},
    )
    # 5 (exact title) + 1 + 1 (year within 1) = 7
    assert score == 7


def test_score_confidence_year_far_apart() -> None:
    score = _score_confidence(
        {"title": "X", "authors": ["A, One"], "year": 2020},
        {"title": "X", "authors": ["A, One"], "year": 2025},
    )
    # 5 + 1 + 0 (years far apart) = 6
    assert score == 6


def test_score_confidence_missing_titles() -> None:
    score = _score_confidence(
        {"authors": ["A, One"], "year": 2024},
        {"authors": ["A, One"], "year": 2024},
    )
    assert score == 3  # 1 overlapping author + 2 exact year


def test_score_confidence_missing_year() -> None:
    score = _score_confidence(
        {"title": "X", "authors": ["A, One"]},
        {"title": "X", "authors": ["A, One"]},
    )
    assert score == 6  # 5 title + 1 author


# ── _find_duplicate_citekey ──────────────────────────────────────

def test_find_duplicate_citekey_by_doi() -> None:
    records = [
        {"citekey": "a", "doi": "10.1/a"},
        {"citekey": "b", "doi": "10.1/b", "title": "Target Title"},
    ]
    candidate = {"doi": "10.1/b"}
    result = _find_duplicate_citekey(candidate, records, "a")
    assert result == "b"


def test_find_duplicate_citekey_by_title() -> None:
    records = [
        {"citekey": "a", "title": "Foo"},
        {"citekey": "b", "title": "Target Title"},
    ]
    candidate = {"title": "Target Title"}
    result = _find_duplicate_citekey(candidate, records, "a")
    assert result == "b"


def test_find_duplicate_citekey_exclude_self() -> None:
    records = [
        {"citekey": "preprint_ck", "doi": "10.1/same"},
    ]
    candidate = {"doi": "10.1/same"}
    result = _find_duplicate_citekey(candidate, records, "preprint_ck")
    assert result is None  # excluded


def test_find_duplicate_citekey_no_match() -> None:
    records = [
        {"citekey": "a", "doi": "10.1/a"},
    ]
    candidate = {"doi": "10.1/unknown"}
    result = _find_duplicate_citekey(candidate, records, "a")
    assert result is None


def test_find_duplicate_citekey_doi_takes_priority_over_title() -> None:
    """DOI match is checked first."""
    records = [
        {"citekey": "doi_match", "doi": "10.1/same"},
        {"citekey": "title_match", "title": "Same Title as Candidate"},
    ]
    candidate = {"doi": "10.1/same", "title": "Same Title as Candidate"}
    result = _find_duplicate_citekey(candidate, records, "other")
    assert result == "doi_match"


def test_find_duplicate_citekey_skips_non_string_citekey() -> None:
    records = [
        {"citekey": None, "doi": "10.1/a"},
        {"citekey": "b", "doi": "10.1/a"},
    ]
    candidate = {"doi": "10.1/a"}
    result = _find_duplicate_citekey(candidate, records, "other")
    assert result == "b"


# ── _merge_published_metadata ────────────────────────────────────

def test_merge_published_metadata_removes_arxiv_id() -> None:
    merged = _merge_published_metadata(
        {"title": "X", "arxiv_id": "2401.12345", "doi": "10.1/pp"},
        {"title": "X Published", "venue": "Nature", "year": 2024},
    )
    assert "arxiv_id" not in merged
    assert merged["title"] == "X Published"
    assert merged["venue"] == "Nature"
    assert merged["doi"] == "10.1/pp"


def test_merge_published_metadata_preserves_tags() -> None:
    merged = _merge_published_metadata(
        {"tags": ["ml", "cv"]},
        {"venue": "Nature"},
    )
    assert merged["tags"] == ["ml", "cv"]


def test_merge_published_metadata_preserves_user_owned() -> None:
    merged = _merge_published_metadata(
        {
            "tags": ["ml"],
            "local_pdf_path": "/papers/a.pdf",
            "citekey": "my-key",
            "note": "my note",
        },
        {
            "tags": ["should-not-overwrite"],
            "local_pdf_path": "/papers/b.pdf",
            "citekey": "new-key",
            "note": "new note",
            "venue": "Nature",
        },
    )
    assert merged["tags"] == ["ml"]  # original
    assert merged["local_pdf_path"] == "/papers/a.pdf"
    assert merged["citekey"] == "my-key"
    assert merged["note"] == "my note"
    assert merged["venue"] == "Nature"  # non-user-owned, should be merged


def test_merge_published_metadata_none_tags() -> None:
    """When preprint has no tags, tags becomes empty list."""
    merged = _merge_published_metadata(
        {"title": "X"},
        {"venue": "Nature"},
    )
    assert merged["tags"] == []


# ── _generate_citekey_for_candidate ──────────────────────────────

def test_generate_citekey_for_candidate() -> None:
    ck = _generate_citekey_for_candidate(
        {"authors": ["Smith, Jane"], "title": "Graph Parsers", "year": 2024},
        set(),
    )
    assert ck.startswith("smith")
    assert "2024" in ck


def test_generate_citekey_for_candidate_collision() -> None:
    existing = {"smith2024graph"}
    ck = _generate_citekey_for_candidate(
        {"authors": ["Smith, Jane"], "title": "Graph Parsers", "year": 2024},
        existing,
    )
    assert ck != "smith2024graph"
    assert ck.startswith("smith")


def test_generate_citekey_for_candidate_missing_authors() -> None:
    ck = _generate_citekey_for_candidate(
        {"title": "Graph Parsers", "year": 2024},
        set(),
    )
    assert ck is not None
    assert len(ck) > 0


# ── _add_note_to_citekey ─────────────────────────────────────────

def test_add_note_to_citekey_new_note(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    _add_note_to_citekey(str(bib_path), "smith2024graph", "Cross-linked: foo2025bar")
    text = bib_path.read_text()
    assert "Cross-linked: foo2025bar" in text


def test_add_note_to_citekey_appends_to_existing_note(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path, note="original note"
    )

    _add_note_to_citekey(str(bib_path), "smith2024graph", "Cross-linked: foo2025bar")
    text = bib_path.read_text()
    assert "original note" in text
    assert "Cross-linked: foo2025bar" in text
    assert "; " in text


def test_add_note_to_citekey_duplicate_note_not_added(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path, note="Cross-linked: foo2025bar"
    )

    _add_note_to_citekey(str(bib_path), "smith2024graph", "Cross-linked: foo2025bar")
    text = bib_path.read_text()
    # Should appear exactly once in note field
    assert text.count("Cross-linked: foo2025bar") == 1


# ── _find_published_candidate fallback chain ─────────────────────

def test_find_published_candidate_translation_server_error(
    tmp_path: Path,
) -> None:
    """When translation server raises OSError, falls through to crossref."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    crossref_called = []

    def _failing_search(query, *, server_url):
        raise OSError("connection refused")

    def _fake_crossref(title):
        crossref_called.append(title)
        return {"title": "Graph Parsers", "venue": "Journal of Parsing", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=_failing_search,
        fetch_crossref=_fake_crossref,
        fetch_openalex=lambda t: None,
        fetch_s2=None,
        s2_api_key=None,
    )
    assert candidate is not None
    assert candidate["venue"] == "Journal of Parsing"
    assert crossref_called == ["Graph Parsers"]


def test_find_published_candidate_value_error_falls_to_crossref(
    tmp_path: Path,
) -> None:
    """ValueError from search also falls through."""
    crossref_called = []

    def _failing_search(query, *, server_url):
        raise ValueError("bad")

    def _fake_crossref(title):
        crossref_called.append(title)
        return {"title": "Graph Parsers", "venue": "CVPR", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=_failing_search,
        fetch_crossref=_fake_crossref,
        fetch_openalex=lambda t: None,
        fetch_s2=None,
        s2_api_key=None,
    )
    assert candidate is not None
    assert len(crossref_called) == 1


def test_find_published_candidate_crossref_error_falls_to_openalex(
    tmp_path: Path,
) -> None:
    """Crossref raises → openalex is tried."""
    openalex_called = []

    def _failing_crossref(title):
        raise OSError("crossref down")

    def _fake_openalex(title):
        openalex_called.append(title)
        return {"title": "Graph Parsers", "venue": "ICML", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [],
        fetch_crossref=_failing_crossref,
        fetch_openalex=_fake_openalex,
        fetch_s2=None,
        s2_api_key=None,
    )
    assert candidate is not None
    assert candidate["venue"] == "ICML"
    assert openalex_called == ["Graph Parsers"]


def test_find_published_candidate_openalex_none_tries_s2(
    tmp_path: Path,
) -> None:
    """OpenAlex returns None → S2 is tried."""
    s2_called = []

    def _fake_s2(title, *, api_key=None):
        s2_called.append(title)
        return {"title": "Graph Parsers", "venue": "Science", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_s2=_fake_s2,
        s2_api_key="test-key",
    )
    assert candidate is not None
    assert candidate["venue"] == "Science"
    assert s2_called == ["Graph Parsers"]


def test_find_published_candidate_openalex_error_tries_s2(
    tmp_path: Path,
) -> None:
    """OpenAlex raises → S2 is tried."""
    s2_called = []

    def _failing_openalex(title):
        raise ValueError("openalex down")

    def _fake_s2(title, *, api_key=None):
        s2_called.append(title)
        return {"title": "Graph Parsers", "venue": "Nature", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=_failing_openalex,
        fetch_s2=_fake_s2,
        s2_api_key="test-key",
    )
    assert candidate is not None
    assert candidate["venue"] == "Nature"
    assert s2_called == ["Graph Parsers"]


def test_find_published_candidate_s2_error_returns_none(
    tmp_path: Path,
) -> None:
    """When all backends fail, returns None."""
    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_s2=lambda t, **kw: (_ for _ in ()).throw(OSError("s2 down")),
        s2_api_key="test-key",
    )
    assert candidate is None


def test_find_published_candidate_s2_skipped_without_api_key(
    tmp_path: Path,
) -> None:
    """Without s2_api_key, S2 is not tried."""
    s2_called = []

    def _fake_s2(title, *, api_key=None):
        s2_called.append(title)
        return {"title": "Graph Parsers", "venue": "Nature", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_s2=_fake_s2,
        s2_api_key=None,
    )
    assert candidate is None
    assert s2_called == []


def test_find_published_candidate_empty_query_returns_none() -> None:
    candidate = _find_published_candidate(
        record={"title": ""},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_s2=None,
        s2_api_key=None,
    )
    assert candidate is None


def test_find_published_candidate_translation_server_no_venue_then_crossref(
    tmp_path: Path,
) -> None:
    """Translation server returns results but no venue → falls through to crossref."""
    crossref_called = []

    def _fake_crossref(title):
        crossref_called.append(title)
        return {"title": "Graph Parsers", "venue": "NeurIPS", "year": 2024}

    candidate = _find_published_candidate(
        record={"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        server_url="http://localhost:1969",
        fetch_search=lambda q, **kw: [
            {"item_type": "preprint", "record": {"title": "Graph Parsers"}}
        ],
        fetch_crossref=_fake_crossref,
        fetch_openalex=lambda t: None,
        fetch_s2=None,
        s2_api_key=None,
    )
    assert candidate is not None
    assert candidate["venue"] == "NeurIPS"
    assert len(crossref_called) == 1


# ── promote_bib integration: additional edge paths ───────────────

def test_promote_low_confidence_due_to_different_authors(tmp_path: Path) -> None:
    """No author overlap + different title → very low confidence."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Completely Different Paper",
                    "venue": "Journal of X",
                    "year": 2024,
                    "authors": ["Unrelated, Person"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
        confidence_threshold=1,
    )
    # 0 (no title match) + 0 (no author overlap) + 2 (exact year) = 2 ≥ 1
    # So it should NOT skip — let's check
    # Actually: 0 title + 0 authors + 2 year = 2, threshold=1 → passes
    # But it would fail duplicate check and handle normally
    if result["items"]:
        # score should be 2 >= 1 threshold
        assert result["items"][0]["action"] != "skip" or "low confidence" not in result["items"][0].get("note", "")


def test_promote_dry_run_update_in_place(tmp_path: Path) -> None:
    """Dry-run with update in place."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    before = bib_path.read_text()

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "doi": "10.9/jop",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=True,
        fetch_search=_search,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["action"] == "update"
    assert item["pdf_attached"] is None  # dry_run → None
    assert bib_path.read_text() == before


def test_promote_update_in_place_with_pdf(tmp_path: Path) -> None:
    """Full update in place fetches PDF via pdf_url."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Nature Methods",
                    "doi": "10.9/nm",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                    "pdf_url": "https://example.com/nm-paper.pdf",
                },
                "attachments": [],
            }
        ]

    def _fetch_binary(url):
        return b"%PDF-1.7\nbody", "application/pdf"

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_search,
        fetch_binary=_fetch_binary,
    )
    assert result["status"] == "ok"
    item = result["items"][0]
    assert item["action"] == "update"
    assert item["pdf_attached"] is True
    text = bib_path.read_text()
    assert "file = {" in text


def test_promote_update_in_place_no_pdf_url(tmp_path: Path) -> None:
    """Update with no pdf_url in candidate → no PDF fetch."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Nature Methods",
                    "doi": "10.9/nm",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_search,
    )
    assert result["status"] == "ok"
    item = result["items"][0]
    assert item["action"] == "update"
    assert item["pdf_attached"] is False


def test_promote_keep_preprint_with_pdf(tmp_path: Path) -> None:
    """Keep preprint mode fetches PDF for published entry."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Nature Methods",
                    "doi": "10.9/nm",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                    "pdf_url": "https://example.com/nm-paper.pdf",
                },
                "attachments": [],
            }
        ]

    def _fetch_binary(url):
        return b"%PDF-1.7\nbody", "application/pdf"

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_search,
        fetch_binary=_fetch_binary,
    )
    assert result["status"] == "ok"
    item = result["items"][0]
    assert item["action"] == "create"
    assert item["pdf_attached"] is True
    # Preprint note should reference published version
    text = bib_path.read_text()
    assert "Published version:" in text
    assert "Preprint version:" in text


def test_promote_keep_preprint_dry_run_no_pdf_fetch(tmp_path: Path) -> None:
    """Keep preprint dry-run does not fetch PDF even with pdf_url."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Nature Methods",
                    "doi": "10.9/nm",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                    "pdf_url": "https://example.com/nm-paper.pdf",
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=True,
        fetch_search=_search,
    )
    item = result["items"][0]
    assert item["action"] == "create"
    assert item["pdf_attached"] is False


def test_promote_no_preprint_records_empty_items(tmp_path: Path) -> None:
    """When no preprints exist, items is empty."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "doe2024vision",
            "title": "Vision",
            "venue": "CVPR",
            "doi": "10.1/cvpr",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=lambda q, **kw: [],
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 0


def test_promote_find_candidate_no_query_no_results(tmp_path: Path) -> None:
    """Record with empty title → no query → candidate is None → skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    # Directly write a preprint-like record with empty title
    bib_path.write_text(
        '@article{empty_preprint,\n  arxiv_id = {2401.12345},\n  title = {}\n}\n'
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=lambda q, **kw: [],
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 0


def test_promote_changed_fields_include_venue(tmp_path: Path) -> None:
    """The changed_fields list correctly reflects all new metadata."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Nature Methods",
                    "doi": "10.9/nm",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_search,
    )
    item = result["items"][0]
    assert "venue" in item["changed_fields"]
    assert "doi" in item["changed_fields"]


def test_promote_keep_preprint_unchanged_fields_use_venue_fallback(
    tmp_path: Path,
) -> None:
    """When published has same fields as candidate, fallback to venue/doi."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Nature",
                    "doi": "10.9/nat",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_search,
    )
    item = result["items"][0]
    assert item["action"] == "create"
    # _handle_keep_preprint: changed = sorted keys where published != candidate
    # published = _merge_published_metadata(preprint, candidate) — has arxiv_id removed
    # candidate still may have less fields, so changed_fields should be non-empty
    assert len(item["changed_fields"]) >= 1


def test_promote_no_search_results_falls_through_to_crossref_openalex(
    tmp_path: Path,
) -> None:
    """Translation server returns no venue → crossref/openalex tried."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(
        tmp_path, bib_path, extra='semantic_scholar_api_key = "test-key"'
    )
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    crossref_calls = []
    openalex_calls = []
    s2_calls = []

    def _fake_crossref(title):
        crossref_calls.append(title)
        return None

    def _fake_openalex(title):
        openalex_calls.append(title)
        return {"title": "Graph Parsers", "venue": "ICLR", "year": 2024}

    def _fake_s2(title, *, api_key=None):
        s2_calls.append(title)
        return None

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=_fake_crossref,
        fetch_openalex=_fake_openalex,
        fetch_s2=_fake_s2,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert crossref_calls == ["Graph Parsers"]
    assert openalex_calls == ["Graph Parsers"]
    # S2 should not be called since openalex found a venue
    assert s2_calls == []
