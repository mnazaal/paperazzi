import pzi.promote_service as promote_service
from pzi.add_service import add_record_to_bib
from pzi.promote_service import (
    _published_candidate_diagnostics,
    _score_published_candidate,
    _select_best_published_candidate,
    promote_bib,
)

#: The real candidate set for `jing-understanding-2022`, taken from a
#: `--verbose` run against the user's library on 2026-08-14. The arXiv record
#: outscored the actual ICLR version 105 to 103 and was promoted in its place.
_ARXIV_CANDIDATE = {
    "title": "Understanding Dimensional Collapse in Contrastive Self-supervised Learning",
    "authors": ["Li Jing", "Pascal Vincent", "Yann LeCun", "Yuandong Tian"],
    "year": 2021,
    "venue": "arXiv (Cornell University)",
    "doi": "10.48550/arxiv.2110.09348",
}
_ICLR_CANDIDATE = {
    "title": "Understanding Dimensional Collapse in Contrastive Self-supervised Learning",
    "authors": ["Li Jing", "Pascal Vincent", "Yann LeCun", "Yuandong Tian"],
    "year": 2022,
    "venue": "ICLR 2022 Poster",
}
_PREPRINT = {
    "citekey": "jing-understanding-2022",
    "title": "Understanding Dimensional Collapse in Contrastive Self-supervised Learning",
    "authors": ["Jing, Li", "Vincent, Pascal", "LeCun, Yann", "Tian, Yuandong"],
    "year": 2022,
    "arxiv_id": "2110.09348",
}


def test_an_arxiv_doi_does_not_earn_the_publisher_doi_bonus() -> None:
    """The `+2` for a DOI is evidence of a *publisher* record, not any DOI.

    arXiv mints its own DataCite DOI under `10.48550/`, so an arXiv candidate
    collected the bonus while the real published version — which often carries
    no DOI at all, because DBLP and OpenReview do not report one — did not. On
    a real preprint that was the entire margin: 105 against 103.
    """
    with_arxiv_doi = _score_published_candidate(_PREPRINT, _ARXIV_CANDIDATE)
    without_doi = _score_published_candidate(
        _PREPRINT, {**_ARXIV_CANDIDATE, "doi": None}
    )
    assert with_arxiv_doi == without_doi, "an arXiv DOI still earns the bonus"

    publisher_doi = _score_published_candidate(
        _PREPRINT, {**_ARXIV_CANDIDATE, "doi": "10.1007/978-3-031-19809-0_38"}
    )
    assert publisher_doi > without_doi, "a real publisher DOI must still count"


def test_the_published_version_wins_over_the_preprint_that_outscored_it() -> None:
    """The regression in full: both defects, and the outcome that matters.

    This is not "the arXiv record is refused" — it is that the *correct* answer
    was in the candidate list all along and lost. Selection must now return the
    ICLR record.
    """
    selected = _select_best_published_candidate(
        _PREPRINT, [_ARXIV_CANDIDATE, _ICLR_CANDIDATE]
    )
    assert selected is not None
    assert selected["venue"] == "ICLR 2022 Poster"


def test_a_candidate_that_is_itself_a_preprint_is_never_selected() -> None:
    """With no published version among the candidates, promote finds nothing.

    The honest outcome for a paper that was never published: `no candidate`,
    not the preprint relabelled as its own published version.
    """
    assert _select_best_published_candidate(_PREPRINT, [_ARXIV_CANDIDATE]) is None
    # A venue alone is not publication: DBLP and Semantic Scholar report arXiv
    # records as `CoRR`, with no DOI and no arXiv id to give them away.
    corr = {**_ARXIV_CANDIDATE, "venue": "CoRR", "doi": None, "publisher": "arXiv"}
    assert _select_best_published_candidate(_PREPRINT, [corr]) is None


def test_a_rejected_preprint_candidate_is_reported_not_silently_dropped() -> None:
    """A filter with no output is indistinguishable from a broken one."""
    lines = _published_candidate_diagnostics(
        _PREPRINT, [_ARXIV_CANDIDATE, _ICLR_CANDIDATE]
    )
    assert any(line.startswith("selected") and "ICLR" in line for line in lines)
    assert any("rejected (preprint)" in line for line in lines), lines

    # And when *everything* found was a preprint, the diagnostics still explain
    # the empty result rather than leaving it unaccounted for.
    only_preprints = _published_candidate_diagnostics(_PREPRINT, [_ARXIV_CANDIDATE])
    assert only_preprints and all(
        "rejected (preprint)" in line for line in only_preprints
    )


