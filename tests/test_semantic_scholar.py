import json

from pzi.semantic_scholar import fetch_semantic_scholar_record

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
