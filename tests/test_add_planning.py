import urllib.error

import pytest

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
    similarity_hint_warnings,
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
    "desktop_fallback_hosts", "pdf_discovery_parallel", "exclude_pdf_urls",
}


def test_build_discovery_context_has_full_key_set() -> None:
    ctx = build_discovery_context(raw_value="https://x.test", server_url="http://ts")
    assert set(ctx) == _DISCOVERY_CONTEXT_KEYS
    # Defaults are filled even when only the two required args are supplied,
    # so the fallback path can never omit a key the steps expect.
    assert ctx["cookies"] is None
    assert ctx["pdf_discovery_parallel"] is False
    # No exclusions by default: a first attempt must be offered every candidate.
    assert ctx["exclude_pdf_urls"] is None


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
    """Without --force-new the match is *handled*: the write becomes an update."""
    record = {"citekey": "smith2024", "title": "Same", "year": 2024}

    result = attach_similarity_hint(record, [record])

    assert result is record


def test_force_new_names_the_entry_it_duplicates() -> None:
    """The exact-match early return skipped the one case that must speak.

    `--force-new` exists to bypass the exact match, so returning early for it
    meant `import --force-new` doubled a library with `warnings: []` — the
    silent duplication `import_service` already claimed to have fixed, on the
    fuzzy branch only.
    """
    existing = {"citekey": "smith2024", "title": "Same", "year": 2024,
                "doi": "10.1000/same"}
    incoming = dict(existing, citekey="smith2024-2")

    result = attach_similarity_hint(incoming, [existing], force_new=True)

    assert result["duplicate_of"] == "smith2024"
    warnings = similarity_hint_warnings(result)
    assert len(warnings) == 1
    assert "smith2024" in warnings[0] and "--force-new" in warnings[0]
    # A certainty, not the fuzzy path's maybe.
    assert "possibly" not in warnings[0].lower()


def test_force_new_does_not_annotate_the_entry_itself() -> None:
    """The duplicate is structural state, not something to write into the .bib."""
    existing = {"citekey": "smith2024", "title": "Same", "doi": "10.1000/same"}

    result = attach_similarity_hint(
        dict(existing, citekey="smith2024-2"), [existing], force_new=True
    )

    assert "note" not in result


# --- Near-duplicate hint: note text, structured field, and the warning -------

_EXISTING_NEAR_DUPLICATE = {
    "citekey": "smith2024graph",
    "title": "A Study of Graph Parsers",
    "authors": ["Smith, Jane"],
    "year": 2024,
    "canonical_url": "https://example.org/paper",
}


def _incoming_near_duplicate(**overrides: object) -> dict[str, object]:
    """Same paper, different URL and no shared identifier — the insert case.

    The URL has to be a genuinely different location. It used to differ from
    the existing entry's only by a trailing slash, which made this an exact URL
    match dressed up as a near-duplicate: it reached the fuzzy path solely
    because identities compared URLs verbatim.
    """
    record: dict[str, object] = {
        "citekey": "smith2024graph-2",
        "title": "A Study of Graph Parsers",
        "authors": ["Smith, Jane"],
        "year": 2024,
        "canonical_url": "https://mirror.example.net/2024/graph-parsers",
    }
    record.update(overrides)
    return record


def test_attach_similarity_hint_records_citekey_structurally() -> None:
    """The hint lands in a typed field, not only in the note prose."""
    result = attach_similarity_hint(
        _incoming_near_duplicate(), [_EXISTING_NEAR_DUPLICATE]
    )

    assert result["similarity_hint"] == "smith2024graph"
    assert result["note"] == "Possibly similar to smith2024graph"


def test_attach_similarity_hint_appends_to_an_existing_note() -> None:
    result = attach_similarity_hint(
        _incoming_near_duplicate(note="Read this one first"),
        [_EXISTING_NEAR_DUPLICATE],
    )

    assert result["note"] == "Read this one first; Possibly similar to smith2024graph"
    assert result["similarity_hint"] == "smith2024graph"


