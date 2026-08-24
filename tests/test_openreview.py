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
    assert "item_type" not in result, (
        "an undated venue must not be typed at all, so the caller's `article` "
        "default stands — which is right for OpenReview's journals"
    )


@pytest.mark.parametrize(
    "venue, expected",
    [
        # A dated venue instance is a conference occurrence.
        ("ICLR 2022 Poster", "conferencePaper"),
        ("NeurIPS 2023 Oral", "conferencePaper"),
        ("ICML 2021", "conferencePaper"),
        # Undated: OpenReview's journals, and anything this cannot read. `None`
        # keeps the caller's `article` default rather than guessing.
        ("TMLR", None),
        ("Transactions on Machine Learning Research", None),
        ("Journal of Machine Learning Research", None),
        ("", None),
        (None, None),
        # Field-agnostic on purpose: no venue abbreviation is hardcoded, so a
        # dated conference outside ML types correctly and an undated journal
        # outside ML is not mistyped.
        ("Conference on Human Factors in Computing Systems 2019", "conferencePaper"),
        ("Annual Meeting of the Association for Computational Linguistics 2020",
         "conferencePaper"),
        ("The Lancet", None),
        ("Nature", None),
        ("Journal of Finance", None),
    ],
)
def test_a_dated_venue_instance_is_a_conference_and_nothing_else_is_guessed(
    venue, expected
) -> None:
    """No venue abbreviation is hardcoded — the year is the whole signal.

    An earlier version of this listed "tmlr" among journal-indicating words. pzi
    captures papers from any discipline, so a list of venue abbreviations is a
    list that works for whoever wrote it. The year is a property of how a venue
    *instance* is named, not of a field's vocabulary.

    The signal is not universal, and the limit is worth naming: a *journal* whose
    title carries a year — "Proceedings of the Royal Society B 1998" — would be
    read as a conference. That case is out of reach here because this function
    only ever sees OpenReview `venue` strings, and OpenReview names its journals
    without a year. It is deliberately not asserted as correct above.
    """
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
