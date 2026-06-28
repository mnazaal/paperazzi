from pzi.http_payloads import (
    capture_payload,
    detail_payload,
    entries_payload,
    inbox_drain_payload,
    promote_payload,
    search_payload,
    tag_change_payload,
    tag_list_payload,
    update_payload,
)
from pzi.http_post_routes import (
    metadata_url_override_error,
    pdf_url_candidates_from_body,
    record_overrides_from_capture_body,
)


def test_record_overrides_from_capture_body_filters_tags_and_fallbacks() -> None:
    result = record_overrides_from_capture_body(
        {
            "tags": ["ml", "", 42, "graphs"],
            "page_title": " Paper ",
            "canonical_url": " https://example.com/canonical ",
            "doi": "10.1234/example",
        }
    )

    assert result == {
        "tags": ["ml", "graphs"],
        "fallback_title": "Paper",
        "fallback_canonical_url": "https://example.com/canonical",
        "fallback_doi": "10.1234/example",
    }


def test_pdf_url_candidates_from_body_validates_public_urls() -> None:
    result = pdf_url_candidates_from_body(
        {"pdf_url_candidates": [" https://example.com/a.pdf ", ""]},
        safe_url=lambda value: value.startswith("https://example.com"),
    )

    assert result == ["https://example.com/a.pdf"]


def test_pdf_url_candidates_from_body_rejects_bad_candidate() -> None:
    result = pdf_url_candidates_from_body(
        {"pdf_url_candidates": ["http://127.0.0.1/a.pdf"]},
        safe_url=lambda _value: False,
    )

    assert result is False


def test_metadata_url_override_error_reports_first_bad_url() -> None:
    result = metadata_url_override_error(
        {"canonical_url": "http://127.0.0.1/page"},
        safe_url=lambda _value: False,
    )

    assert result == "canonical_url must be a public http(s) URL"


def test_capture_payload_defaults_pdf_status() -> None:
    result = capture_payload(
        {
            "status": "ok",
            "bib_name": "ml",
            "citekey": "smith2024paper",
            "action": "insert",
            "pdf_path": "/tmp/a.pdf",
            "dry_run": False,
            "message": "insert entry",
            "warnings": [],
            "errors": [],
        }
    )

    assert result["pdf_status"] == "direct_saved"
    assert result["bib"] == "ml"


def test_capture_payload_hides_diagnostics_by_default_and_includes_when_verbose() -> None:
    result = {
        "status": "ok",
        "bib_name": "ml",
        "citekey": "smith2024paper",
        "action": "insert",
        "pdf_path": None,
        "dry_run": False,
        "message": "insert entry",
        "warnings": [],
        "errors": [],
        "metadata_diagnostics": ["selected result 2/2: score=10"],
    }

    assert "metadata_diagnostics" not in capture_payload(result)
    assert capture_payload(result, include_diagnostics=True)["metadata_diagnostics"] == [
        "selected result 2/2: score=10"
    ]


def test_update_payload_hides_item_diagnostics_by_default_and_includes_when_verbose() -> None:
    result = {
        "status": "ok",
        "bib_name": "ml",
        "dry_run": True,
        "items": [
            {
                "citekey": "smith2024paper",
                "changed_fields": ["doi"],
                "note": None,
                "metadata_diagnostics": ["selected result 2/2: score=10"],
            }
        ],
        "errors": [],
    }

    assert "metadata_diagnostics" not in update_payload(result)["items"][0]
    assert update_payload(result, include_diagnostics=True)["items"][0]["metadata_diagnostics"] == [
        "selected result 2/2: score=10"
    ]


# ── Tier 2: rich embedded metadata from browser extension ──────────────


