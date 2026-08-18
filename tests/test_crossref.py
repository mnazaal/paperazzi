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


# ---------------------------------------------------------------------------
# Authors Crossref does not describe as `given` + `family`
# ---------------------------------------------------------------------------


def _record_from(message: dict) -> dict:
    result = fetch_crossref_record("10.1000/x", fetch_text=lambda _: json.dumps({"message": message}))
    assert result is not None
    return result


def test_an_organizational_author_is_not_dropped() -> None:
    """Crossref gives a corporate author a `name`, not `family`/`given`.

    Standards bodies, consortia and working groups were silently dropped, so a
    W3C or IETF reference captured with *no author at all* — and `pzi library check`
    then flagged it `author_unknown`.
    """
    result = _record_from({
        "title": ["HTML Standard"],
        "author": [{"name": "World Wide Web Consortium"}],
    })

    assert result["authors"] == ["World Wide Web Consortium"]


def test_a_mononym_author_is_kept() -> None:
    result = _record_from({
        "title": ["A Paper"],
        "author": [{"family": "Plato"}, {"given": "Prince"}],
    })

    assert result["authors"] == ["Plato", "Prince"]


def test_an_author_suffix_is_kept() -> None:
    """`King, Jr., Martin Luther` is a different person from `King, Martin Luther`."""
    result = _record_from({
        "title": ["A Paper"],
        "author": [{"given": "Martin Luther", "family": "King", "suffix": "Jr."}],
    })

    assert result["authors"] == ["King Jr., Martin Luther"]


# ---------------------------------------------------------------------------
# Which Crossref date is the publication year
# ---------------------------------------------------------------------------


def test_issued_wins_over_the_deposit_date() -> None:
    """`created` is when the DOI record was deposited, not when the work appeared.

    A 1998 paper back-deposited in 2015 was captured as `year = {2015}`, which
    then failed `pzi library check` against every other source.
    """
    result = _record_from({
        "title": ["An Old Paper"],
        "issued": {"date-parts": [[1998]]},
        "created": {"date-parts": [[2015, 6, 1]]},
    })

    assert result["year"] == 1998


def test_posted_is_used_for_posted_content() -> None:
    result = _record_from({
        "title": ["A Preprint"],
        "posted": {"date-parts": [[2024, 3, 2]]},
        "created": {"date-parts": [[2024, 3, 5]]},
    })

    assert result["year"] == 2024


def test_a_printed_date_still_wins_over_issued() -> None:
    """`issued` is the earliest of the known dates, print included; when the
    print date is stated it is the one a citation uses."""
    result = _record_from({
        "title": ["A Paper"],
        "published-print": {"date-parts": [[2009]]},
        "issued": {"date-parts": [[2008]]},
    })

    assert result["year"] == 2009


def test_the_deposit_date_is_still_the_last_resort() -> None:
    result = _record_from({
        "title": ["A Paper"],
        "created": {"date-parts": [[2015, 6, 1]]},
    })

    assert result["year"] == 2015


#: What api.crossref.org actually returns for this DOI, verified live. Note that
#: `title` and `subtitle` arrive as *separate* arrays — the fixture above joins
#: them by hand, which is exactly why dropping `subtitle` went unnoticed: the
#: real record's title is the bare word "MapReduce".
_MAPREDUCE_LIVE_SHAPE = {
    "message": {
        "DOI": "10.1145/1327452.1327492",
        "title": ["MapReduce"],
        "subtitle": ["simplified data processing on large clusters"],
        "author": [{"given": "Jeffrey", "family": "Dean"}],
        "published-print": {"date-parts": [[2008, 1, 1]]},
        "container-title": ["Communications of the ACM"],
        "volume": "51",
        "issue": "1",
        "page": "107-113",
        "publisher": "Association for Computing Machinery (ACM)",
        "ISSN": ["0001-0782", "1557-7317"],
    }
}


def test_crossref_subtitle_is_joined_into_the_title() -> None:
    """Dropping it stored `title = {MapReduce}` — a citation naming no subject."""
    result = fetch_crossref_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_MAPREDUCE_LIVE_SHAPE),
    )

    assert result is not None
    assert result["title"] == "MapReduce: simplified data processing on large clusters"


def test_crossref_carries_volume_issue_and_pages() -> None:
    """Crossref reports all of these; the normalizer read none of them."""
    result = fetch_crossref_record(
        "10.1145/1327452.1327492",
        fetch_text=lambda _: json.dumps(_MAPREDUCE_LIVE_SHAPE),
    )

    assert result is not None
    assert result["volume"] == "51"
    assert result["number"] == "1"
    assert result["pages"] == "107--113"
    assert result["publisher"] == "Association for Computing Machinery (ACM)"
    assert result["issn"] == "0001-0782"


def test_crossref_title_survives_an_absent_or_empty_subtitle() -> None:
    for subtitle in ({}, {"subtitle": []}, {"subtitle": [""]}, {"subtitle": ["  "]}):
        payload = {"message": {**_MAPREDUCE_LIVE_SHAPE["message"], **subtitle}}
        payload["message"].pop("subtitle", None) if not subtitle else None
        result = fetch_crossref_record("10.1145/x", fetch_text=lambda _: json.dumps(payload))
        assert result is not None
        assert result["title"] == "MapReduce", subtitle


def test_crossref_does_not_double_append_a_subtitle_already_in_the_title() -> None:
    """Some deposits repeat the subtitle inside `title`."""
    payload = {
        "message": {
            **_MAPREDUCE_LIVE_SHAPE["message"],
            "title": ["MapReduce: simplified data processing on large clusters"],
        }
    }
    result = fetch_crossref_record("10.1145/x", fetch_text=lambda _: json.dumps(payload))

    assert result is not None
    assert result["title"] == "MapReduce: simplified data processing on large clusters"
