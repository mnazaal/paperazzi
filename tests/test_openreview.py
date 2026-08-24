import json

import pytest

from pzi.metadata_sources import fetch_openreview_record_by_title

# API v2 wraps each content field as {"value": ...}.
_OPENREVIEW_V2 = {
    "notes": [
        {
            "content": {
                "title": {"value": "Scaling Laws for Neural Language Models"},
                "authors": {"value": ["Jared Kaplan", "Sam McCandlish"]},
                "venue": {"value": "ICLR 2024"},
                "pdf": {"value": "/pdf/abc123.pdf"},
            },
            "pdate": 1704067200000,  # 2024-01-01 UTC (ms)
        }
    ]
}

# API v1 stores content fields plainly.
_OPENREVIEW_V1 = {
    "notes": [
        {
            "content": {
                "title": "An Older Paper",
                "authors": ["Ada Lovelace"],
                "venue": "TMLR",
            },
            "cdate": 1500000000000,  # 2017 (ms)
        }
    ]
}


def test_openreview_v2_value_wrapped_fields() -> None:
    result = fetch_openreview_record_by_title(
        "Scaling Laws", fetch_text=lambda _: json.dumps(_OPENREVIEW_V2)
    )
    assert result is not None
    assert result["title"] == "Scaling Laws for Neural Language Models"
    assert result["authors"] == ["Jared Kaplan", "Sam McCandlish"]
    assert result["venue"] == "ICLR 2024"
    assert result["year"] == 2024
    assert result["pdf_url"] == "https://openreview.net/pdf/abc123.pdf"


def test_openreview_v1_plain_fields() -> None:
    result = fetch_openreview_record_by_title(
        "An Older Paper", fetch_text=lambda _: json.dumps(_OPENREVIEW_V1)
    )
    assert result is not None
    assert result["title"] == "An Older Paper"
    assert result["authors"] == ["Ada Lovelace"]
    assert result["venue"] == "TMLR"
    assert result["year"] == 2017
    assert "pdf_url" not in result


def test_openreview_empty_title_returns_none() -> None:
    assert fetch_openreview_record_by_title("  ", fetch_text=lambda _: "{}") is None


def test_openreview_no_notes_returns_none() -> None:
    assert fetch_openreview_record_by_title(
        "x", fetch_text=lambda _: json.dumps({"notes": []})
    ) is None


# ── Entry type must not depend on which provider answered ───────────────
#
# `carry_item_type` exists because "the entry type silently depended on which
# provider answered". It was wired for the three providers that already carried
# `item_type`; OpenReview did not, so it had nothing to carry and an ICLR paper
# was written as `@article` with `journal = {ICLR 2022 Poster}` — a conference
# paper typed as a journal one, whose journal is a submission decision.


def test_a_conference_submission_is_typed_as_a_conference_paper() -> None:
    result = fetch_openreview_record_by_title(
        "Scaling Laws", fetch_text=lambda _: json.dumps(_OPENREVIEW_V2)
    )
    assert result is not None
    assert result["item_type"] == "conferencePaper"


def test_a_journal_submission_is_not_typed_as_a_conference_paper() -> None:
    """OpenReview hosts TMLR as well as the conferences, so this is not blanket."""
    result = fetch_openreview_record_by_title(
        "An Older Paper", fetch_text=lambda _: json.dumps(_OPENREVIEW_V1)
    )
    assert result is not None
    assert result["item_type"] == "journalArticle"


@pytest.mark.parametrize(
    "venue, expected",
    [
        ("ICLR 2022 Poster", "conferencePaper"),
        ("NeurIPS 2023 Oral", "conferencePaper"),
        ("ICML 2021", "conferencePaper"),
        ("TMLR", "journalArticle"),
        ("Transactions on Machine Learning Research", "journalArticle"),
        ("Journal of Machine Learning Research", "journalArticle"),
        ("", "conferencePaper"),
        (None, "conferencePaper"),
    ],
)
def test_the_venue_string_decides_the_entry_type(venue, expected) -> None:
    from pzi.metadata_sources import _openreview_item_type

    assert _openreview_item_type(venue) == expected


def test_every_title_search_provider_supplies_an_item_type() -> None:
    """The invariant, spanning all four — this is how OpenReview was missed.

    `carry_item_type` can only carry what a provider puts on the record, so a
    provider that omits `item_type` silently falls back to `@article`. Asserted
    across the four together rather than per provider, because the defect was
    that three had it and the fourth did not.
    """
    import inspect

    from pzi import metadata_sources as ms

    normalizers = [
        "_crossref_normalize", "_openalex_normalize",
        "_dblp_normalize", "_openreview_normalize",
    ]
    missing = [
        name for name in normalizers
        if hasattr(ms, name) and "item_type" not in inspect.getsource(getattr(ms, name))
    ]
    assert not missing, f"normalizers that set no item_type: {missing}"
