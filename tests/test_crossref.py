import json

from pzi.metadata_sources import fetch_crossref_record

_MAPREDUCE_RESPONSE = {
    "message": {
        "DOI": "10.1145/1327452.1327492",
        "title": ["MapReduce: simplified data processing on large clusters"],
        "author": [
            {"given": "Jeffrey", "family": "Dean"},
            {"given": "Sanjay", "family": "Ghemawat"},
        ],
        "published-print": {"date-parts": [[2008, 1, 1]]},
        "container-title": ["Communications of the ACM"],
    }
}


def test_fetch_crossref_record_normalizes_fields() -> None:
    result = fetch_crossref_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_MAPREDUCE_RESPONSE),
    )

    assert result is not None
    assert result["title"] == "MapReduce: simplified data processing on large clusters"
    assert result["authors"] == ["Dean, Jeffrey", "Ghemawat, Sanjay"]
    assert result["year"] == 2008
    assert result["venue"] == "Communications of the ACM"
    assert result["doi"] == "10.1145/1327452.1327492"


def test_fetch_crossref_record_returns_none_on_http_error() -> None:
    def failing_fetch(url: str) -> str:
        raise OSError("network error")

    assert fetch_crossref_record("10.1234/foo", fetch_text=failing_fetch) is None


def test_fetch_crossref_record_returns_none_on_missing_message() -> None:
    result = fetch_crossref_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"status": "failed"}),
    )
    assert result is None


def test_fetch_crossref_record_encodes_doi_in_url() -> None:
    seen: list[str] = []

    def fetch_and_record(url: str) -> str:
        seen.append(url)
        return json.dumps(
            {"message": {"DOI": "10.5555/3327546.3327713", "title": []}}
        )

    fetch_crossref_record(
        "10.5555/3327546.3327713",
        fetch_text=fetch_and_record,
    )
    assert seen and "10.5555%2F3327546.3327713" in seen[0]


_CROSSREF_LINK_RESPONSE = {
    "message": {
        "DOI": "10.1038/nature12373",
        "title": ["Nanometre-scale thermometry in a living cell"],
        "link": [
            {
                "URL": "http://www.nature.com/articles/nature12373.pdf",
                "content-type": "application/pdf",
                "content-version": "vor",
                "intended-application": "text-mining",
            },
            {
                "URL": "http://www.nature.com/articles/nature12373",
                "content-type": "text/html",
                "content-version": "vor",
                "intended-application": "text-mining",
            },
        ],
    }
}


def test_fetch_crossref_pdf_url_extracts_pdf_from_links() -> None:
    from pzi.metadata_sources import fetch_crossref_pdf_url

    result = fetch_crossref_pdf_url(
        "10.1038/nature12373",
        fetch_text=lambda _: json.dumps(_CROSSREF_LINK_RESPONSE),
    )
    assert result == "http://www.nature.com/articles/nature12373.pdf"


def test_fetch_crossref_pdf_url_returns_none_without_pdf_links() -> None:
    from pzi.metadata_sources import fetch_crossref_pdf_url

    response = {
        "message": {
            "DOI": "10.1234/foo",
            "title": ["Test"],
            "link": [
                {
                    "URL": "http://example.com/article",
                    "content-type": "text/html",
                }
            ],
        }
    }
    result = fetch_crossref_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is None


def test_fetch_crossref_pdf_url_returns_none_on_error() -> None:
    from pzi.metadata_sources import fetch_crossref_pdf_url

    def failing_fetch(url: str) -> str:
        raise OSError("network error")

    assert fetch_crossref_pdf_url("10.1234/foo", fetch_text=failing_fetch) is None


def test_fetch_crossref_record_includes_pdf_url_when_available() -> None:
    result = fetch_crossref_record(
        "10.1038/nature12373",
        fetch_text=lambda _: json.dumps(_CROSSREF_LINK_RESPONSE),
    )
    assert result is not None
    assert result.get("pdf_url") == "http://www.nature.com/articles/nature12373.pdf"


def test_fetch_crossref_record_by_title_empty() -> None:
    from pzi.metadata_sources import fetch_crossref_record_by_title
    result = fetch_crossref_record_by_title("   ", fetch_text=lambda url: "{}")
    assert result is None


def test_fetch_crossref_record_by_title_no_items() -> None:
    from pzi.metadata_sources import fetch_crossref_record_by_title
    result = fetch_crossref_record_by_title(
        "nonexistent",
        fetch_text=lambda url: json.dumps({"message": {"items": []}}),
    )
    assert result is None
