"""Edge tests for write_plan.merge_entries covering previously uncovered branches.

Covers missing lines in merge_entries and _prefer_more_informative_text.
"""


from pzi.bib_repository import _prefer_more_informative_text, merge_entries

# ---------------------------------------------------------------------------
# merge_entries: tags edge cases
# ---------------------------------------------------------------------------


def test_merge_tags_existing_none_incoming_none() -> None:
    decision = merge_entries({}, {})
    # existing_tags = None → or [] → []
    # incoming_tags = None → or [] → []
    # merged_tags = sorted(set()) = [] → same as existing_tags ([] compared to [] is same)
    # Then: existing.get("tags") is not None → False (tags not in existing)
    # tags not added to merged since merged_tags == existing_tags
    assert decision["merged"] == {}


def test_merge_tags_existing_none_incoming_some() -> None:
    decision = merge_entries({}, {"tags": ["a", "b"]})
    assert decision["merged"]["tags"] == ["a", "b"]
    assert "tags" in decision["changed_fields"]


def test_merge_tags_existing_some_incoming_none() -> None:
    decision = merge_entries({"tags": ["a", "b"]}, {})
    assert decision["merged"]["tags"] == ["a", "b"]
    assert "tags" not in decision["changed_fields"]


def test_merge_tags_existing_empty_list() -> None:
    """Existing tags is [] → merged with incoming."""
    decision = merge_entries({"tags": []}, {"tags": ["x"]})
    assert decision["merged"]["tags"] == ["x"]
    assert "tags" in decision["changed_fields"]


def test_merge_tags_identical() -> None:
    """Tags identical → no change."""
    decision = merge_entries({"tags": ["a", "b"]}, {"tags": ["a", "b"]})
    assert decision["merged"]["tags"] == ["a", "b"]
    assert "tags" not in decision["changed_fields"]


def test_merge_tags_overlap() -> None:
    """Tags partially overlap → union sorted."""
    decision = merge_entries({"tags": ["a", "c"]}, {"tags": ["b", "c"]})
    assert decision["merged"]["tags"] == ["a", "b", "c"]
    assert "tags" in decision["changed_fields"]


# ---------------------------------------------------------------------------
# merge_entries: authors edge cases
# ---------------------------------------------------------------------------


def test_merge_authors_incoming_shorter() -> None:
    """Incoming has fewer authors → keep existing."""
    decision = merge_entries(
        {"authors": ["A", "B", "C"]},
        {"authors": ["A"]},
    )
    assert decision["merged"]["authors"] == ["A", "B", "C"]
    assert "authors" not in decision["changed_fields"]


def test_merge_authors_same_length() -> None:
    """Same length → existing kept (incoming not longer)."""
    decision = merge_entries(
        {"authors": ["A", "B"]},
        {"authors": ["C", "D"]},
    )
    assert decision["merged"]["authors"] == ["A", "B"]
    assert "authors" not in decision["changed_fields"]


def test_merge_authors_existing_none() -> None:
    """Existing has no authors key → takes incoming."""
    decision = merge_entries({}, {"authors": ["A", "B"]})
    assert decision["merged"]["authors"] == ["A", "B"]
    assert "authors" in decision["changed_fields"]


def test_merge_authors_existing_empty() -> None:
    """Existing authors is [] (falsy) → or [] → [], incoming longer → takes incoming."""
    decision = merge_entries({"authors": []}, {"authors": ["A"]})
    assert decision["merged"]["authors"] == ["A"]
    assert "authors" in decision["changed_fields"]


def test_merge_authors_both_empty() -> None:
    """Both empty → no change."""
    decision = merge_entries({"authors": []}, {"authors": []})
    assert decision["merged"]["authors"] == []
    assert "authors" not in decision["changed_fields"]


def test_merge_authors_incoming_none() -> None:
    """Incoming has no authors key → keep existing."""
    decision = merge_entries({"authors": ["A"]}, {})
    assert decision["merged"]["authors"] == ["A"]
    assert "authors" not in decision["changed_fields"]


# ---------------------------------------------------------------------------
# merge_entries: year edge cases
# ---------------------------------------------------------------------------


def test_merge_year_existing_none() -> None:
    """Existing year is None (or missing) → takes incoming."""
    decision = merge_entries({}, {"year": 2024})
    assert decision["merged"]["year"] == 2024
    assert "year" in decision["changed_fields"]


def test_merge_year_both_present() -> None:
    """Both have year → keep existing."""
    decision = merge_entries({"year": 2024}, {"year": 2023})
    assert decision["merged"]["year"] == 2024
    assert "year" not in decision["changed_fields"]


def test_merge_year_existing_present_incoming_none() -> None:
    """Existing has year, incoming has no year key → keep existing."""
    decision = merge_entries({"year": 2024}, {})
    assert decision["merged"]["year"] == 2024
    assert "year" not in decision["changed_fields"]


