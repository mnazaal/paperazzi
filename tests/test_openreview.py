import json

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
