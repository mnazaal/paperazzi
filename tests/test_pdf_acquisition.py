from pzi.pdf_acquisition import landing_page_urls, pdf_candidates_from_record

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


# --- pdf_candidates_from_record tests ---


def test_pdf_candidates_from_record_returns_record_pdf_url() -> None:
    result = pdf_candidates_from_record(
        base_record={"pdf_url": "https://example.com/paper.pdf"},
        raw_value="https://journal.org/article",
    )
    assert result == [{"source": "record", "url": "https://example.com/paper.pdf"}]


def test_pdf_candidates_from_record_strips_pdf_url() -> None:
    result = pdf_candidates_from_record(
        base_record={"pdf_url": "  https://example.com/paper.pdf  "},
        raw_value="https://journal.org/article",
    )
    assert result == [{"source": "record", "url": "https://example.com/paper.pdf"}]


def test_pdf_candidates_from_record_falls_back_to_landing_pages() -> None:
    result = pdf_candidates_from_record(
        base_record={
            "canonical_url": "https://journal.org/article",
        },
        raw_value="https://journal.org",
    )
    assert result == [
        {"source": "landing_page", "url": "https://journal.org/article"},
        {"source": "landing_page", "url": "https://journal.org"},
    ]


def test_pdf_candidates_from_record_handles_no_urls() -> None:
    result = pdf_candidates_from_record(
        base_record={},
        raw_value="not-a-url",
    )
    assert result == []


def test_pdf_candidates_from_record_handles_empty_pdf_url() -> None:
    result = pdf_candidates_from_record(
        base_record={"pdf_url": ""},
        raw_value="https://journal.org/article",
    )
    assert result == [
        {"source": "landing_page", "url": "https://journal.org/article"},
    ]