def test_merge_year_both_none() -> None:
    """Neither has year → merged_dict.get('year') is None."""
    decision = merge_entries({}, {})
    assert "year" not in decision["merged"]


def test_merge_year_existing_explicitly_none() -> None:
    """Existing year explicitly None → takes incoming."""
    decision = merge_entries({"year": None}, {"year": 2024})
    assert decision["merged"]["year"] == 2024


# ---------------------------------------------------------------------------
# merge_entries: PREFER_LONGER_TEXT_FIELDS (title, venue, note, abstract)
# ---------------------------------------------------------------------------


def test_merge_title_existing_none() -> None:
    decision = merge_entries({}, {"title": "New Title"})
    assert decision["merged"]["title"] == "New Title"
    assert "title" in decision["changed_fields"]


def test_merge_title_existing_empty() -> None:
    decision = merge_entries({"title": ""}, {"title": "New Title"})
    assert decision["merged"]["title"] == "New Title"
    assert "title" in decision["changed_fields"]


def test_merge_title_existing_whitespace() -> None:
    decision = merge_entries({"title": "   "}, {"title": "New Title"})
    assert decision["merged"]["title"] == "New Title"
    assert "title" in decision["changed_fields"]


def test_merge_title_incoming_none() -> None:
    decision = merge_entries({"title": "Existing Title"}, {})
    assert decision["merged"]["title"] == "Existing Title"
    assert "title" not in decision["changed_fields"]


def test_merge_title_incoming_empty() -> None:
    decision = merge_entries({"title": "Existing Title"}, {"title": ""})
    assert decision["merged"]["title"] == "Existing Title"
    assert "title" not in decision["changed_fields"]


def test_merge_title_incoming_longer() -> None:
    decision = merge_entries(
        {"title": "Short"},
        {"title": "A Much Longer Title Here"},
    )
    assert decision["merged"]["title"] == "A Much Longer Title Here"
    assert "title" in decision["changed_fields"]


def test_merge_title_existing_longer() -> None:
    decision = merge_entries(
        {"title": "A Much Longer Existing Title Here"},
        {"title": "Short"},
    )
    assert decision["merged"]["title"] == "A Much Longer Existing Title Here"
    assert "title" not in decision["changed_fields"]


def test_merge_title_same_length_different_content() -> None:
    """Same length after strip → keeps existing."""
    decision = merge_entries(
        {"title": "ABCD"},
        {"title": "WXYZ"},
    )
    # incoming is not longer → keeps existing
    assert decision["merged"]["title"] == "ABCD"
    assert "title" not in decision["changed_fields"]


def test_merge_venue_existing_none() -> None:
    decision = merge_entries({}, {"venue": "Nature"})
    assert decision["merged"]["venue"] == "Nature"
    assert "venue" in decision["changed_fields"]


def test_merge_note_incoming_longer() -> None:
    decision = merge_entries(
        {"note": "Short note"},
        {"note": "A much longer and more detailed note here"},
    )
    assert decision["merged"]["note"] == "A much longer and more detailed note here"
    assert "note" in decision["changed_fields"]


def test_merge_abstract_existing_none() -> None:
    decision = merge_entries({}, {"abstract": "An abstract"})
    assert decision["merged"]["abstract"] == "An abstract"
    assert "abstract" in decision["changed_fields"]


def test_merge_both_none_field() -> None:
    """Both existing and incoming have None for a text field → None stays."""
    decision = merge_entries({"title": None}, {"title": None})
    assert decision["merged"]["title"] is None
    assert "title" not in decision["changed_fields"]


# ---------------------------------------------------------------------------
# merge_entries: FILL_IF_MISSING_FIELDS
# ---------------------------------------------------------------------------


def test_fill_doi_existing_none() -> None:
    decision = merge_entries({"doi": None}, {"doi": "10.1/foo"})
    assert decision["merged"]["doi"] == "10.1/foo"
    assert "doi" in decision["changed_fields"]


def test_fill_doi_existing_empty() -> None:
    decision = merge_entries({"doi": ""}, {"doi": "10.1/foo"})
    assert decision["merged"]["doi"] == "10.1/foo"
    assert "doi" in decision["changed_fields"]


def test_fill_doi_existing_present() -> None:
    decision = merge_entries({"doi": "10.1/existing"}, {"doi": "10.1/new"})
    assert decision["merged"]["doi"] == "10.1/existing"
    assert "doi" not in decision["changed_fields"]


def test_fill_canonical_url_existing_empty() -> None:
    decision = merge_entries(
        {"canonical_url": "  "},
        {"canonical_url": "https://example.com"},
    )
    assert decision["merged"]["canonical_url"] == "https://example.com"
    assert "canonical_url" in decision["changed_fields"]


def test_fill_source_url_missing() -> None:
    """source_url not in existing at all → filled."""
    decision = merge_entries({}, {"source_url": "https://example.com"})
    assert decision["merged"]["source_url"] == "https://example.com"
    assert "source_url" in decision["changed_fields"]