def test_record_overrides_accepts_embedded_metadata() -> None:
    result = record_overrides_from_capture_body({
        "embedded_authors": ["Alice Smith", "Bob Jones"],
        "embedded_year": "2024",
        "embedded_venue": "Journal of Tests",
        "embedded_abstract": "This paper tests the capture pipeline.",
        "embedded_volume": "42",
        "embedded_issue": "3",
        "embedded_pages": "100--199",
        "embedded_issn": "1234-5678",
        "embedded_isbn": "978-0-123-45678-9",
        "embedded_pdf_url": "https://example.com/paper.pdf",
    })

    assert result["fallback_authors"] == "Alice Smith and Bob Jones"
    assert result["fallback_year"] == "2024"
    assert result["fallback_venue"] == "Journal of Tests"
    assert result["fallback_abstract"] == "This paper tests the capture pipeline."
    assert result["fallback_volume"] == "42"
    assert result["fallback_issue"] == "3"
    assert result["fallback_pages"] == "100--199"
    assert result["fallback_issn"] == "1234-5678"
    assert result["fallback_isbn"] == "978-0-123-45678-9"
    assert result["fallback_pdf_url"] == "https://example.com/paper.pdf"


def test_record_overrides_embedded_authors_strips_and_deduplicates() -> None:
    result = record_overrides_from_capture_body({
        "embedded_authors": [" Alice Smith ", "Bob Jones", "Alice Smith"],
    })

    assert result["fallback_authors"] == "Alice Smith and Bob Jones"


def test_record_overrides_embedded_authors_rejects_non_strings() -> None:
    result = record_overrides_from_capture_body({
        "embedded_authors": ["Valid Author", 42],
    })

    assert "fallback_authors" not in result


def test_record_overrides_embedded_skips_empty_or_absent() -> None:
    result = record_overrides_from_capture_body({
        "embedded_authors": [],
        "embedded_year": "",
        "embedded_venue": None,
    })

    assert "fallback_authors" not in result
    assert "fallback_year" not in result
    assert "fallback_venue" not in result


def test_record_overrides_jsonld_og_fallbacks() -> None:
    result = record_overrides_from_capture_body({
        "embedded_jsonld_authors": ["Carl Clarke", "Dana Diaz"],
        "embedded_jsonld_title": "From JSON-LD",
        "embedded_jsonld_year": "2023",
        "embedded_og_title": "From OpenGraph",
    })

    assert result["fallback_authors"] == "Carl Clarke and Dana Diaz"
    assert result["fallback_title"] == "From JSON-LD"
    assert result["fallback_year"] == "2023"


def test_record_overrides_jsonld_og_overwrites_empty_citation_fallback() -> None:
    """JSON-LD/OG should be processed and may fill gaps that citation fields
    left empty, but they don't overwrite already-filled citation fields."""
    result = record_overrides_from_capture_body({
        "embedded_authors": [],          # no citation authors
        "embedded_jsonld_authors": ["Eve Ellis"],
        "embedded_og_title": "OG Title",
    })

    assert result["fallback_authors"] == "Eve Ellis"
    assert result["fallback_title"] == "OG Title"


# ---------------------------------------------------------------------------
# trusted fields promotion
# ---------------------------------------------------------------------------


def test_trusted_fields_promotes_doi_to_normal_override() -> None:
    """When trusted_fields contains 'doi', store as 'doi' not 'fallback_doi'."""
    result = record_overrides_from_capture_body({
        "doi": "10.1109/TEST.2022.12345",
        "trusted_fields": ["doi"],
    })

    assert result["doi"] == "10.1109/TEST.2022.12345"
    assert "fallback_doi" not in result


def test_trusted_fields_promotes_multiple_fields() -> None:
    result = record_overrides_from_capture_body({
        "doi": "10.1109/TEST.2022.12345",
        "embedded_authors": ["Alice Smith"],
        "embedded_year": "2022",
        "trusted_fields": ["doi", "authors", "year"],
    })

    assert result["doi"] == "10.1109/TEST.2022.12345"
    # Authors promoted as list, not " and "-joined string
    assert result["authors"] == ["Alice Smith"]
    assert result["year"] == "2022"
    assert "fallback_doi" not in result
    assert "fallback_authors" not in result
    assert "fallback_year" not in result


def test_trusted_fields_ignored_when_not_list() -> None:
    result = record_overrides_from_capture_body({
        "doi": "10.1109/TEST.2022.12345",
        "trusted_fields": "not-a-list",
    })

    assert "fallback_doi" in result
    assert "doi" not in result


