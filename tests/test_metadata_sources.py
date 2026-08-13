

def test_a_rate_limit_on_the_title_search_path_is_reported() -> None:
    """The guard returned one line before the reporter could read the body.

    `if not isinstance(data, dict) or "error" in data or "message" in data:
    return None` ran *before* `_s2_data_error`, so the reporting branch was
    unreachable for exactly the bodies it was written to catch. This is the
    title-search path, which is what `check` uses — so an S2 rate limit was
    invisible there while the by-DOI path reported it correctly.
    """
    from pzi.metadata_sources import fetch_semantic_scholar_record_by_title

    errors: list[str] = []
    record = fetch_semantic_scholar_record_by_title(
        "Attention Is All You Need",
        fetch_text=lambda url, **_kw: '{"message": "Too Many Requests"}',
        errors=errors,
    )

    assert record is None
    assert errors == ["semantic-scholar: Too Many Requests"]


def test_every_s2_entry_point_reads_both_error_keys() -> None:
    """S2 uses `error` and `message`; one reader handled only `error`."""
    from pzi.metadata_sources import (
        fetch_semantic_scholar_record,
        fetch_semantic_scholar_record_by_title,
        fetch_semantic_scholar_record_by_title_with_error,
    )

    for key in ("error", "message"):
        body = f'{{"{key}": "quota exceeded"}}'

        by_doi_errors: list[str] = []
        fetch_semantic_scholar_record(
            "10.1/x", fetch_text=lambda url, **_kw: body, errors=by_doi_errors
        )
        by_title_errors: list[str] = []
        fetch_semantic_scholar_record_by_title(
            "A Paper", fetch_text=lambda url, **_kw: body, errors=by_title_errors
        )
        _record, tuple_error = fetch_semantic_scholar_record_by_title_with_error(
            "A Paper", fetch_text=lambda url, **_kw: body
        )

        assert by_doi_errors == ["semantic-scholar: quota exceeded"], key
        assert by_title_errors == ["semantic-scholar: quota exceeded"], key
        assert tuple_error == "quota exceeded", key