def test_candidate_scoring_matches_authors_across_name_formats() -> None:
    # "Smith, John" (preprint) vs "John Smith" (candidate) must count as an
    # author match — family-name normalized, not exact-string.
    preprint = {"title": "Graph Nets", "authors": ["Smith, John"], "year": 2024}
    candidate = {"title": "Graph Nets", "authors": ["John Smith"], "year": 2024}
    same_format = {"title": "Graph Nets", "authors": ["Smith, John"], "year": 2024}

    assert _score_published_candidate(preprint, candidate) == _score_published_candidate(
        preprint, same_format
    )
    # No author overlap scores strictly lower than a full author overlap.
    no_overlap = {"title": "Graph Nets", "authors": ["Jane Doe"], "year": 2024}
    assert _score_published_candidate(preprint, candidate) > _score_published_candidate(
        preprint, no_overlap
    )


def test_the_published_entry_does_not_inherit_arxiv_as_its_publisher() -> None:
    """A promoted paper must not keep the preprint's publisher and locator.

    Better BibTeX writes `publisher = {arXiv}` and `number = {arXiv:2110.09348}`
    onto its arXiv entries. The merge starts from the preprint, so both rode
    into the published record — an ICLR paper published by arXiv, carrying the
    preprint's identifier as its issue number. Invisible until the selection
    fixes landed, because the "published" record used to *be* the arXiv one.
    """
    preprint = {
        **_PREPRINT,
        "publisher": "arXiv",
        "number": "arXiv:2110.09348",
        "volume": "abs/2110.09348",
    }
    merged = promote_service._merge_published_metadata(preprint, _ICLR_CANDIDATE)

    assert "publisher" not in merged
    assert "number" not in merged
    assert "volume" not in merged
    assert merged["venue"] == "ICLR 2022 Poster"


def test_a_year_glued_to_the_venue_name_is_still_a_preprint_venue() -> None:
    """DBLP writes `CoRR2019`, which a plain punctuation split leaves as one
    token — so it matched neither `corr` nor `corr `, and it was the last arXiv
    record to survive the filter on a real 20-entry slice. The split must not
    over-fire on a real venue that happens to end in digits."""
    glued = {**_ARXIV_CANDIDATE, "venue": "CoRR2019", "doi": None}
    assert _select_best_published_candidate(_PREPRINT, [glued]) is None

    real = {**_ICLR_CANDIDATE, "venue": "Nature2020"}
    assert _select_best_published_candidate(_PREPRINT, [real]) is not None


def test_a_different_paper_by_the_same_authors_is_not_a_published_version() -> None:
    """The composite gate cannot catch this; the title floor can.

    Both of these are real: they survived the preprint filter and cleared the
    confidence threshold of 60 on a 20-entry slice of the user's library,
    because a perfect author match dragged a weak title over the line. Every one
    of the 12 *correct* promotions in that run scored `title 100`; these scored
    62 and 70.
    """
    from pzi.resolution_match import score_match

    pairs = [
        (
            "Adaptivity and Confounding in Multi-Armed Bandit Experiments",
            "On Adaptivity and Confounding in Contextual Bandit Experiments",
        ),
        (
            "RecSim NG: Toward Principled Uncertainty Modeling for Recommender Ecosystems",
            "Demonstrating Principled Uncertainty Modeling for Recommender "
            "Ecosystems with RecSim NG",
        ),
    ]
    authors = ["Chao Qin", "Daniel Russo"]
    for preprint_title, other_paper in pairs:
        match = score_match(
            {"title": preprint_title, "authors": authors},
            {"title": other_paper, "authors": authors},
        )
        assert match["author_similarity"] == 100, "the authors really do match"
        assert match["title_similarity"] < promote_service._MIN_TITLE_SIMILARITY, (
            f"{other_paper!r} would still be accepted as a published version"
        )


def test_the_real_published_version_still_clears_the_title_floor() -> None:
    """The floor must not cost the promotions that were right all along.

    A published version may retitle slightly — a subtitle appearing or a
    capitalisation change — and that has to survive.
    """
    from pzi.resolution_match import score_match

    authors = ["Li Jing", "Pascal Vincent"]
    for published_title in (
        "Understanding Dimensional Collapse in Contrastive Self-supervised Learning",
        "Understanding dimensional collapse in contrastive self-supervised learning",
    ):
        match = score_match(
            {"title": _PREPRINT["title"], "authors": authors},
            {"title": published_title, "authors": authors},
        )
        assert match["title_similarity"] >= promote_service._MIN_TITLE_SIMILARITY


