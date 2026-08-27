from pzi.translation_server import (
    extract_pdf_attachments,
    fetch_search_translations,
    fetch_web_translations,
    normalize_translation_item,
)
from tests.stub_translation_server import stub_translation_server, translation_item


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


def test_an_arxiv_id_from_the_translator_is_stored_without_its_prefix() -> None:
    """Zotero's `archiveID` carries the `arXiv:` prefix, which `archiveprefix`
    already supplies — stored raw, the citation reads "arXiv:arXiv:2301.12345"."""
    from pzi.translation_server import _extract_arxiv_id

    assert _extract_arxiv_id({"archiveID": "arXiv:2301.12345"}) == "2301.12345"
    assert _extract_arxiv_id({"archiveID": "2301.12345v3"}) == "2301.12345"
    assert _extract_arxiv_id({"extra": "arXiv: arXiv:2301.12345"}) == "2301.12345"
    assert _extract_arxiv_id({"archiveID": "not-an-arxiv-id"}) is None


def test_translation_item_carries_volume_issue_and_pages() -> None:
    """The translation-server is the *primary* capture path.

    Zotero translators report these for any journal article, so a capture that
    went through a translator — the common case — was still losing the volume,
    issue and page range that the Crossref fallback also dropped.
    """
    result = normalize_translation_item(
        {
            "itemType": "journalArticle",
            "title": "MapReduce",
            "publicationTitle": "Communications of the ACM",
            "volume": "51",
            "issue": "1",
            "pages": "107-113",
            "publisher": "ACM",
            "ISSN": "0001-0782",
            "ISBN": "978-1-4503-0000-0",
        }
    )

    record = result["record"]
    assert record["volume"] == "51"
    assert record["number"] == "1"
    assert record["pages"] == "107--113"
    assert record["publisher"] == "ACM"
    assert record["issn"] == "0001-0782"
    assert record["isbn"] == "978-1-4503-0000-0"


def test_translation_item_omits_detail_keys_it_was_not_given() -> None:
    """An absent key is a gap to fill later, not an empty value to write."""
    result = normalize_translation_item({"itemType": "journalArticle", "title": "T"})

    for key in ("volume", "number", "pages", "publisher", "issn", "isbn"):
        assert key not in result["record"], key


# --- Contract against the pinned upstream ------------------------------------


def test_a_multiple_choice_response_is_explained_not_reported_as_a_server_error() -> None:
    """`/web` answers 300 with a selection map, not an item list.

    From `src/webEndpoint.js` at the pinned commit: when a page yields several
    candidate items the session is stored and the response is 300 Multiple
    Choices. pzi does not select among them, so finding nothing is correct — but
    `safe_api_call` reported every status as bare `HTTP <code>`, so the user was
    told "HTTP 300", which reads as a broken translation server rather than as a
    page that needs a more specific URL.
    """
    import urllib.error

    from pzi.add_planning import safe_api_call

    def _multiple_choices():
        raise urllib.error.HTTPError(
            "http://127.0.0.1:1969/web", 300, "Multiple Choices", {}, None  # type: ignore[arg-type]
        )

    errors: list[str] = []
    assert safe_api_call(_multiple_choices, errors=errors) == []
    assert len(errors) == 1
    assert "several possible items" in errors[0]
    assert "HTTP 300" not in errors[0]


# --- Stub server dispatches on the request path (D2) -------------------------