def test_fill_pdf_url_existing_truthy() -> None:
    decision = merge_entries(
        {"pdf_url": "https://existing.com/p.pdf"},
        {"pdf_url": "https://new.com/p.pdf"},
    )
    assert decision["merged"]["pdf_url"] == "https://existing.com/p.pdf"
    assert "pdf_url" not in decision["changed_fields"]


def test_fill_local_pdf_path_incoming_none() -> None:
    """Incoming doesn't have the field at all."""
    decision = merge_entries(
        {"local_pdf_path": "/p/smith.pdf"},
        {},
    )
    assert decision["merged"]["local_pdf_path"] == "/p/smith.pdf"
    assert "local_pdf_path" not in decision["changed_fields"]


def test_fill_source_name_existing_truthy() -> None:
    """source_name is a string → truthy → keep existing."""
    decision = merge_entries(
        {"source_name": "crossref"},
        {"source_name": "europepmc"},
    )
    assert decision["merged"]["source_name"] == "crossref"
    assert "source_name" not in decision["changed_fields"]


def test_fill_source_payload_none_to_something() -> None:
    decision = merge_entries(
        {"source_payload": None},
        {"source_payload": {"key": "val"}},
    )
    # None is not truthy → takes incoming
    assert decision["merged"]["source_payload"] == {"key": "val"}
    assert "source_payload" in decision["changed_fields"]


def test_fill_arxiv_id_missing() -> None:
    decision = merge_entries({}, {"arxiv_id": "2401.12345"})
    assert decision["merged"]["arxiv_id"] == "2401.12345"
    assert "arxiv_id" in decision["changed_fields"]


def test_fill_abstract_url_missing() -> None:
    decision = merge_entries({}, {"abstract_url": "https://example.com/abs"})
    assert decision["merged"]["abstract_url"] == "https://example.com/abs"
    assert "abstract_url" in decision["changed_fields"]


# ---------------------------------------------------------------------------
# merge_entries: USER_OWNED_FIELDS preservation
# ---------------------------------------------------------------------------


def test_preserve_citekey_in_merged() -> None:
    """citekey from existing is preserved in merged."""
    decision = merge_entries(
        {"citekey": "smith2024graph", "title": "Test"},
        {"title": "Test Extended"},
    )
    assert decision["merged"]["citekey"] == "smith2024graph"
    assert "citekey" not in decision["changed_fields"]


def test_user_owned_field_not_in_merged() -> None:
    """If user-owned field from existing is overwritten, restore it."""
    # The _prefer_more_informative_text is applied to "note" but "tags" and "citekey"
    # are USER_OWNED_FIELDS. They're handled in the for loop at the end.
    # But tags are handled separately above. citekey is user-owned.
    # Let's test that existing citekey survives.
    decision = merge_entries(
        {"citekey": "smith2024graph"},
        {"citekey": "different2024"},
    )
    assert decision["merged"]["citekey"] == "smith2024graph"
    assert "citekey" not in decision["changed_fields"]


# ---------------------------------------------------------------------------
# _prefer_more_informative_text: direct tests
# ---------------------------------------------------------------------------


def test_prefer_text_both_none() -> None:
    assert _prefer_more_informative_text(None, None) is None


def test_prefer_text_existing_none() -> None:
    assert _prefer_more_informative_text(None, "incoming") == "incoming"


def test_prefer_text_existing_empty() -> None:
    assert _prefer_more_informative_text("", "incoming") == "incoming"


def test_prefer_text_existing_whitespace() -> None:
    assert _prefer_more_informative_text("   ", "incoming") == "incoming"


def test_prefer_text_incoming_none() -> None:
    assert _prefer_more_informative_text("existing", None) == "existing"


def test_prefer_text_incoming_empty() -> None:
    assert _prefer_more_informative_text("existing", "") == "existing"


def test_prefer_text_incoming_whitespace() -> None:
    assert _prefer_more_informative_text("existing", "   ") == "existing"


def test_prefer_text_incoming_longer() -> None:
    assert _prefer_more_informative_text("short", "longer text") == "longer text"


def test_prefer_text_existing_longer() -> None:
    assert _prefer_more_informative_text("longer text here", "short") == "longer text here"


def test_prefer_text_same_length() -> None:
    """Same length → keeps existing (incoming not strictly longer)."""
    assert _prefer_more_informative_text("ABCD", "WXYZ") == "ABCD"


def test_prefer_text_whitespace_not_counted() -> None:
    """Length comparison uses stripped versions."""
    # "  ABC  " → strip → "ABC" (3 chars)
    # "DE" → strip → "DE" (2 chars)
    # existing is longer → keep existing
    assert _prefer_more_informative_text("  ABC  ", "DE") == "  ABC  "


def test_prefer_text_incoming_longer_after_strip() -> None:
    """Incoming longer after stripping both."""
    assert _prefer_more_informative_text("A", "  BC  ") == "  BC  "