def test_a_publisher_the_candidate_supplies_is_kept() -> None:
    """Only the *inherited* preprint values are dropped, never the real ones."""
    preprint = {**_PREPRINT, "publisher": "arXiv", "number": "arXiv:2110.09348"}
    candidate = {
        **_ICLR_CANDIDATE,
        "publisher": "Springer Nature Switzerland",
        "number": "7",
    }
    merged = promote_service._merge_published_metadata(preprint, candidate)

    assert merged["publisher"] == "Springer Nature Switzerland"
    assert merged["number"] == "7"


def _seed_bib_with_preprint(tmp_path, bib_path, config_path, **kwargs):
    record = {
        "citekey": "smith2024graph",
        "title": "Graph Parsers",
        "arxiv_id": "2401.12345",
        "year": 2024,
        "authors": ["Smith, Jane"],
        **kwargs,
    }
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record=record,
        bib_selector=None,
        dry_run=False,
    )


def _write_config(tmp_path, bib_path, **kwargs):
    config_path = tmp_path / "config.toml"
    app_extra = "\n".join(
        # ints unquoted so numeric keys like promote_confidence_threshold parse
        f"{k} = {v}" if isinstance(v, int) and not isinstance(v, bool) else f'{k} = "{v}"'
        for k, v in kwargs.items()
    )
    prefix = f"{app_extra}\n" if app_extra else ""
    config_path.write_text(
        f"""
{prefix}[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    return config_path


def _fake_search_with_venue(query: str, *, server_url: str):
    return [
        {
            "item_type": "journalArticle",
            "record": {
                "title": "Graph Parsers",
                "venue": "Journal of Parsing",
                "doi": "10.9/jop",
                "year": 2024,
                "authors": ["Smith, Jane"],
                "pdf_url": "https://example.com/paper.pdf",
            },
            "attachments": [],
        }
    ]


def _fake_search_no_venue(query: str, *, server_url: str):
    return [
        {
            "item_type": "preprint",
            "record": {
                "title": "Graph Parsers",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            "attachments": [],
        }
    ]


def test_promote_dry_run_does_not_write(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    before = bib_path.read_text()

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        keep_preprint=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "update"
    assert bib_path.read_text() == before


def test_promote_update_in_place(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["action"] == "update"
    assert item["published_citekey"] == "smith2024graph"
    text = bib_path.read_text()
    assert "journal = {Journal of Parsing}" in text
    assert "doi = {10.9/jop}" in text


def test_promote_mark_resolved_tags_and_skips_on_rerun(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    first = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        mark_resolved=True,
        fetch_search=_fake_search_with_venue,
    )
    assert first["summary"]["created"] == 1
    assert first["summary"]["marked_resolved"] == 1
    assert "promoted" in bib_path.read_text()  # preprint now carries the marker tag

    # Re-running skips the already-tagged preprint instead of re-promoting it.
    second = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        mark_resolved=True,
        fetch_search=_fake_search_with_venue,
    )
    assert second["summary"]["checked"] == 0
    assert second["summary"]["skipped_already_resolved"] == 1
    assert second["summary"]["created"] == 0


def test_promote_keep_preprint_creates_new_entry(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["action"] == "create"
    assert item["published_citekey"] is not None
    assert item["published_citekey"] != "smith2024graph"

    text = bib_path.read_text()
    assert "@article{" in text
    assert "@unpublished{" in text
    assert "journal = {Journal of Parsing}" in text
    assert "Published version:" in text
    assert "Preprint version:" in text


def test_promote_skips_when_published_already_exists(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph_jop",
            "title": "Graph Parsers",
            "venue": "Journal of Parsing",
            "doi": "10.9/jop",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    assert "already exists" in result["items"][0]["note"]


def test_promote_skips_low_confidence(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Totally Different Title",
                    "venue": "Journal of X",
                    "year": 2024,
                    "authors": ["Doe, John"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    assert "low confidence" in result["items"][0]["note"]


def test_promote_skips_non_preprints(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "doe2024vision",
            "title": "Vision",
            "venue": "CVPR",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert len(result["items"]) == 0


def test_promote_attaches_pdf_when_available(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_fetch_binary(url):
        return b"%PDF-1.4 test", "application/pdf"

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["pdf_attached"] is True


def test_promote_falls_back_to_oa_when_the_candidate_url_is_blocked(tmp_path, monkeypatch):
    """`promote` had the same one-URL defect as `add` and `pdf retry`.

    Its `_maybe_attach_pdf` pushed the candidate's `pdf_url` through every
    *transport* fallback, all of which retry that one URL, and reported
    `pdf_attached: False` with an open-access mirror one discovery step away.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, unpaywall_email="pzi-tests@example.org")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    monkeypatch.setattr(
        "pzi.pdf.fetch_unpaywall_pdf_url",
        lambda doi, *, email=None: "https://oa.example.org/mirror.pdf",
    )

    attempted: list[str] = []

    def fake_fetch_binary(url):
        attempted.append(url)
        if "oa.example.org" not in url:
            from urllib.error import HTTPError

            raise HTTPError(url, 403, "Forbidden", {}, None)
        return b"%PDF-1.4 from-OA-mirror", "application/pdf"

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["pdf_attached"] is True, result
    assert any("oa.example.org" in url for url in attempted), attempted