def test_attach_similarity_hint_does_not_duplicate_note_but_still_flags() -> None:
    """Re-capturing an already-hinted entry still warns.

    The note must not grow a second copy of the same sentence, but suppressing
    the note is not a reason to suppress the warning.
    """
    result = attach_similarity_hint(
        _incoming_near_duplicate(note="Possibly similar to smith2024graph"),
        [_EXISTING_NEAR_DUPLICATE],
    )

    assert result["note"] == "Possibly similar to smith2024graph"
    assert result["similarity_hint"] == "smith2024graph"


def test_similarity_hint_warnings_surfaces_the_citekey() -> None:
    hinted = attach_similarity_hint(
        _incoming_near_duplicate(), [_EXISTING_NEAR_DUPLICATE]
    )

    assert similarity_hint_warnings(hinted) == [
        "possibly a duplicate of smith2024graph — compare them with `pzi library dedupe`"
    ]


def test_similarity_hint_warnings_silent_without_a_hint() -> None:
    assert similarity_hint_warnings({"citekey": "smith2024graph"}) == []
    assert similarity_hint_warnings({"similarity_hint": None}) == []
    assert similarity_hint_warnings({"similarity_hint": "  "}) == []


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

    # Case survives a template now — the final sanitizer used to lowercase
    # everything, which is also why `.upper` could never do anything.
    assert format_citekey(template, RECORD, {"Smith2024AStu"}) == "Smith2024AStu-2"


