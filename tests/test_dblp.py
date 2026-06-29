import json

from pzi.metadata_sources import fetch_dblp_record_by_title

_DBLP_RESPONSE = {
    "result": {
        "hits": {
            "hit": [
                {
                    "info": {
                        "title": "Attention Is All You Need.",
                        "authors": {
                            "author": [
                                {"@pid": "1", "text": "Ashish Vaswani"},
                                {"@pid": "2", "text": "Noam Shazeer 0001"},
                            ]
                        },
                        "venue": "NeurIPS",
                        "year": "2017",
                        "type": "Conference and Workshop Papers",
                        "doi": "10.5555/3295222.3295349",
                    }
                }
            ]
        }
    }
}

_DBLP_SINGLE_AUTHOR = {
    "result": {
        "hits": {
            "hit": [
                {
                    "info": {
                        "title": "A Solo Paper",
                        "authors": {"author": {"@pid": "9", "text": "Solo Author"}},
                        "venue": "JMLR",
                        "year": "2020",
                        "type": "Journal Articles",
                    }
                }
            ]
        }
    }
}


def test_dblp_normalizes_fields_and_strips_homonym_suffix() -> None:
    result = fetch_dblp_record_by_title(
        "Attention Is All You Need",
        fetch_text=lambda _: json.dumps(_DBLP_RESPONSE),
    )
    assert result is not None
    assert result["title"] == "Attention Is All You Need"  # trailing '.' stripped
    assert result["authors"] == ["Ashish Vaswani", "Noam Shazeer"]  # '0001' removed
    assert result["year"] == 2017
    assert result["venue"] == "NeurIPS"
    assert result["doi"] == "10.5555/3295222.3295349"
    assert result["item_type"] == "conferencePaper"


def test_dblp_handles_single_author_object() -> None:
    result = fetch_dblp_record_by_title(
        "A Solo Paper",
        fetch_text=lambda _: json.dumps(_DBLP_SINGLE_AUTHOR),
    )
    assert result is not None
    assert result["authors"] == ["Solo Author"]
    assert result["item_type"] == "journalArticle"


def test_dblp_empty_title_returns_none() -> None:
    assert fetch_dblp_record_by_title("   ", fetch_text=lambda _: "{}") is None


def test_dblp_no_hits_returns_none() -> None:
    empty = {"result": {"hits": {}}}
    assert fetch_dblp_record_by_title("x", fetch_text=lambda _: json.dumps(empty)) is None
