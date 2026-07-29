from pzi.pdf_discovery import landing_page_urls

# --- landing_page_urls tests ---


def test_landing_page_urls_returns_deduplicated_urls() -> None:
    result = landing_page_urls(
        base_record={
            "canonical_url": "https://journal.org/article",
            "source_url": "https://journal.org/abstract",
            "abstract_url": "https://journal.org/abstract",  # duplicate of source_url
        },
        raw_value="https://journal.org/",
    )
    assert result == [
        "https://journal.org/article",
        "https://journal.org/abstract",
        "https://journal.org/",
    ]


def test_landing_page_urls_skips_non_urls() -> None:
    result = landing_page_urls(
        base_record={
            "canonical_url": "https://journal.org/article",
        },
        raw_value="/relative/path",
    )
    assert result == ["https://journal.org/article"]


def test_landing_page_urls_skips_missing_values() -> None:
    result = landing_page_urls(
        base_record={},
        raw_value="https://example.com/paper",
    )
    assert result == ["https://example.com/paper"]


def test_landing_page_urls_returns_empty_when_none_valid() -> None:
    result = landing_page_urls(
        base_record={"canonical_url": None, "source_url": 123},
        raw_value="not-a-url",
    )
    assert result == []


def test_landing_page_urls_strips_and_deduplicates() -> None:
    result = landing_page_urls(
        base_record={
            "canonical_url": "  https://journal.org/article  ",
            "source_url": "https://journal.org/article",
        },
        raw_value="https://journal.org/article",
    )
    assert result == ["https://journal.org/article"]