def test_stub_translation_server_rejects_a_matching_body_on_the_wrong_path() -> None:
    """Pins the stub's own contract, since nothing else exercises it directly.

    Before this fix `do_POST` matched a needle in the body regardless of
    `self.path`, so a client bug that posted an identifier to the wrong
    endpoint — or a typo'd path string — still got back a 200 with the
    matching item. A request to a path that is neither `/web` nor `/search`
    must 404 even when the body would otherwise match.
    """
    import urllib.error
    import urllib.request

    item = translation_item(title="A Stub Paper")
    with stub_translation_server({"10.1/x": item}) as server_url:
        for path in ("/web", "/search"):
            req = urllib.request.Request(
                f"{server_url}{path}", data=b"10.1/x", method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200

        req = urllib.request.Request(
            f"{server_url}/wrong-endpoint", data=b"10.1/x", method="POST"
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected HTTPError 404 for an unknown POST path")


def test_stub_translation_server_get_only_answers_the_health_probe_root() -> None:
    """`is_ts_reachable` GETs the root; `/web` and `/search` never see GET."""
    import urllib.error
    import urllib.request

    with stub_translation_server({}) as server_url:
        with urllib.request.urlopen(server_url) as resp:
            assert resp.status == 200

        try:
            urllib.request.urlopen(f"{server_url}/web")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected HTTPError 404 for GET /web")


def test_other_statuses_keep_their_terse_form() -> None:
    import urllib.error

    from pzi.add_planning import safe_api_call

    def _server_error():
        raise urllib.error.HTTPError(
            "http://127.0.0.1:1969/web", 500, "Server Error", {}, None  # type: ignore[arg-type]
        )

    errors: list[str] = []
    safe_api_call(_server_error, errors=errors)
    assert errors == ["HTTP 500"]


def test_a_bot_challenge_page_is_not_a_translation_result() -> None:
    """An anti-bot interstitial must never be captured as a paper.

    Verbatim from the live server on 2026-08-27, for
    `https://openreview.net/forum?id=zUbBaWAM1Q`: HTTP 200, one item, a real
    title string. Every gate downstream passed it — `identifies_a_paper` sees a
    title, and so does `_answers_the_lookup` — so a dry run reported
    `status: ok` and would have written
    `@article{unknownxxxxverifying, title = {Verifying your browser | OpenReview}}`.
    17 of 152 links in one real batch were OpenReview, so this is not a corner.

    Dropped here, at the boundary where every consumer gets its results, rather
    than at the three translation-server return sites in `add_planning`: a
    predicate applied at three sites is the shape this project keeps
    regressing. Dropping it also stops it becoming a *floor* — the thin-record
    fallback would otherwise write exactly this record when nothing better
    answered.
    """
    def fake_post_json(endpoint: str, payload: object) -> object:
        return [
            {
                "key": "IZNQC6G9",
                "version": 0,
                "itemType": "webpage",
                "url": "https://openreview.net/challenge?redirect=%2Fforum%3Fid%3DzUbBaWAM1Q",
                "title": "Verifying your browser | OpenReview",
                "accessDate": "2026-08-27T10:48:29Z",
            }
        ]

    results = fetch_web_translations(
        "https://openreview.net/forum?id=zUbBaWAM1Q",
        server_url="http://127.0.0.1:1969",
        post_json=fake_post_json,
    )

    assert results == []


def test_a_real_paper_alongside_a_challenge_page_still_survives() -> None:
    """The filter drops challenge items, not the whole response."""
    def fake_post_json(endpoint: str, payload: object) -> object:
        return [
            {"itemType": "webpage", "title": "Just a moment...", "url": "https://x.test/cdn-cgi/challenge-platform/y"},
            {"itemType": "journalArticle", "title": "Graph Parsers", "url": "https://example.com/paper"},
        ]

    results = fetch_web_translations(
        "https://example.com/paper",
        server_url="http://127.0.0.1:1969",
        post_json=fake_post_json,
    )

    assert [r["record"]["title"] for r in results] == ["Graph Parsers"]


def test_a_paper_whose_title_merely_mentions_attention_is_kept() -> None:
    """The markers must be interstitial phrases, not common title words.

    `Attention Is All You Need` is in this very library; a substring match on
    "attention" would have refused it.
    """
    def fake_post_json(endpoint: str, payload: object) -> object:
        return [{"itemType": "conferencePaper", "title": "Attention Is All You Need",
                 "url": "https://arxiv.org/abs/1706.03762"}]

    results = fetch_web_translations(
        "https://arxiv.org/abs/1706.03762",
        server_url="http://127.0.0.1:1969",
        post_json=fake_post_json,
    )

    assert [r["record"]["title"] for r in results] == ["Attention Is All You Need"]