def test_format_citekey_supports_common_better_bibtex_formula() -> None:
    # shorttitle(n, m) per Better BibTeX: first `n` non-stopword title words,
    # the first `m` of them capitalized. `m` is not a truncation length — it
    # defaults to 0, so that reading would make every plain shorttitle()
    # render empty.
    assert (
        format_citekey("auth.lower + shorttitle(3,3) + year", RECORD, set())
        == "smithStudyGraphParsers2024"
    )


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
    """Pins the actual collision behaviour, not just "some string came back".

    The base key this template produces is `smithDeepGraphNetworks2024`
    (checked by the plain no-collision case below). Passing an `existing_keys`
    set that does not contain it — the previous version passed
    `{"smithdeep2024"}`, which never collides with the real base — would let
    `resolve_citekey_collision` be deleted entirely and this test would still
    pass. Collision suffixing itself is already pinned in
    `tests/test_format_templates.py:157` and `tests/test_citekeys.py:108-115`;
    this only has to not contradict them.
    """
    record = {
        "authors": ["Smith, Jane"],
        "title": "Deep Graph Networks",
        "year": 2024,
    }
    base = "smithDeepGraphNetworks2024"

    assert format_citekey("auth.lower + shorttitle(3,3) + year", record, set()) == base
    assert (
        format_citekey("auth.lower + shorttitle(3,3) + year", record, {base})
        == f"{base}-2"
    )


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
    """fetch_record_for_input returns (record, errors, translation_results)."""
    doi = "10.1/test"
    doi_429 = urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    def _failing_crossref(doi: str, **_: object) -> None:
        raise doi_429

    def _good_openalex(doi: str, **_: object):
        return {"title": "Test Paper", "doi": doi}

    record, provider_errors, _results = fetch_record_for_input(
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


def test_fetch_record_for_input_routes_providers_through_the_shared_fetcher() -> None:
    """The add path called providers with their module-default fetcher.

    `check` and `promote` compose `build_metadata_fetch_text` (disk cache +
    per-host rate limiting) and pass it down; add did not, so
    `metadata_cache_ttl` did nothing for adds and keyless Semantic Scholar was
    hit with no spacing.
    """
    from pzi.add_planning import fetch_record_for_input

    seen_urls: list[str] = []

    def recording_fetch_text(url: str, **_kwargs) -> str:
        seen_urls.append(url)
        raise OSError("stop here — only the wiring is under test")

    # Every provider fails, so the cascade ends by reporting no metadata; the
    # point under test is which fetcher it used on the way there.
    with pytest.raises(ValueError, match="no metadata found"):
        fetch_record_for_input(
            raw_value="10.1234/example",
            classified={"kind": "doi", "raw": "10.1234/example",
                        "normalized": "10.1234/example"},
            server_url="http://127.0.0.1:1",
            fetch_web=lambda *_a, **_kw: [],
            fetch_search=lambda *_a, **_kw: [],
            metadata_fetch_text=recording_fetch_text,
        )

    # Crossref, OpenAlex and S2 were all reached through the injected fetcher.
    assert any("crossref.org" in url for url in seen_urls), seen_urls
    assert any("openalex.org" in url for url in seen_urls), seen_urls
    assert any("semanticscholar.org" in url for url in seen_urls), seen_urls


# ---------------------------------------------------------------------------
# A candidate that is a different paper than the one asked for
# ---------------------------------------------------------------------------


def test_a_candidate_whose_doi_contradicts_the_request_is_refused() -> None:
    """A contradicting DOI only cost 50 points, which a rich record outweighs.

    When it is the *only* candidate it wins outright, so `pzi add 10.1145/A`
    stored the record for `10.9999/B` — a different paper, under the citekey and
    DOI the user asked for.
    """
    from pzi.add_planning import fetch_record_for_input

    def _search(_query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "A Completely Different Paper",
                    "doi": "10.9999/b",
                    "authors": ["Other, A", "Other, B", "Other, C"],
                    "year": 2020,
                    "venue": "Elsewhere",
                    "abstract_url": "https://example.com/abs",
                    "canonical_url": "https://example.com/paper",
                },
                "attachments": [{"url": "https://example.com/p.pdf"}],
            }
        ]

    record, _errors, _results = fetch_record_for_input(
        raw_value="10.1145/3372297",
        classified={"kind": "doi", "raw": "10.1145/3372297",
                    "normalized": "10.1145/3372297"},
        server_url="http://127.0.0.1:1969",
        fetch_web=lambda *_a, **_k: [],
        fetch_search=_search,
        fetch_crossref=lambda *_a, **_k: {
            "title": "The Paper That Was Asked For", "doi": "10.1145/3372297",
        },
    )

    assert record["doi"] == "10.1145/3372297"
    assert record["title"] == "The Paper That Was Asked For"


def test_a_candidate_that_agrees_or_is_silent_about_the_doi_is_still_taken() -> None:
    """Most translators return no DOI at all; refusing those would break capture."""
    from pzi.add_planning import fetch_record_for_input

    def _search(_query, *, server_url):
        return [
            {"item_type": "journalArticle",
             "record": {"title": "The Paper", "authors": ["A, B"], "year": 2020},
             "attachments": []},
        ]

    record, _errors, _results = fetch_record_for_input(
        raw_value="10.1145/3372297",
        classified={"kind": "doi", "raw": "10.1145/3372297",
                    "normalized": "10.1145/3372297"},
        server_url="http://127.0.0.1:1969",
        fetch_web=lambda *_a, **_k: [],
        fetch_search=_search,
    )

    assert record["title"] == "The Paper"
    assert record["doi"] == "10.1145/3372297"