def test_promote_pdf_failure_still_updates_metadata(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_fetch_binary(url):
        raise ConnectionError("no")

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["action"] == "update"
    assert item["pdf_attached"] is False
    text = bib_path.read_text()
    assert "journal = {Journal of Parsing}" in text


# --- additional coverage tests ---


def test_promote_errors_when_bib_not_found(tmp_path):
    config_path = _write_config(tmp_path, tmp_path / "missing.bib")
    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="nonexistent",
        dry_run=False,
    )
    assert result["status"] == "error"
    assert len(result["errors"]) > 0


def test_promote_record_without_citekey_skipped(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    # Manually write a record without citekey
    bib_path.write_text("@article{},\n")

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 0


def test_promote_uses_s2_api_key(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, semantic_scholar_api_key="test-key")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    s2_calls = []

    def fake_s2(title):
        s2_calls.append(title)
        return {"title": "Graph Parsers", "venue": "Journal of Parsing", "year": 2024}, None

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        keep_preprint=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )

    assert s2_calls == ["Graph Parsers"]
    assert result["status"] == "ok"
    assert result["items"][0]["action"] == "update"


def test_promote_empty_query_skips_search(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "empty", "title": ""},
        bib_selector=None,
        dry_run=False,
    )

    search_calls = []

    def fake_search(q, *, server_url):
        search_calls.append(q)
        return []

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert search_calls == []
    assert result["status"] == "ok"


def test_promote_different_author_year_scoring(tmp_path):
    bib_path = tmp_path / "ml.bib"
    # Above the extra-author/off-by-one-year penalty, below the default bar of
    # 60: a threshold of 2 would pass no matter how the scoring drifted. Set via
    # config since `promote_bib`'s per-call override was removed as unused.
    config_path = _write_config(tmp_path, bib_path, promote_confidence_threshold=40)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    # Was "Graph Parsers Extended", which scores `title 67` —
                    # squarely in the band of the two real false positives this
                    # suite now pins (62 and 70). Titles are governed by
                    # `_MIN_TITLE_SIMILARITY` and tested there; what this case
                    # exists to exercise is the extra-author and year penalties,
                    # so it keeps those and drops the incidental title drift.
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "year": 2025,
                    "authors": ["Smith, Jane", "Doe, John"],
                },
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        keep_preprint=False,
        fetch_search=fake_search,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "update"


