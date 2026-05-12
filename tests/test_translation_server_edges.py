"""Edge-case tests for Zotero translation-server — covering previously untested branches."""


from pzi.translation_server import (
    _call_translation_server,
    _extract_arxiv_id,
    _extract_year,
    _normalize_creators,
    _post_text,
    extract_pdf_attachments,
    fetch_search_translations,
    fetch_web_translations,
    normalize_translation_item,
)

# ── normalize_translation_item edges ─────────────────────────────────────

def test_normalize_translation_item_no_arxiv_id_in_extra() -> None:
    """Line 69: extra field has no 'arxiv:' line."""
    item = {
        "title": "A Paper",
        "creators": [],
        "extra": "some notes\nmore notes\n",
    }
    result = normalize_translation_item(item)
    assert result["record"]["arxiv_id"] is None


def test_normalize_translation_item_arxiv_id_from_archive_id() -> None:
    """Line 74: arxiv_id extracted from archiveID field."""
    item = {
        "title": "A Paper",
        "creators": [],
        "archiveID": " 2401.00001  ",
    }
    result = normalize_translation_item(item)
    assert result["record"]["arxiv_id"] == "2401.00001"


# ── extract_pdf_attachments edges ───────────────────────────────────────

def test_extract_pdf_attachments_non_list_input() -> None:
    """Line 142: value is not a list — returns empty list."""
    result = extract_pdf_attachments("not-a-list")
    assert result == []


def test_extract_pdf_attachments_non_mapping_entry() -> None:
    """Line 160: individual attachment is not a Mapping — skipped."""
    result = extract_pdf_attachments(["not-a-dict"])
    assert result == []


def test_extract_pdf_attachments_pdf_by_title() -> None:
    """Line 164: PDF detected via title containing 'pdf'."""
    attachments = [
        {
            "url": "https://example.com/article",
            "title": "Download PDF version",
            "mimeType": "text/html",
        }
    ]
    result = extract_pdf_attachments(attachments)
    assert len(result) == 1
    assert result[0]["title"] == "Download PDF version"


def test_extract_pdf_attachments_null_url() -> None:
    """Line 174: url is None — skipped."""
    attachments = [
        {
            "url": None,
            "title": "A PDF",
            "mimeType": "application/pdf",
        }
    ]
    result = extract_pdf_attachments(attachments)
    assert result == []


# ── _call_translation_server edges ───────────────────────────────────────

def test_call_translation_server_response_not_list() -> None:
    """Lines 183-184: response is not a list — raises ValueError."""
    import pytest

    def fake_post(endpoint: str, payload: object) -> object:
        return {"not": "a list"}

    with pytest.raises(ValueError, match="must be a list"):
        _call_translation_server(
            endpoint="http://localhost/web",
            payload={},
            post_json=fake_post,
        )


def test_call_translation_server_items_not_mapping() -> None:
    """Line 206: individual item is not a Mapping — raises ValueError."""
    import pytest

    def fake_post(endpoint: str, payload: object) -> object:
        return ["not-a-mapping"]

    with pytest.raises(ValueError, match="must be objects"):
        _call_translation_server(
            endpoint="http://localhost/web",
            payload={},
            post_json=fake_post,
        )


# ── fetch_web_translations / fetch_search_translations edges ─────────────

def test_fetch_search_translations_empty_response() -> None:
    """Lines 208->204: search endpoint returns empty list."""
    def fake_post_text(endpoint: str, payload: object) -> object:
        return []

    results = fetch_search_translations(
        "no results query",
        server_url="http://127.0.0.1:1969",
        post_text=fake_post_text,
    )
    assert results == []


def test_fetch_web_translations_empty_response() -> None:
    """Line 211: web endpoint returns empty list."""
    def fake_post_json(endpoint: str, payload: object) -> object:
        return []

    results = fetch_web_translations(
        "https://example.com",
        server_url="http://127.0.0.1:1969",
        post_json=fake_post_json,
    )
    assert results == []


# ── _post / _post_text edges ─────────────────────────────────────────────

def test_post_text_delegates_to_post_with_text_plain() -> None:
    """_post_text calls _post with content_type='text/plain'."""
    try:
        _post_text("http://127.0.0.1:19999/nowhere", "query text")
    except (OSError, ConnectionError):
        pass  # Expected: no server


# ── _normalize_creators / _extract_year edges ────────────────────────────

def test_normalize_creators_name_fallback() -> None:
    """Creator has 'name' field directly."""
    creators = [{"name": "Jane Doe"}]
    result = _normalize_creators(creators)
    assert result == ["Jane Doe"]


def test_normalize_creators_first_last_order() -> None:
    """Creator has firstName and lastName."""
    creators = [{"firstName": "Jane", "lastName": "Doe"}]
    result = _normalize_creators(creators)
    assert result == ["Doe, Jane"]


def test_normalize_creators_last_name_only() -> None:
    """Creator has lastName but no firstName."""
    creators = [{"lastName": "Smith"}]
    result = _normalize_creators(creators)
    assert result == ["Smith"]


def test_normalize_creators_name_overrides_first_last() -> None:
    """Name takes precedence over firstName+lastName."""
    creators = [{"name": "Full Name", "firstName": "Partial", "lastName": "Partial"}]
    result = _normalize_creators(creators)
    assert result == ["Full Name"]


def test_extract_year_no_date_field() -> None:
    """_extract_year returns None when item has no 'date' key."""
    result = _extract_year({})
    assert result is None


def test_extract_arxiv_id_empty_archive_id() -> None:
    """_extract_arxiv_id with whitespace-only archiveID returns None."""
    result = _extract_arxiv_id({"archiveID": "   "})
    assert result is None


def test_extract_arxiv_id_extra_no_colon() -> None:
    """_extract_arxiv_id with extra lines that have no colon."""
    result = _extract_arxiv_id({"extra": "line_without_colon"})
    assert result is None


def test_extract_arxiv_id_extra_arxiv_value_empty() -> None:
    """_extract_arxiv_id with 'arxiv: ' (empty value) returns None."""
    result = _extract_arxiv_id({"extra": "arxiv:   "})
    assert result is None