def test_the_translation_server_is_named_as_the_answering_provider() -> None:
    """Both metadata paths must say which one answered, not just the cascade.

    The cascade records `metadata_provider`; the translation-server branch
    returned without it, so that path was identifiable only by the key being
    *absent* — and "the translation server answered" was therefore
    indistinguishable from "a provider answered and nobody recorded it". That is
    what let the live smoke job pass on nothing but Crossref fallbacks while
    claiming to cover the translation-server path, and it is why every capture
    on record is a fallback (PLAN item 412).

    Verified here rather than only in `tests/live/` because installing a real
    translation-server is blocked on this machine (agent git guard, item 410),
    so the live job cannot be the only thing pinning this.
    """
    from pzi.add_planning import fetch_record_for_input

    def _search(_query, *, server_url):
        return [
            {"item_type": "journalArticle",
             "record": {"title": "The Paper", "authors": ["A, B"], "year": 2020},
             "attachments": []},
        ]

    record, _errors, _results = fetch_record_for_input(
        raw_value="10.1145/3372297",
        classified={"kind": "doi", "raw": "10.1145/3372297",
                    "normalized": "10.1145/3372297"},
        server_url="http://127.0.0.1:1969",
        fetch_web=lambda *_a, **_k: [],
        fetch_search=_search,
    )

    assert record["metadata_provider"] == "translation_server"


def test_the_fallback_cascade_still_names_itself() -> None:
    """The other half of the same contract: naming one path must not blank the
    other. When the translation server returns nothing usable, the provider that
    actually answered is still the one reported."""
    from pzi.add_planning import fetch_record_for_input

    record, _errors, _results = fetch_record_for_input(
        raw_value="10.1145/3372297",
        classified={"kind": "doi", "raw": "10.1145/3372297",
                    "normalized": "10.1145/3372297"},
        server_url="http://127.0.0.1:1969",
        fetch_web=lambda *_a, **_k: [],
        fetch_search=lambda *_a, **_k: [],
        fetch_crossref=lambda *_a, **_k: {
            "title": "The Paper That Was Asked For", "doi": "10.1145/3372297",
            "authors": ["A, B"], "year": 2020,
        },
    )

    assert record["metadata_provider"] == "crossref"


# --- The provider cascade must reach its fallbacks ---------------------------


def test_a_thin_first_answer_does_not_stop_the_cascade() -> None:
    """Every normalizer returns a dict even when the response said nothing.

    `_crossref_normalize_work` builds one with `title: None` for an empty
    `message`, and the cascade broke on `meta is not None` — so a thin Crossref
    answer won permanently and OpenAlex and Semantic Scholar were never
    consulted. The fallbacks existed for exactly this case and could not be
    reached, and the resulting titleless record then passed the acceptance gate.
    """
    from pzi.add_planning import fetch_record_for_input

    calls: list[str] = []

    def _thin_crossref(doi, **_kw):
        calls.append("crossref")
        return {"title": None, "authors": [], "year": None, "doi": doi}

    def _good_openalex(doi, **_kw):
        calls.append("openalex")
        return {"title": "The Real Paper", "authors": ["Smith, Jane"], "year": 2020}

    record, _errors, _results = fetch_record_for_input(
        raw_value="10.1000/thin",
        classified={"kind": "doi", "normalized": "10.1000/thin"},
        server_url="http://127.0.0.1:59999",
        fetch_web=lambda *a, **k: [],
        fetch_search=lambda *a, **k: [],
        fetch_crossref=_thin_crossref,
        fetch_openalex=_good_openalex,
        fetch_s2=lambda *a, **k: None,
    )

    assert calls == ["crossref", "openalex"], calls
    assert record is not None and record["title"] == "The Real Paper"


def test_a_thin_answer_is_still_used_when_nothing_better_arrives() -> None:
    """The floor: what little Crossref said is better than nothing at all."""
    from pzi.add_planning import fetch_record_for_input

    record, _errors, _results = fetch_record_for_input(
        raw_value="10.1000/thin",
        classified={"kind": "doi", "normalized": "10.1000/thin"},
        server_url="http://127.0.0.1:59999",
        fetch_web=lambda *a, **k: [],
        fetch_search=lambda *a, **k: [],
        fetch_crossref=lambda doi, **_kw: {"title": None, "doi": doi, "year": 2020},
        fetch_openalex=lambda *a, **k: None,
        fetch_s2=lambda *a, **k: None,
    )

    assert record is not None
    assert record.get("year") == 2020


