import json

from pzi.metadata_sources import fetch_openalex_record

_OPENALEX_RESPONSE = {
    "id": "https://openalex.org/W2123456789",
    "doi": "https://doi.org/10.1145/1327452.1327492",
    "title": "MapReduce: simplified data processing on large clusters",
    "authorships": [
        {"author": {"display_name": "Jeffrey Dean"}},
        {"author": {"display_name": "Sanjay Ghemawat"}},
    ],
    "publication_year": 2008,
    "primary_location": {
        "source": {"display_name": "Communications of the ACM"}
    },
}

_OPENALEX_OA_RESPONSE = {
    **_OPENALEX_RESPONSE,
    "open_access": {
        "oa_url": "https://example.com/paper.pdf"
    },
}


def test_fetch_openalex_record_normalizes_fields() -> None:
    result = fetch_openalex_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_OPENALEX_RESPONSE),
    )
    assert result is not None
    assert result["title"] == "MapReduce: simplified data processing on large clusters"
    assert result["authors"] == ["Jeffrey Dean", "Sanjay Ghemawat"]
    assert result["year"] == 2008
    assert result["venue"] == "Communications of the ACM"
    assert result["doi"] == "10.1145/1327452.1327492"


def test_fetch_openalex_record_returns_none_on_http_error() -> None:
    def failing_fetch(url: str) -> str:
        raise OSError("network error")

    assert fetch_openalex_record("10.1234/foo", fetch_text=failing_fetch) is None


def test_fetch_openalex_record_returns_none_without_id() -> None:
    result = fetch_openalex_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"title": "No ID here"}),
    )
    assert result is None


def test_fetch_openalex_record_encodes_doi_in_url() -> None:
    seen: list[str] = []

    def fetch_and_record(url: str) -> str:
        seen.append(url)
        return json.dumps(_OPENALEX_RESPONSE)

    fetch_openalex_record(
        "10.5555/3327546.3327713",
        fetch_text=fetch_and_record,
    )
    assert seen and "10.5555%2F3327546.3327713" in seen[0]


def test_fetch_openalex_record_includes_pdf_url_when_oa() -> None:
    result = fetch_openalex_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_OPENALEX_OA_RESPONSE),
    )
    assert result is not None
    assert result.get("pdf_url") == "https://example.com/paper.pdf"


def test_fetch_openalex_record_handles_missing_authors() -> None:
    response = {**_OPENALEX_RESPONSE, "authorships": None}
    result = fetch_openalex_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is not None
    assert result["authors"] == []


def test_fetch_openalex_record_handles_missing_venue() -> None:
    response = {**_OPENALEX_RESPONSE, "primary_location": None}
    result = fetch_openalex_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is not None
    assert result["venue"] is None


def test_fetch_openalex_record_handles_malformed_json() -> None:
    def bad_json(url: str) -> str:
        return "not json"

    assert fetch_openalex_record("10.1234/foo", fetch_text=bad_json) is None


def test_openalex_carries_volume_issue_and_pages_from_biblio() -> None:
    """OpenAlex puts them under `biblio`, split into first/last page."""
    payload = {
        **_OPENALEX_RESPONSE,
        "biblio": {
            "volume": "51",
            "issue": "1",
            "first_page": "107",
            "last_page": "113",
        },
    }
    result = fetch_openalex_record("10.1145/1327452.1327492", fetch_text=lambda _: json.dumps(payload))

    assert result is not None
    assert result["volume"] == "51"
    assert result["number"] == "1"
    assert result["pages"] == "107--113"


def test_openalex_single_page_article_has_no_range() -> None:
    payload = {**_OPENALEX_RESPONSE, "biblio": {"first_page": "e0234", "last_page": None}}
    result = fetch_openalex_record("10.1145/x", fetch_text=lambda _: json.dumps(payload))

    assert result is not None
    assert result["pages"] == "e0234"