def test_promote_keep_preprint_pdf_failure(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_fetch_binary(url):
        raise ConnectionError("no")

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["action"] == "create"
    assert item["pdf_attached"] is False


def test_promote_dry_run_keep_preprint_no_pdf(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=True,
        fetch_search=_fake_search_with_venue,
    )

    item = result["items"][0]
    assert item["action"] == "create"
    assert item["pdf_attached"] is False


def test_promote_find_duplicate_by_title(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    # Add duplicate with same title but different citekey
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "other2024graph",
            "title": "Graph Parsers",
            "venue": "Journal of Parsing",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    assert "other2024graph" in result["items"][0]["note"]


# --- S2 error differentiation tests ---


def test_promote_s2_http_429_rate_limit_no_key(tmp_path):
    """HTTP 429 from S2 with no key configured → rate-limited message."""
    from urllib.error import HTTPError

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    class _FakeResp:
        def read(self): return b""
        def close(self): pass

    def fake_s2(title):
        raise HTTPError("http://s2", 429, "Too Many Requests", {}, _FakeResp())

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (rate-limited — configure" in note


def test_promote_s2_http_403_with_key(tmp_path):
    """HTTP 403 from S2 with key configured → check-key message."""
    from urllib.error import HTTPError

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, semantic_scholar_api_key="my-key")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    class _FakeResp:
        def read(self): return b""
        def close(self): pass

    def fake_s2(title):
        raise HTTPError("http://s2", 403, "Forbidden", {}, _FakeResp())

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (rate-limited — check API key" in note


def test_promote_s2_http_500_generic(tmp_path):
    """HTTP 500 from S2 → generic HTTP error message."""
    from urllib.error import HTTPError

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    class _FakeResp:
        def read(self): return b""
        def close(self): pass

    def fake_s2(title):
        raise HTTPError("http://s2", 500, "Server Error", {}, _FakeResp())

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (HTTP 500)" in note


def test_promote_s2_data_error_rate_limit_no_key(tmp_path):
    """S2 returns (None, 'Rate limit exceeded') with no key → rate-limited msg."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_s2(title):
        return None, "Rate limit exceeded"

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (rate-limited — configure" in note


def test_promote_s2_data_error_auth_with_key(tmp_path):
    """S2 returns (None, 'Authorization required') with key → auth msg."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, semantic_scholar_api_key="my-key")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_s2(title):
        return None, "Authorization required"

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (auth required)" in note


def test_promote_s2_summary_warning_multiple_rate_limits(tmp_path):
    """Two records with S2 rate-limit → s2_warning in summary."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path,
        citekey="jones2024graphs2",
        title="Graph Analyzers",
        arxiv_id="2401.99999",
    )

    def fake_s2(title):
        return None, "Rate limit exceeded"

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert result["status"] == "ok"
    assert "s2_warning" in result["summary"]
    assert "2 Semantic Scholar rate-limit failures" in result["summary"]["s2_warning"]


def test_promote_unexpected_error_isolated_per_record(tmp_path, monkeypatch):
    """A handler blowing up on one preprint must not abort the whole run.

    The loop's per-record guard turns the failure into an explainable skip
    (counted in ``summary['skipped_failed']``) and lets later preprints promote.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    for ck, arxiv, title in [
        ("alpha2024", "2401.00001", "Alpha Net"),
        ("beta2024", "2401.00002", "Beta Net"),
    ]:
        add_record_to_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record={
                "citekey": ck,
                "title": title,
                "arxiv_id": arxiv,
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            bib_selector=None,
            dry_run=False,
        )

    def _search(query: str, *, server_url: str):
        title, doi = ("Alpha Net", "10.9/alpha") if "Alpha" in query else ("Beta Net", "10.9/beta")
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": title,
                    "venue": "Journal of Parsing",
                    "doi": doi,
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    original = promote_service._handle_update_in_place

    def _flaky(*, preprint_record, **kwargs):
        if preprint_record.get("citekey") == "alpha2024":
            raise RuntimeError("kaboom")
        return original(preprint_record=preprint_record, **kwargs)

    monkeypatch.setattr(promote_service, "_handle_update_in_place", _flaky)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_search,
    )

    assert result["status"] == "ok"
    items = {it["preprint_citekey"]: it for it in result["items"]}
    assert "promotion failed" in items["alpha2024"]["note"]
    assert result["summary"]["skipped_failed"] == 1
    # The second preprint still promoted despite the first record raising.
    assert items["beta2024"]["action"] == "update"
    assert "doi = {10.9/beta}" in bib_path.read_text()


# === destructive write paths (2026-07 audit) ===


def _preprint_bib_text(extra_fields: str = "") -> str:
    """A raw @unpublished preprint carrying fields the record model does not
    model, so a rewrite-from-scratch is visible as field loss."""
    return (
        "@unpublished{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "  volume = {12},\n"
        "  pages = {3--14},\n"
        "  publisher = {Cold Spring Press},\n"
        f"{extra_fields}"
        "}\n"
    )


def test_promote_keep_preprint_inserts_instead_of_rewriting_the_preprint(tmp_path):
    """Keep-mode must insert the published version, never patch the preprint.

    The merged published record inherits the preprint's ``url`` — no provider
    normalizer emits ``canonical_url`` — so an identity-matched write plan turns
    the intended insert into an in-place update of the preprint itself, and the
    reported ``published_citekey`` ends up existing nowhere in the file.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path,
        bib_path,
        config_path,
        canonical_url="https://arxiv.org/abs/2401.12345",
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    item = result["items"][0]
    assert item["action"] == "create"
    published_ck = item["published_citekey"]
    assert published_ck != "smith2024graph"

    text = bib_path.read_text()
    # The published citekey the run reported must actually be in the file...
    assert f"{{{published_ck}," in text
    # ...and the preprint must still be the preprint: unpublished, and not
    # carrying the published venue/doi it never had.
    assert "@unpublished{smith2024graph," in text
    assert text.count("journal = {Journal of Parsing}") == 1
    assert text.count("doi = {10.9/jop}") == 1


def test_promote_rejects_a_candidate_matching_on_authors_alone(tmp_path):
    """Shared surnames alone must never clear the promotion gate.

    The gate scores on a coarse integer scale where three shared surnames hit
    the default threshold exactly, so a candidate with an unrelated title is
    written in — while the 0-100 ``score_match`` breakdown printed alongside it
    reports a title mismatch.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path,
        bib_path,
        config_path,
        authors=["Smith, Jane", "Doe, John", "Roe, Ann"],
    )

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "An Entirely Unrelated Study of Beetles",
                    "venue": "Journal of Coleoptera",
                    "doi": "10.9/beetle",
                    "year": 2019,
                    "authors": ["Smith, Jane", "Doe, John", "Roe, Ann"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["items"][0]["action"] == "skip"
    assert "low confidence" in result["items"][0]["note"]
    assert "10.9/beetle" not in bib_path.read_text()


def test_promote_applies_the_configured_default_threshold(tmp_path):
    """With no threshold in config, the gate is 60 — the loader's default.

    `promote_service` used to carry its own fallback of 3, left over from the
    pre-`score_match` feature-count scale. It was unreachable (`AppConfig` is a
    total TypedDict, so the loader always supplies the key), but a re-introduced
    independent default would reopen the gate to near-anything.

    The candidate below scores exactly 50: same title, disjoint authors, which
    `score_match` flags `chimeric` — the classic fabricated-author citation.
    50 is above 3 and below 60, so it is promoted under the stale fallback and
    skipped under the real default. That band is the point of this test.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)  # no promote_confidence_threshold
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_search(query: str, *, server_url: str):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "doi": "10.9/jop",
                    "year": 2024,
                    "authors": ["Doe, John"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["items"][0]["action"] == "skip"
    assert result["items"][0]["note"] == "low confidence (50 < 60)"
    assert "10.9/jop" not in bib_path.read_text(), "nothing may be written"


def test_promote_does_not_blank_fields_the_candidate_left_none(tmp_path):
    """A candidate key explicitly set to ``None`` must not clear a populated
    field — ``_openreview_normalize`` always emits ``doi: None``."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path, doi="10.1/preprint")

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "doi": None,
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["items"][0]["action"] == "update"
    text = bib_path.read_text()
    assert "doi = {10.1/preprint}" in text
    assert "journal = {Journal of Parsing}" in text


def test_promote_update_in_place_preserves_unmodelled_fields(tmp_path):
    """Promoting in place rewrites the entry from the record projection, which
    drops every BibTeX field the record model does not carry."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(_preprint_bib_text())

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "update"
    text = bib_path.read_text()
    assert "volume = {12}" in text
    assert "pages = {3--14}" in text
    assert "publisher = {Cold Spring Press}" in text
    assert "journal = {Journal of Parsing}" in text
    # The promoted entry is no longer unpublished; its type is resolved from the
    # published record rather than hardcoded.
    assert "@article{smith2024graph," in text


def test_promote_keep_preprint_note_preserves_unmodelled_preprint_fields(tmp_path):
    """Stamping the cross-reference note onto the preprint must not rewrite the
    rest of its entry away."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(_preprint_bib_text())

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "create"
    text = bib_path.read_text()
    assert "Published version:" in text
    assert "Preprint version:" in text
    assert "volume = {12}" in text
    assert "pages = {3--14}" in text
    assert "publisher = {Cold Spring Press}" in text


def test_promote_in_place_moves_the_venue_when_it_retypes_the_entry(tmp_path):
    """Retyping an entry must move its venue to the key the new type expects.

    `merge_projected_entry` writes the venue back to whichever key the entry
    already used — right for an ordinary update, wrong when promotion retypes a
    proceedings entry to `@article`, which would leave the journal name sitting
    in `booktitle`.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        "@inproceedings{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  booktitle = {Workshop on Parsing},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n"
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "update"
    text = bib_path.read_text()
    assert "@article{smith2024graph," in text
    assert "journal = {Journal of Parsing}" in text
    assert "booktitle" not in text


def test_promote_keep_preprint_note_preserves_a_booktitle_venue(tmp_path):
    """The preprint's note update must not move or drop its venue.

    A preprint filed as `@inproceedings` keeps its venue in `booktitle`. The
    projection round-trips venue through the record's single `venue` key, so a
    note update that merges twice reads back `journal` (absent), concludes the
    venue was cleared, and deletes `booktitle`.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        "@inproceedings{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  booktitle = {Workshop on Parsing},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n"
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "create"
    text = bib_path.read_text()
    assert "booktitle = {Workshop on Parsing}" in text


def test_promote_keep_preprint_writes_nothing_when_library_is_malformed(tmp_path):
    """The insert half of keep-mode commits before the note updates run, and
    only the note path refuses to patch a malformed library — so a broken block
    anywhere in the file leaves a committed entry behind a reported failure."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        _preprint_bib_text()
        + "\n@article{broken2024,\n"
        "  title = {Missing closing brace\n"
        "  author = {Someone},\n"
        "  year = {2024},\n"
        "}\n"
    )
    before = bib_path.read_text()

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["summary"]["created"] == 0
    assert bib_path.read_text() == before


def test_promote_reports_unreachable_providers_not_just_no_candidate(
    tmp_path, monkeypatch
):
    """A provider that could not be reached must not read as "no published version".

    The real title-search fetchers catch transport errors internally and return
    None, so promote's `except (OSError, ValueError)` around them could only
    ever fire for injected test fetchers. Offline, every provider returned None
    and the entry was skipped with a bare "no published candidate found" — a
    fixable outage presented as a settled answer.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _offline_fetch_text(*_args, **_kwargs):
        def _raise(_url: str) -> str:
            raise OSError("connection refused")

        return _raise

    monkeypatch.setattr(
        promote_service, "build_metadata_fetch_text", _offline_fetch_text
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=lambda *_a, **_kw: [],
    )

    assert result["status"] == "ok"
    note = result["items"][0]["note"]
    assert "provider errors" in note
    assert "crossref" in note
    assert result["summary"]["provider_errors"] > 0


def test_promote_still_accepts_a_published_doi_that_differs_from_the_preprint(tmp_path):
    """A preprint's DOI legitimately differs from the published version's.

    `check` now penalizes contradicting DOIs via the shared `score_match`, and
    `promote` uses that same function as its acceptance gate — so without the
    preprint exemption this promotion would be rejected as low confidence,
    breaking the command's whole purpose.
    """
    bib_path = tmp_path / "ml.bib"
    # A raised bar, so the DOI penalty is decisive: an otherwise perfect match
    # scores 100 and passes, but 100 - 25 = 75 would be rejected. At the default
    # bar of 60 the penalty would not change the outcome and this test would
    # pass whether or not the exemption existed.
    config_path = _write_config(tmp_path, bib_path, promote_confidence_threshold=80)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path, doi="10.48550/arXiv.2401.12345"
    )

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                    "doi": "10.1145/3372297",
                },
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        keep_preprint=False,
        fetch_search=fake_search,
    )

    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "update", result["items"][0]


