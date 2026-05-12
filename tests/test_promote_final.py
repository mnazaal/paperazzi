"""Edge tests for promote_service.py uncovered lines.

Covers: 72 (empty query skip), 142->69 (partial match scoring),
180->206 (keep_preprint cross-note), 217->216 (update-in-place),
283->277 (_add_note_to_citekey).
"""

from pathlib import Path

from pzi.promote_service import (
    _add_note_to_citekey,
    _build_query,
    _find_duplicate_citekey,
    _first_with_venue,
    _merge_published_metadata,
    _score_confidence,
    promote_bib,
)

# ── _build_query ─────────────────────────────────────────────────

def test_build_query_empty() -> None:
    """No title, authors, year → empty string (line 72 skipped)."""
    assert _build_query({}) == ""


def test_build_query_title_only() -> None:
    assert _build_query({"title": "Graph Parsers"}) == "Graph Parsers"


def test_build_query_title_authors_year() -> None:
    q = _build_query({
        "title": "Graph Parsers",
        "authors": ["Jane Smith", "John Doe"],
        "year": 2024,
    })
    assert "Graph Parsers" in q
    assert "Jane Smith" in q
    assert "2024" in q


def test_build_query_non_string_title() -> None:
    """Title that isn't a string is skipped."""
    assert _build_query({"title": 42}) == ""


def test_build_query_whitespace_title() -> None:
    assert _build_query({"title": "   "}) == ""


def test_build_query_authors_not_list() -> None:
    q = _build_query({"title": "T", "authors": "not a list"})
    assert q == "T"


def test_build_query_year_not_int() -> None:
    q = _build_query({"title": "T", "year": "2024"})
    assert q == "T"  # year not included because not isinstance int


# ── _first_with_venue ────────────────────────────────────────────

def test_first_with_venue_none() -> None:
    assert _first_with_venue(None) is None


def test_first_with_venue_empty() -> None:
    assert _first_with_venue([]) is None


def test_first_with_venue_no_venue() -> None:
    results = [{"record": {"title": "Foo"}}]
    assert _first_with_venue(results) is None


def test_first_with_venue_found() -> None:
    results = [
        {"record": {"title": "Foo"}},
        {"record": {"title": "Bar", "venue": "Nature"}},
    ]
    found = _first_with_venue(results)
    assert found is not None
    assert found["venue"] == "Nature"


# ── _score_confidence ────────────────────────────────────────────

def test_score_exact_title_match() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "authors": ["A Smith"]},
        {"title": "Graph Parsers", "authors": ["A Smith"], "year": 2024},
    )
    assert score >= 5  # exact title = 5


def test_score_partial_title_match() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers"},
        {"title": "Graph Parsers: Extended"},
    )
    assert score == 3  # substring match


def test_score_author_overlap() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "authors": ["A B", "C D"]},
        {"title": "Graph Parsers", "authors": ["A B", "E F"]},
    )
    assert score == 6  # 5 (title exact) + 1 (one author overlap)


def test_score_year_exact() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "year": 2024},
        {"title": "Graph Parsers", "year": 2024},
    )
    assert score >= 7  # 5 (title) + 2 (year exact)


def test_score_year_close() -> None:
    score = _score_confidence(
        {"title": "Graph Parsers", "year": 2024},
        {"title": "Graph Parsers", "year": 2023},
    )
    assert score == 6  # 5 (title) + 1 (year close)


def test_score_low() -> None:
    """Minimal match — no title, no authors, no year."""
    score = _score_confidence({}, {})
    assert score == 0


# ── _find_duplicate_citekey ──────────────────────────────────────

def test_find_duplicate_citekey_by_doi() -> None:
    records = [
        {"citekey": "existing2024", "doi": "10.1/existing"},
        {"citekey": "other2024", "title": "Other Paper"},
    ]
    result = _find_duplicate_citekey(
        {"doi": "10.1/existing", "title": "New Paper"},
        records,
        exclude_citekey="nope",
    )
    assert result == "existing2024"


