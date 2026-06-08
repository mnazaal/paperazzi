from pzi.add_planning import (
    attach_similarity_hint,
    error_result,
    manual_record_from_overrides,
    merge_fetched_record_with_overrides,
    pdf_result_fields,
    split_record_overrides,
)


def test_split_record_overrides_separates_fallback_prefixes() -> None:
    normal, fallback = split_record_overrides(
        {
            "title": "Exact Title",
            "fallback_title": "Fallback Title",
            "fallback_year": 2024,
        }
    )

    assert normal == {"title": "Exact Title"}
    assert fallback == {"title": "Fallback Title", "year": 2024}


def test_merge_fetched_record_with_overrides_applies_fallback_only_when_empty() -> None:
    merged = merge_fetched_record_with_overrides(
        {"title": "Fetched Title", "venue": ""},
        {
            "title": "Manual Title",
            "fallback_title": "Fallback Title",
            "fallback_venue": "Fallback Venue",
            "fallback_year": 2024,
        },
    )

    assert merged["title"] == "Manual Title"
    assert merged["venue"] == "Fallback Venue"
    assert merged["year"] == 2024


def test_manual_record_from_overrides_merges_fallback_and_normal_values() -> None:
    record = manual_record_from_overrides(
        {"title": "Manual Title", "fallback_year": 2024}
    )

    assert record == {"title": "Manual Title", "year": 2024}


def test_pdf_result_fields_reports_blocked_direct_pdf() -> None:
    fields = pdf_result_fields(
        pdf_url="https://example.test/paper.pdf",
        pdf_path=None,
        warnings=["blocked"],
        dry_run=False,
    )

    assert fields["pdf_status"] == "direct_blocked"
    assert fields["pdf_error"] == "blocked"
    assert fields["pdf_suggestion"] is not None


def test_error_result_shapes_consistent_failure_payload() -> None:
    result = error_result(
        message="failed",
        errors=["bad"],
        dry_run=True,
        warnings=["warn"],
        bib={"name": "ml", "path": "/tmp/library.bib"},
    )

    assert result["status"] == "error"
    assert result["bib_name"] == "ml"
    assert result["bib_path"] == "/tmp/library.bib"
    assert result["errors"] == ["bad"]
    assert result["warnings"] == ["warn"]


def test_attach_similarity_hint_leaves_exact_match_unchanged() -> None:
    record = {"citekey": "smith2024", "title": "Same", "year": 2024}

    result = attach_similarity_hint(record, [record])

    assert result is record