# ---------------------------------------------------------------------------
# An injected FlareSolverr fetcher that raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "raw_value"),
    [
        # The DOI cascade's last rung, reached when the raw input was a URL.
        ("doi", "https://example.com/paper"),
        # The URL / PDF-URL branch's own rung.
        ("url", "https://example.com/paper"),
    ],
    ids=["doi-branch", "url-branch"],
)
@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.HTTPError(None, 502, "Bad Gateway", {}, None),  # type: ignore[arg-type]
        urllib.error.URLError("connection refused"),
        TimeoutError("read timed out"),
    ],
    ids=["http", "unreachable", "timeout"],
)
def test_a_raising_flaresolverr_fetcher_does_not_abort_the_cascade(
    kind: str, raw_value: str, exc: Exception
) -> None:
    """Every injected FlareSolverr seam absorbs its fetcher's failure.

    The module's own `fetch_html_via_flaresolverr` returns None on a transport
    failure, but an injected fetcher is a plain one-argument callable that may
    raise — and the HTTP capture route injects one. Called bare, a dead
    FlareSolverr escaped `fetch_record_for_input` as whatever it raised, so the
    cascade never reached `MetadataExhausted` and the provider errors it had
    already accumulated died with it.
    """
    from pzi.add_planning import MetadataExhausted, fetch_record_for_input

    def _raising(_url: str) -> str | None:
        raise exc

    with pytest.raises(MetadataExhausted) as caught:
        fetch_record_for_input(
            raw_value=raw_value,
            classified={"kind": kind, "normalized":
                        "10.1000/x" if kind == "doi" else raw_value},
            server_url="http://127.0.0.1:59999",
            fetch_web=lambda *a, **k: [],
            fetch_search=lambda *a, **k: [],
            fetch_crossref=lambda *a, **k: None,
            fetch_openalex=lambda *a, **k: None,
            fetch_s2=lambda *a, **k: None,
            flaresolverr_url="http://127.0.0.1:8191",
            fetch_flaresolverr=_raising,
        )

    # The failure is reported, not swallowed and not raised.
    assert any(
        "502" in err or "refused" in err or "timed out" in err
        for err in caught.value.provider_errors
    ), caught.value.provider_errors


@pytest.mark.parametrize(
    ("kind", "raw_value"),
    [("doi", "https://example.com/paper"), ("url", "https://example.com/paper")],
    ids=["doi-branch", "url-branch"],
)
def test_flaresolverr_html_is_read_into_a_record_at_both_seams(
    kind: str, raw_value: str
) -> None:
    """The success side of the same two rungs.

    Both carried a `# pragma: no cover — covered by integration/browser tests`;
    no browser test imports this module, and the rungs are reachable from a
    string. Their `return` tuples were also dedented to a level that parses only
    by bracket continuation, so this is what says the re-indentation kept them.
    """
    from pzi.add_planning import fetch_record_for_input

    html = (
        '<html><head>'
        '<meta name="citation_title" content="Behind Cloudflare">'
        '<meta name="citation_author" content="Smith, Ada">'
        '<meta name="citation_publication_date" content="2024">'
        '</head></html>'
    )

    record, _errors, _results = fetch_record_for_input(
        raw_value=raw_value,
        classified={"kind": kind, "normalized":
                    "10.1000/x" if kind == "doi" else raw_value},
        server_url="http://127.0.0.1:59999",
        fetch_web=lambda *a, **k: [],
        fetch_search=lambda *a, **k: [],
        fetch_crossref=lambda *a, **k: None,
        fetch_openalex=lambda *a, **k: None,
        fetch_s2=lambda *a, **k: None,
        flaresolverr_url="http://127.0.0.1:8191",
        fetch_flaresolverr=lambda _url: html,
    )

    assert record["title"] == "Behind Cloudflare"
    assert record["authors"] == ["Smith, Ada"]