def _fake_search_without_doi(query: str, *, server_url: str):
    """A published version whose provider record carries no DOI of its own."""
    return [
        {
            "item_type": "journalArticle",
            "record": {
                "title": "Graph Parsers",
                "venue": "Journal of Parsing",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            "attachments": [],
        }
    ]


def test_promote_in_place_reports_the_fields_it_removed(tmp_path):
    """`changed_fields` iterated the *updated* record only.

    A field the promotion deleted — the `eprint`, the preprint URL — is
    therefore absent from the record being iterated, so the one change the user
    most needs to see was applied and never named.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path,
        canonical_url="https://arxiv.org/abs/2401.12345",
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    changed = result["items"][0]["changed_fields"]
    assert "arxiv_id" in changed
    assert "canonical_url" in changed


def test_promote_in_place_leaves_a_backup(tmp_path):
    """It overwrites an entry with a different paper's metadata, with no undo."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    before = bib_path.read_text()

    promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == before
    assert bib_path.read_text() != before


def test_promote_drops_the_arxiv_doi_when_the_published_version_has_none(tmp_path):
    """`10.48550/arXiv.…` identifies the preprint, not the published paper.

    Inherited, it labels the promoted entry with the version it just stopped
    being — and a later `pzi check` resolves it straight back to the preprint.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path, doi="10.48550/arXiv.2401.12345",
    )

    promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_without_doi,
    )

    text = bib_path.read_text()
    assert "10.48550" not in text
    assert "journal = {Journal of Parsing}" in text


def test_promote_keeps_a_real_doi_the_published_version_supplied(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path, doi="10.48550/arXiv.2401.12345",
    )

    promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    text = bib_path.read_text()
    assert "doi = {10.9/jop}" in text
    assert "10.48550" not in text


def _fake_search_echoing_the_record(query: str, *, server_url: str):
    """A provider that returns the very entry it was asked about."""
    return [
        {
            "item_type": "journalArticle",
            "record": {
                "title": "An Ordinary Paper",
                "doi": "10.7777/ordinary",
                "year": 2018,
                "authors": ["Doe, John"],
                "venue": "Journal of Ordinary Things",
            },
            "attachments": [],
        }
    ]


def test_promote_does_not_fork_an_entry_that_merely_lacks_a_venue(tmp_path):
    """A venue-less entry with a real publisher DOI is not a preprint.

    `is_preprint` returns True for *any* record with no `venue`, which is a
    large share of an ordinary library. Selecting on it meant promotion forked
    a second entry out of a plain @article that happened to have no `journal`
    field — creating exactly the duplicate `pzi library dedupe` exists to report.

    `update_service` documents this hazard and refuses to use `is_preprint` for
    the same reason; this pins that `promote` agrees.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "venueless2018",
            "title": "An Ordinary Paper",
            "doi": "10.7777/ordinary",
            "year": 2018,
            "authors": ["Doe, John"],
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_echoing_the_record,
    )

    assert result["status"] == "ok"
    assert result["items"] == [], f"venue-less non-preprint was promoted: {result['items']}"
    assert bib_path.read_text().count("@article") == 1