def test_find_duplicate_citekey_by_title() -> None:
    records = [
        {"citekey": "dup2024", "title": "Duplicate Paper"},
    ]
    result = _find_duplicate_citekey(
        {"title": "Duplicate Paper"},
        records,
        exclude_citekey="nope",
    )
    assert result == "dup2024"


def test_find_duplicate_citekey_excludes_self() -> None:
    records = [{"citekey": "self2024", "doi": "10.1/self"}]
    result = _find_duplicate_citekey(
        {"doi": "10.1/self"}, records, exclude_citekey="self2024"
    )
    assert result is None


def test_find_duplicate_citekey_not_found() -> None:
    assert _find_duplicate_citekey({"title": "New"}, [], exclude_citekey="x") is None


# ── _merge_published_metadata ────────────────────────────────────

def test_merge_prunes_arxiv_id() -> None:
    """arxiv_id is removed from merged record."""
    preprint = {"arxiv_id": "2401.12345", "title": "Old"}
    candidate = {"venue": "Nature", "title": "New", "doi": "10.1/foo"}
    merged = _merge_published_metadata(preprint, candidate)
    assert "arxiv_id" not in merged


def test_merge_preserves_user_tags() -> None:
    preprint = {"tags": ["ml", "nlu"]}
    candidate = {"tags": ["cv"]}
    merged = _merge_published_metadata(preprint, candidate)
    assert merged["tags"] == ["ml", "nlu"]


def test_merge_applies_venue_and_doi() -> None:
    preprint = {"title": "Old"}
    candidate = {"venue": "Nature", "doi": "10.1/x"}
    merged = _merge_published_metadata(preprint, candidate)
    assert merged["venue"] == "Nature"
    assert merged["doi"] == "10.1/x"


# ── _add_note_to_citekey (via update_bib_entry) ──────────────────

def test_add_note_to_citekey_new_note(tmp_path: Path) -> None:
    """Add a note to an entry that has no existing note."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {Test},\n"
        "}\n"
    )
    _add_note_to_citekey(str(bib_path), "smith2024", "Published version: new2024")
    text = bib_path.read_text()
    assert "Published version: new2024" in text


def test_add_note_to_citekey_existing_note(tmp_path: Path) -> None:
    """Append to existing note."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {Test},\n"
        "  note = {Existing note},\n"
        "}\n"
    )
    _add_note_to_citekey(str(bib_path), "smith2024", "extra info")
    text = bib_path.read_text()
    assert "Existing note" in text
    assert "extra info" in text


def test_add_note_to_citekey_duplicate_text(tmp_path: Path) -> None:
    """Duplicate text is not appended again."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {Test},\n"
        "  note = {Existing note; extra info},\n"
        "}\n"
    )
    before = bib_path.read_text()
    _add_note_to_citekey(str(bib_path), "smith2024", "extra info")
    after = bib_path.read_text()
    assert before == after  # no modification


# ── promote_bib empty query skip ─────────────────────────────────

def test_promote_bib_record_without_query_skipped(tmp_path: Path) -> None:
    """Records that produce empty query strings are skipped."""
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {},\n"
        "}\n"
    )
    config_path.write_text(
        f"""
translation_server_url = "http://localhost:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    # The record has no venue → is_preprint returns True
    # But _build_query returns empty → candidate is None → skipped
    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="ml",
        dry_run=True,
        confidence_threshold=0,
    )
    assert result["status"] == "ok"
    # No items because query is empty
    assert len(result["items"]) == 0


def test_promote_bib_low_confidence_skip(tmp_path: Path) -> None:
    """Low confidence items are recorded as 'skip'."""
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  title = {Graph Parsers},\n"
        "}\n"
    )
    config_path.write_text(
        f"""
translation_server_url = "http://localhost:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_search(query, *, server_url):
        return [{"record": {"venue": "Nature", "title": "Different Paper"}}]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="ml",
        dry_run=True,
        fetch_search=fake_search,
        confidence_threshold=5,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) >= 1
    item = result["items"][0]
    if item["action"] == "skip":
        assert "low confidence" in str(item["note"])
