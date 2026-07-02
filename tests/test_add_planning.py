import urllib.error

from pzi.add_planning import (
    _coerce_year,
    attach_similarity_hint,
    build_discovery_context,
    error_result,
    fetch_record_for_input,
    has_minimum_metadata,
    manual_record_from_overrides,
    merge_fetched_record_with_overrides,
    minimum_metadata_diagnostics,
    pdf_result_fields,
    safe_api_call,
    split_record_overrides,
)
from pzi.format_templates import format_citekey, format_pdf_filename, render_zotero_template

# Every key a discovery step may read. If a step starts consuming a new context
# key, add it here so the shared builder (used by both the normal fetch path and
# the add_service TS-failure fallback) is guaranteed to provide it.
_DISCOVERY_CONTEXT_KEYS = {
    "raw_value", "server_url", "unpaywall_email", "contact_email", "s2_api_key",
    "flaresolverr_url", "browser_pdf_cmd", "pdf_url_candidates", "cookies",
    "fetch_web", "fetch_unpaywall", "fetch_crossref", "fetch_openalex", "fetch_s2",
    "fetch_flaresolverr", "translation_attachments", "api_url", "api_auth_token",
    "desktop_fallback_hosts", "pdf_discovery_parallel",
}


def test_build_discovery_context_has_full_key_set() -> None:
    ctx = build_discovery_context(raw_value="https://x.test", server_url="http://ts")
    assert set(ctx) == _DISCOVERY_CONTEXT_KEYS
    # Defaults are filled even when only the two required args are supplied,
    # so the fallback path can never omit a key the steps expect.
    assert ctx["cookies"] is None
    assert ctx["pdf_discovery_parallel"] is False


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


def test_merge_fetched_record_with_overrides_applies_fallback_for_empty_list() -> None:
    # Regression: a fetched `authors: []` (metadata source found the record
    # but not the author list) used to block the fallback_authors override
    # entirely, since the emptiness check only recognized None/blank-string,
    # not an empty list.
    merged = merge_fetched_record_with_overrides(
        {"title": "Fetched Title", "authors": []},
        {"fallback_authors": ["Page Author"]},
    )

    assert merged["authors"] == ["Page Author"]


def test_merge_fetched_record_with_overrides_coerces_string_fallback_year() -> None:
    # Regression: the HTTP capture route sends fallback_year as a string
    # (page-scraped embedded_year); merge must not leave a string in the
    # NormalizedRecord's year field, or downstream similarity comparisons
    # (abs(int - str)) crash. See test_similarity's coerces_string_year tests.
    merged = merge_fetched_record_with_overrides(
        {"title": "Fetched Title"},
        {"fallback_year": "2024"},
    )

    assert merged["year"] == 2024
    assert isinstance(merged["year"], int)


def test_merge_fetched_record_with_overrides_drops_unparseable_string_year() -> None:
    merged = merge_fetched_record_with_overrides(
        {"title": "Fetched Title"},
        {"fallback_year": "not-a-year"},
    )

    assert merged["year"] is None


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
    # shorttitle(3,3): first 3 non-stopword title words, each truncated to 3
    # chars — "Study", "Graph", "Parsers" -> "stu"+"gra"+"par".
    assert format_citekey("auth.lower + shorttitle(3,3) + year", RECORD, set()) == "smithstugrapar2024"


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


def test_format_citekey_with_existing_keys() -> None:
    result = format_citekey("auth.lower + shorttitle(3,3) + year", {
        "authors": ["Smith, Jane"],
        "title": "Deep Graph Networks",
        "year": 2024,
    }, {"smithdeep2024"})
    assert result is not None
    assert "smith" in result.lower()


def test_format_pdf_filename_basic() -> None:
    result = format_pdf_filename(
        "{{ firstCreator suffix=\" - \" }}{{ year suffix=\" - \" }}{{ title truncate=\"80\" }}",
        {"authors": ["Smith, Jane"], "year": 2024, "title": "Deep Graph Networks for Citation Context Prediction"},
    )
    assert "Smith" in result
    assert "2024" in result


# ---------------------------------------------------------------------------
# Provider-error propagation
# ---------------------------------------------------------------------------


def test_safe_api_call_records_http_error() -> None:
    """safe_api_call appends HTTP error codes to the errors list."""
    errors: list[str] = []
    exc = urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    result = safe_api_call(lambda: (_ for _ in ()).throw(exc), errors=errors)

    assert result == []
    assert errors == ["HTTP 429"]


def test_safe_api_call_without_errors_stays_silent() -> None:
    """When errors is not given, HTTPError is still swallowed silently."""
    exc = urllib.error.HTTPError(None, 500, "Server Error", {}, None)  # type: ignore[arg-type]

    result = safe_api_call(lambda: (_ for _ in ()).throw(exc))

    assert result == []


def test_fetch_record_for_input_returns_provider_errors() -> None:
    """fetch_record_for_input returns (record, errors); errors accumulate across providers."""
    doi = "10.1/test"
    doi_429 = urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    def _failing_crossref(doi: str, **_: object) -> None:
        raise doi_429

    def _good_openalex(doi: str, **_: object):
        return {"title": "Test Paper", "doi": doi}

    record, provider_errors = fetch_record_for_input(
        raw_value=doi,
        classified={"kind": "doi", "normalized": doi},
        server_url="http://ts.test",
        fetch_web=lambda *a, **k: [],
        fetch_search=lambda *a, **k: [],
        fetch_crossref=_failing_crossref,
        fetch_openalex=_good_openalex,
    )

    assert record.get("title") == "Test Paper"
    assert provider_errors, "expected at least one provider error"
    assert any("429" in e for e in provider_errors)