def _distinct_published_candidate(query: str, *, server_url: str):
    """A published version per preprint, so all three really are promoted.

    The resolver's query is "<title> <authors> <year>", so the title is
    recovered from it rather than assumed to be the whole string — a candidate
    titled with the query scores 30 and falls below the confidence gate.
    """
    title = query.split(" Smith,")[0]
    return [
        {
            "item_type": "journalArticle",
            "record": {
                "title": title,
                "venue": "Journal of Parsing",
                "doi": f"10.9/jop-{title.split()[-1]}",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            "attachments": [],
        }
    ]


def test_promoting_several_preprints_leaves_one_backup(tmp_path):
    """`backup_path_for` was called inside the per-preprint loop.

    It never reuses a name and `update_bib_entry` copies the whole file, so a
    `--replace` run wrote a full copy of the library per promoted entry:
    against a 15.8 MB library, promoting 100 preprints leaves roughly 1.6 GB of
    `.bak` files that nothing ever cleans up. One run, one undo.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    for index in range(3):
        add_record_to_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record={
                "citekey": f"smith{index}graph",
                # Distinct papers: one shared title would make candidates 2 and 3
                # "already exists as smith0graph", which is correct behaviour but
                # would leave nothing for this test to measure.
                "title": f"Graph Parsers Volume {index}",
                "arxiv_id": f"2401.1234{index}",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            bib_selector=None,
            dry_run=False,
        )
    before = bib_path.read_text()

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_distinct_published_candidate,
    )

    promoted = [item for item in result["items"] if item["action"] == "update"]
    assert len(promoted) == 3, result["items"]
    backups = list(tmp_path.glob("*.bak*"))
    assert len(backups) == 1, [b.name for b in backups]
    # And it holds the library as it was before the run, not a half-promoted one.
    assert backups[0].read_text() == before
    # Every promoted entry points at that one file, so the undo is findable.
    assert {item.get("backup_path") for item in promoted} == {str(backups[0])}
