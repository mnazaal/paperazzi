import json

from pzi.metadata_sources import (
    _s2_error_message,
    fetch_semantic_scholar_record,
    fetch_semantic_scholar_record_by_title_with_error,
    probe_s2_api,
)

_S2_RESPONSE = {
    "title": "MapReduce: simplified data processing on large clusters",
    "authors": [
        {"name": "Jeffrey Dean"},
        {"name": "Sanjay Ghemawat"},
    ],
    "year": 2008,
    "venue": "Communications of the ACM",
    "externalIds": {"DOI": "10.1145/1327452.1327492"},
}

_S2_OA_RESPONSE = {
    **_S2_RESPONSE,
    "openAccessPdf": {"url": "https://example.com/paper.pdf"},
}


def test_fetch_semantic_scholar_record_normalizes_fields() -> None:
    result = fetch_semantic_scholar_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_S2_RESPONSE),
    )
    assert result is not None
    assert result["title"] == "MapReduce: simplified data processing on large clusters"
    assert result["authors"] == ["Jeffrey Dean", "Sanjay Ghemawat"]
    assert result["year"] == 2008
    assert result["venue"] == "Communications of the ACM"
    assert result["doi"] == "10.1145/1327452.1327492"


def test_fetch_semantic_scholar_record_returns_none_on_http_error() -> None:
    def failing_fetch(url: str) -> str:
        raise OSError("network error")

    assert (
        fetch_semantic_scholar_record("10.1234/foo", fetch_text=failing_fetch) is None
    )


def test_fetch_semantic_scholar_record_returns_none_on_error_response() -> None:
    result = fetch_semantic_scholar_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"error": "not found"}),
    )
    assert result is None


def test_fetch_semantic_scholar_record_returns_none_on_message_response() -> None:
    result = fetch_semantic_scholar_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"message": "not found"}),
    )
    assert result is None


def test_fetch_semantic_scholar_record_encodes_doi_in_url() -> None:
    seen: list[str] = []

    def fetch_and_record(url: str) -> str:
        seen.append(url)
        return json.dumps(_S2_RESPONSE)

    fetch_semantic_scholar_record(
        "10.5555/3327546.3327713",
        fetch_text=fetch_and_record,
    )
    assert seen and "DOI:10.5555%2F3327546.3327713" in seen[0]


def test_fetch_semantic_scholar_record_includes_pdf_url_when_oa() -> None:
    result = fetch_semantic_scholar_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_S2_OA_RESPONSE),
    )
    assert result is not None
    assert result.get("pdf_url") == "https://example.com/paper.pdf"


def test_fetch_semantic_scholar_record_accepts_api_key() -> None:
    """Verify that api_key parameter is accepted and function still works."""
    result = fetch_semantic_scholar_record(
        "10.1145/1327452.1327492",
        api_key="test-api-key",
        fetch_text=lambda url: json.dumps(_S2_RESPONSE),
    )
    assert result is not None
    assert result["title"] == "MapReduce: simplified data processing on large clusters"


def test_fetch_semantic_scholar_record_no_api_key() -> None:
    """Verify function works without api_key (anonymous access)."""
    result = fetch_semantic_scholar_record(
        "10.1145/1327452.1327492",
        api_key=None,
        fetch_text=lambda url: json.dumps(_S2_RESPONSE),
    )
    assert result is not None


def test_fetch_semantic_scholar_record_handles_missing_authors() -> None:
    response = {**_S2_RESPONSE, "authors": None}
    result = fetch_semantic_scholar_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is not None
    assert result["authors"] == []


def test_fetch_semantic_scholar_record_handles_missing_year() -> None:
    response = {**_S2_RESPONSE, "year": None}
    result = fetch_semantic_scholar_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is not None
    assert result["year"] is None


def test_fetch_semantic_scholar_record_handles_malformed_json() -> None:
    def bad_json(url: str) -> str:
        return "not json"

    assert (
        fetch_semantic_scholar_record("10.1234/foo", fetch_text=bad_json) is None
    )


# ---------------------------------------------------------------------------
# _s2_error_message
# ---------------------------------------------------------------------------


def test_s2_error_message_extracts_error_string() -> None:
    assert _s2_error_message({"error": "Rate limit exceeded"}) == "Rate limit exceeded"


def test_s2_error_message_returns_none_when_no_error() -> None:
    assert _s2_error_message({"data": []}) is None


def test_s2_error_message_returns_none_for_non_dict_inputs() -> None:
    assert _s2_error_message(None) is None
    assert _s2_error_message("not a dict") is None
    assert _s2_error_message(42) is None


def test_s2_error_message_returns_none_when_error_not_string() -> None:
    assert _s2_error_message({"error": 403}) is None


# ---------------------------------------------------------------------------
# fetch_semantic_scholar_record_by_title_with_error
# ---------------------------------------------------------------------------

_S2_SEARCH_RESPONSE = {
    "data": [
        {
            "title": "Graph Parsers",
            "authors": [{"name": "Jane Smith"}],
            "year": 2024,
            "venue": "Journal of Parsing",
            "externalIds": {"DOI": "10.9999/jop.2024"},
        }
    ]
}


def test_fetch_semantic_scholar_by_title_with_error_returns_record() -> None:
    result, error = fetch_semantic_scholar_record_by_title_with_error(
        "Graph Parsers",
        fetch_text=lambda _: json.dumps(_S2_SEARCH_RESPONSE),
    )
    assert result is not None
    assert result["title"] == "Graph Parsers"
    assert result["venue"] == "Journal of Parsing"
    assert error is None


def test_fetch_semantic_scholar_by_title_with_error_returns_rate_limit_error() -> None:
    result, error = fetch_semantic_scholar_record_by_title_with_error(
        "Graph Parsers",
        fetch_text=lambda _: json.dumps({"error": "Rate limit exceeded"}),
    )
    assert result is None
    assert error == "Rate limit exceeded"


def test_fetch_semantic_scholar_by_title_with_error_returns_auth_error() -> None:
    result, error = fetch_semantic_scholar_record_by_title_with_error(
        "Graph Parsers",
        fetch_text=lambda _: json.dumps({"error": "Authorization required"}),
    )
    assert result is None
    assert error == "Authorization required"


def test_fetch_semantic_scholar_by_title_with_error_empty_title() -> None:
    result, error = fetch_semantic_scholar_record_by_title_with_error("   ")
    assert result is None
    assert error == "empty title"


def test_fetch_semantic_scholar_by_title_with_error_raises_on_oserror() -> None:
    def failing(url: str) -> str:
        raise OSError("network down")

    import pytest
    with pytest.raises(OSError, match="network down"):
        fetch_semantic_scholar_record_by_title_with_error(
            "Graph Parsers",
            fetch_text=failing,
        )


# ---------------------------------------------------------------------------
# probe_s2_api
# ---------------------------------------------------------------------------


def test_probe_s2_api_reachable() -> None:
    assert probe_s2_api(
        fetch_text=lambda _: json.dumps({"title": "Gravitational Waves", "authors": []}),
    ) is True


def test_probe_s2_api_unreachable_on_error_json() -> None:
    assert probe_s2_api(
        fetch_text=lambda _: json.dumps({"error": "not found"}),
    ) is False


def test_probe_s2_api_unreachable_on_oserror() -> None:
    def failing(url: str) -> str:
        raise OSError("connection refused")

    assert probe_s2_api(fetch_text=failing) is False


def test_probe_s2_api_unreachable_on_garbage() -> None:
    assert probe_s2_api(
        fetch_text=lambda _: "not json",
    ) is False
