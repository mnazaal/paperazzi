from pzi.add_planning import (
    _coerce_year,
    attach_similarity_hint,
    error_result,
    has_minimum_metadata,
    manual_record_from_overrides,
    merge_fetched_record_with_overrides,
    minimum_metadata_diagnostics,
    pdf_result_fields,
    split_record_overrides,
)
from pzi.format_templates import format_citekey, format_pdf_filename, render_zotero_template


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


RECORD = {
    "authors": ["Smith, Jane", "Doe, John"],
    "year": 2024,
    "title": "A Study of Graph Parsers: Methods and Results.",
    "doi": "10.1234/ABC.DEF",
    "venue": "ICSE",
}


def test_render_zotero_default_file_template() -> None:
    template = (
        '{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}'
        '{{ title truncate="20" }}'
    )

    assert render_zotero_template(template, RECORD) == "Smith - 2024 - A Study of Graph Par"


def test_render_zotero_colon_variables_and_regex_replacement() -> None:
    template = (
        '{{ :firstCreator suffix="-" replaceFrom="\\s+and\\s+|\\." replaceTo="-" }}'
        '{{ :year suffix="-" }}'
        '{{ :title truncate="100" replaceFrom="\\s+" replaceTo="-" regexOpts="g" }}'
    )

    assert (
        render_zotero_template(template, RECORD)
        == "Smith-2024-A-Study-of-Graph-Parsers:-Methods-and-Results."
    )


def test_format_pdf_filename_sanitizes_path_separators_and_adds_extension() -> None:
    template = (
        '{{ firstCreator suffix="-" }}{{ year suffix="-" }}'
        '{{ title truncate="100" }}'
    )
    record = {**RECORD, "title": "Bad / Path: Paper"}

    assert format_pdf_filename(template, record) == "Smith-2024-Bad Path Paper.pdf"


def test_format_citekey_supports_zotero_template_and_collision_suffix() -> None:
    template = '{{ firstCreator }}{{ year }}{{ title truncate="5" }}'

    assert format_citekey(template, RECORD, {"smith2024astu"}) == "smith2024astu-2"


def test_format_citekey_supports_common_better_bibtex_formula() -> None:
    assert format_citekey("auth.lower + shorttitle(3,3) + year", RECORD, set()) == "smithstu2024"


# ---------------------------------------------------------------------------
# _coerce_year
# ---------------------------------------------------------------------------


def test_coerce_year_int() -> None:
    assert _coerce_year(2023) == 2023


def test_coerce_year_valid_str() -> None:
    assert _coerce_year("2023") == 2023


def test_coerce_year_str_with_whitespace() -> None:
    assert _coerce_year(" 2024 ") == 2024


def test_coerce_year_boundary_low() -> None:
    assert _coerce_year(1000) == 1000


def test_coerce_year_boundary_high() -> None:
    assert _coerce_year(2099) == 2099


def test_coerce_year_below_range() -> None:
    assert _coerce_year(999) is None
    assert _coerce_year("999") is None


def test_coerce_year_above_range() -> None:
    assert _coerce_year(2100) is None
    assert _coerce_year("2100") is None


def test_coerce_year_non_numeric_str() -> None:
    assert _coerce_year("n/a") is None


def test_coerce_year_empty_str() -> None:
    assert _coerce_year("") is None


def test_coerce_year_none() -> None:
    assert _coerce_year(None) is None


def test_coerce_year_list() -> None:
    assert _coerce_year([2023]) is None


def test_coerce_year_float() -> None:
    assert _coerce_year(2023.0) is None


# ---------------------------------------------------------------------------
# has_minimum_metadata
# ---------------------------------------------------------------------------


def test_has_minimum_metadata_title_and_doi() -> None:
    assert has_minimum_metadata({"title": "Paper", "doi": "10.1234/x"}) is True


def test_has_minimum_metadata_title_and_authors() -> None:
    assert has_minimum_metadata(
        {"title": "Paper", "authors": ["Smith"]}
    ) is True


def test_has_minimum_metadata_title_and_year_int() -> None:
    assert has_minimum_metadata({"title": "Paper", "year": 2023}) is True


def test_has_minimum_metadata_title_and_year_str() -> None:
    assert has_minimum_metadata({"title": "Paper", "year": "2023"}) is True


def test_has_minimum_metadata_title_whitespace_rejected() -> None:
    assert has_minimum_metadata({"title": "  ", "doi": "10.1234/x"}) is False


def test_has_minimum_metadata_title_missing() -> None:
    assert has_minimum_metadata({"doi": "10.1234/x"}) is False


def test_has_minimum_metadata_title_is_wrong_type() -> None:
    assert has_minimum_metadata({"title": 123, "doi": "10.1234/x"}) is False


def test_has_minimum_metadata_empty_authors() -> None:
    assert has_minimum_metadata({"title": "Paper", "authors": []}) is False


def test_has_minimum_metadata_authors_wrong_type() -> None:
    assert has_minimum_metadata(
        {"title": "Paper", "authors": "Smith"}
    ) is False


def test_has_minimum_metadata_empty_doi() -> None:
    assert has_minimum_metadata({"title": "Paper", "doi": "  "}) is False


def test_has_minimum_metadata_title_only() -> None:
    assert has_minimum_metadata({"title": "Paper"}) is False


def test_has_minimum_metadata_empty_record() -> None:
    assert has_minimum_metadata({}) is False


# ---------------------------------------------------------------------------
# minimum_metadata_diagnostics
# ---------------------------------------------------------------------------


def test_minimum_metadata_diagnostics_missing_title() -> None:
    diag = minimum_metadata_diagnostics({})
    assert len(diag) == 1
    assert "missing title" in diag[0]


def test_minimum_metadata_diagnostics_title_but_no_identifiers() -> None:
    diag = minimum_metadata_diagnostics({"title": "Paper"})
    assert len(diag) == 1
    assert "title found" in diag[0]
    assert "doi not available" in diag[0]
    assert "authors not available" in diag[0]
    assert "year not available" in diag[0]


def test_minimum_metadata_diagnostics_with_doi() -> None:
    diag = minimum_metadata_diagnostics(
        {"title": "Paper", "doi": "10.1234/x"}
    )
    assert len(diag) == 1
    assert "doi=10.1234/x" in diag[0]


def test_minimum_metadata_diagnostics_with_authors() -> None:
    diag = minimum_metadata_diagnostics(
        {"title": "Paper", "authors": ["Smith", "Doe"]}
    )
    assert len(diag) == 1
    assert "2 author(s)" in diag[0]


def test_minimum_metadata_diagnostics_with_year_str() -> None:
    diag = minimum_metadata_diagnostics(
        {"title": "Paper", "year": "2023"}
    )
    assert len(diag) == 1
    assert "year=2023" in diag[0]


def test_minimum_metadata_diagnostics_with_non_numeric_year() -> None:
    diag = minimum_metadata_diagnostics(
        {"title": "Paper", "year": "n/a"}
    )
    assert len(diag) == 1
    assert "year not available" in diag[0]