def test_trusted_fields_absent_preserves_fallback_behavior() -> None:
    result = record_overrides_from_capture_body({
        "doi": "10.1109/TEST.2022.12345",
    })

    assert result["fallback_doi"] == "10.1109/TEST.2022.12345"
    assert "doi" not in result


def test_trusted_fields_unknown_field_silently_ignored() -> None:
    result = record_overrides_from_capture_body({
        "doi": "10.1109/TEST.2022.12345",
        "trusted_fields": ["nonexistent"],
    })

    assert result["fallback_doi"] == "10.1109/TEST.2022.12345"


def test_trusted_fields_prefers_embedded_over_page_title_for_title() -> None:
    """When 'title' is trusted, embedded_jsonld or embedded_og prevails.
    page_title maps to fallback_title, NOT affected by trusted_fields."""
    result = record_overrides_from_capture_body({
        "page_title": "Generic Page",
        "embedded_jsonld_title": "Real Title",
        "trusted_fields": ["title"],
    })

    # JSON-LD title wins because it overwrites fallback_title.
    # But trusted_fields promotes fallback_title → title.
    # So 'title' should be set from whatever fallback_title was.
    assert "title" in result
    assert "fallback_title" not in result


# --- pure response-payload builders ----------------------------------------


def test_search_payload_wraps_matches_with_total() -> None:
    out = search_payload({"status": "ok", "bib_name": "ml",
                          "matches": [{"citekey": "a"}, {"citekey": "b"}]})
    assert out == {"status": "ok", "bib": "ml", "errors": [],
                   "matches": [{"citekey": "a"}, {"citekey": "b"}], "total": 2}


def test_entries_payload_listing_form_includes_sort() -> None:
    out = entries_payload(
        {"status": "ok", "items": [{"citekey": "a"}], "total": 5,
         "offset": 0, "limit": 1, "sort": "year"},
        offset=0, limit=1,
    )
    assert out["entries"] == [{"citekey": "a"}]
    assert out["total"] == 5
    assert out["sort"] == "year"


def test_entries_payload_legacy_matches_form_paginates() -> None:
    out = entries_payload(
        {"status": "ok", "matches": [{"c": 1}, {"c": 2}, {"c": 3}]},
        offset=1, limit=1,
    )
    assert out["entries"] == [{"c": 2}]
    assert out["total"] == 3
    assert out["offset"] == 1


def test_detail_payload_sorts_tags_and_picks_url() -> None:
    out = detail_payload(
        {"citekey": "a", "title": "T", "tags": ["z", "a"],
         "source_url": "https://example.com/s"},
        "ml",
    )
    assert out["bib"] == "ml"
    assert out["entry"]["tags"] == ["a", "z"]
    assert out["entry"]["url"] == "https://example.com/s"


def test_tag_list_and_tag_change_payloads() -> None:
    listed = tag_list_payload({"status": "ok", "citekey": "a", "tags": ["x"]})
    assert listed["citekey"] == "a" and listed["tags"] == ["x"]
    changed = tag_change_payload(
        {"status": "ok", "citekey": "a", "tags": ["x"], "changed": True,
         "dry_run": False, "message": "ok"}
    )
    assert changed["changed"] is True and changed["message"] == "ok"


def test_promote_payload_carries_summary_and_flags() -> None:
    out = promote_payload(
        {"status": "ok", "items": [{"x": 1}], "summary": {"promoted": 1},
         "keep_preprint": False, "dry_run": False}
    )
    assert out["summary"] == {"promoted": 1}
    assert out["keep_preprint"] is False


def test_inbox_drain_payload_shapes_counts_and_items() -> None:
    out = inbox_drain_payload(
        {"status": "ok", "inbox_file": "/x/inbox.txt", "dry_run": False,
         "total": 2, "counts": {"added": 1, "failed": 1}, "items": [{"v": 1}]}
    )
    assert out["inbox_file"] == "/x/inbox.txt"
    assert out["counts"] == {"added": 1, "failed": 1}
    assert out["total"] == 2


def test_items_payload_passes_non_dict_items_through() -> None:
    # _items_payload via update_payload: a non-dict item is preserved as-is.
    out = update_payload({"status": "ok", "items": ["raw-string-item"]})
    assert out["items"] == ["raw-string-item"]
