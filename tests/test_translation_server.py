from pzi.translation_server import (
    extract_pdf_attachments,
    fetch_search_translations,
    fetch_web_translations,
    normalize_translation_item,
)


def test_normalize_translation_item_maps_core_fields() -> None:
    result = normalize_translation_item(
        {
            "itemType": "journalArticle",
            "title": "Graph Parsers",
            "creators": [
                {"firstName": "Jane", "lastName": "Smith"},
                {"name": "John Doe"},
            ],
            "date": "2024-01-15",
            "publicationTitle": "Journal of Parsing",
            "DOI": "10.1145/3368089.3409741",
            "url": "https://example.com/paper?utm_source=x",
            "archiveID": "2401.12345",
        },
        source_url="https://example.com/landing",
    )

    assert result == {
        "item_type": "journalArticle",
        "record": {
            "title": "Graph Parsers",
            "authors": ["Smith, Jane", "John Doe"],
            "year": 2024,
            "venue": "Journal of Parsing",
            "doi": "10.1145/3368089.3409741",
            "arxiv_id": "2401.12345",
            "canonical_url": "https://example.com/paper",
            "source_url": "https://example.com/landing",
            "abstract_url": "https://example.com/paper",
            "abstract": None,
        },
        "attachments": [],
    }


def test_normalize_translation_item_extracts_arxiv_from_extra() -> None:
    result = normalize_translation_item(
        {
            "title": "Graph Parsers",
            "extra": "arXiv: 2401.12345\nSome other field: value",
        }
    )

    assert result["record"]["arxiv_id"] == "2401.12345"


def test_extract_pdf_attachments_filters_and_normalizes_candidates() -> None:
    attachments = extract_pdf_attachments(
        [
            {
                "title": "Full Text PDF",
                "url": "https://example.com/paper.pdf?utm_source=x",
                "mimeType": "application/pdf",
            },
            {
                "title": "Snapshot",
                "url": "https://example.com/page",
                "mimeType": "text/html",
            },
            {
                "title": "PDF mirror",
                "url": "https://example.com/download?id=1",
            },
        ]
    )

    assert attachments == [
        {
            "title": "Full Text PDF",
            "url": "https://example.com/paper.pdf",
            "mime_type": "application/pdf",
        },
        {
            "title": "PDF mirror",
            "url": "https://example.com/download?id=1",
            "mime_type": None,
        },
    ]


def test_fetch_web_translations_posts_to_web_endpoint() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post_json(endpoint: str, payload: object) -> object:
        calls.append((endpoint, payload))
        return [
            {
                "title": "Graph Parsers",
                "url": "https://example.com/paper",
            }
        ]

    results = fetch_web_translations(
        "https://example.com/paper",
        server_url="http://127.0.0.1:1969",
        post_json=fake_post_json,
    )

    assert calls == [
        (
            "http://127.0.0.1:1969/web",
            {"url": "https://example.com/paper", "session": "pzi"},
        )
    ]
    assert results[0]["record"]["canonical_url"] == "https://example.com/paper"


def test_fetch_search_translations_posts_to_search_endpoint() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post_text(endpoint: str, payload: object) -> object:
        calls.append((endpoint, payload))
        return [{"title": "Graph Parsers"}]

    results = fetch_search_translations(
        "10.1145/3368089.3409741",
        server_url="http://127.0.0.1:1969/",
        post_text=fake_post_text,
    )

    assert calls == [
        (
            "http://127.0.0.1:1969/search",
            "10.1145/3368089.3409741",
        )
    ]
    assert results[0]["record"]["title"] == "Graph Parsers"


def test_fetch_translation_rejects_non_list_response() -> None:
    def fake_post_json(endpoint: str, payload: object) -> object:
        return {"title": "Graph Parsers"}

    try:
        fetch_web_translations(
            "https://example.com/paper",
            server_url="http://127.0.0.1:1969",
            post_json=fake_post_json,
        )
    except ValueError as exc:
        assert str(exc) == "translation-server response must be a list"
    else:
        raise AssertionError("expected ValueError")
