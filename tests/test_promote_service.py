import re
from contextlib import contextmanager
from datetime import timedelta

import pytest

import pzi.promote_service as promote_service
import pzi.update_service as update_service
from pzi import promote_ledger
from pzi.add_service import add_record_with_bib
from pzi.bib_repository import ConcurrentEditError, StalePlanError
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.errors import REASON_UNAVAILABLE
from pzi.http_status import status_for_service_result
from pzi.pdf import PdfSourceOutcome
from pzi.promote_planning import (
    _published_candidate_diagnostics,
    _score_published_candidate,
    _select_best_published_candidate,
)
from pzi.promote_service import promote_bib
from pzi.update_service import update_bib


def _add_via_config(*, config_path, home_dir, record, bib_selector=None, dry_run=False):
    """Seed the bib through the live write path (`add_record_with_bib`).

    Inlines what the now-deleted single-record capture wrapper used to do — resolve
    the config's bib, then delegate — so these `promote_bib`/`update_bib`
    fixtures keep seeding their library exactly as production would capture it.
    """
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    assert not isinstance(resolved, BibResolutionFailure)
    config, bib = resolved
    return add_record_with_bib(
        bib=bib,
        record=record,
        dry_run=dry_run,
        browser_hook=config.get("browser_hook", True),
        citekey_format=config.get("citekey_format"),
        pdf_filename_format=config.get("pdf_filename_format"),
        file_path_style=config.get("pdf_file_path_style", "absolute"),
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
    _add_via_config(
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
    _add_via_config(
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
    _add_via_config(
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
    _add_via_config(
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
    _add_via_config(
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
        _add_via_config(
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
    being — and a later `pzi library check` resolves it straight back to the preprint.
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
    _add_via_config(
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
        _add_via_config(
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


# === the sweep contract, spanning `promote` and `update` (2026-08-23 audit) ===
#
# Both services run one library-wide sweep, isolate each record behind a broad
# `except Exception`, and hand the same dict to three front ends. So the two
# rules below belong to *the sweep*, not to either service: `promote` had
# neither, and reported a run in which every promotion raised as
# `status: "ok"` — `pzi.promote()` returned normally and `POST /promote`
# answered 200.


def _seed_two_preprints(tmp_path, config_path) -> None:
    for index in (1, 2):
        _add_via_config(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record={
                "citekey": f"smith2024graph{index}",
                "title": f"Graph Parsers Volume {index}",
                "arxiv_id": f"2401.1234{index}",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            bib_selector=None,
            dry_run=False,
        )


def _run_promote_with_failing_record(tmp_path, monkeypatch, exc: Exception):
    """`promote_bib` over two preprints where every write raises *exc*."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_two_preprints(tmp_path, config_path)

    def _raise(**kwargs):
        raise exc

    monkeypatch.setattr(promote_service, "_handle_update_in_place", _raise)
    return promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_distinct_published_candidate,
    )


def _run_update_with_failing_record(tmp_path, monkeypatch, exc: Exception):
    """`update_bib` over two records where every plan raises *exc*."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_two_preprints(tmp_path, config_path)

    def _raise(*args, **kwargs):
        raise exc

    monkeypatch.setattr(update_service, "_plan_update_for_record", _raise)
    return update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_distinct_published_candidate,
    )


#: Each sweep, its harness, and the write-race failures its write path can
#: actually raise. `update` writes through `update_bib_entry` and previews
#: through `preview_write_plan`, and only the latter refuses — as a
#: `StalePlanError` (see the carve-out in `update_service`). `promote` also
#: forks an entry through a batch session, which refuses a citekey that
#: appeared mid-run with `ConcurrentEditError`.
_SWEEPS = [
    ("promote", _run_promote_with_failing_record, (StalePlanError, ConcurrentEditError)),
    ("update", _run_update_with_failing_record, (StalePlanError,)),
]
_SWEEP_RUNNERS = [(name, run) for name, run, _races in _SWEEPS]
_SWEEP_RACES = [
    (name, run, race)
    for name, run, races in _SWEEPS
    for race in races
]


@pytest.mark.parametrize(
    "sweep_name,run_sweep", _SWEEP_RUNNERS, ids=[name for name, _run in _SWEEP_RUNNERS]
)
def test_a_sweep_where_every_record_failed_is_not_ok(sweep_name, run_sweep, tmp_path, monkeypatch):
    """No item done is `status: "error"`, with a `reason` both front ends map.

    Without the `reason` the HTTP route falls back to 400, which
    `http_status.status_for_service_result` documents as "a bug in that
    service".
    """
    result = run_sweep(tmp_path, monkeypatch, RuntimeError("kaboom"))

    assert result["status"] == "error", f"{sweep_name} reported a wholly-failed run as ok"
    assert result["reason"] == REASON_UNAVAILABLE
    assert status_for_service_result(result) == 503
    assert len(result["items"]) == 2
    assert all(item.get("failed") for item in result["items"])
    assert len(result["errors"]) == 2, result["errors"]
    assert all("kaboom" in error for error in result["errors"])


@pytest.mark.parametrize(
    "sweep_name,run_sweep,race_error",
    _SWEEP_RACES,
    ids=[f"{name}-{race.__name__}" for name, _run, race in _SWEEP_RACES],
)
def test_a_sweep_that_loses_a_write_race_stops_instead_of_skipping_the_record(
    sweep_name, run_sweep, race_error, tmp_path, monkeypatch
):
    """Losing the race is not a per-record fault, so the broad catch must not eat it.

    The bib moved underneath the run: every remaining record was planned
    against a snapshot that no longer describes the file, so continuing to
    write is unsafe. Swallowed, it read as N separate "promotion failed" notes
    on a run that still called itself ok.
    """
    with pytest.raises(race_error):
        run_sweep(tmp_path, monkeypatch, race_error("the bib changed under this run"))


def _two_preprints_bib_text() -> str:
    return "".join(
        f"@unpublished{{smith2024graph{n},\n"
        f"  title = {{Graph Parsers Volume {n}}},\n"
        f"  author = {{Smith, Jane}},\n"
        f"  year = {{2024}},\n"
        f"  eprint = {{2401.1234{n}}},\n"
        f"  archiveprefix = {{arXiv}},\n"
        f"}}\n\n"
        for n in (1, 2)
    )


def _fake_search_echoing_title(query: str, *, server_url: str):
    """A published version of whichever preprint is being looked up."""
    title = query.split(" Smith")[0]
    return [
        {
            "item_type": "conferencePaper",
            "record": {
                "title": title,
                "venue": "Proceedings of Parsing",
                "doi": "10.9/pop." + title.rsplit(" ", 1)[-1],
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            "attachments": [],
        }
    ]


def test_promote_keep_mode_opens_one_batch_session_for_the_whole_run(
    tmp_path, monkeypatch
):
    """K promotions, one parse-and-serialise cycle — not K of them.

    Each `batch_write_session` takes the lock, reads the source, parses every
    entry, validates the round trip and re-serialises the library. Opening one
    per promoted preprint made a sweep's cost K full cycles over the whole file;
    measured on a synthetic 4,010-entry library, promoting 10 preprints took
    8.5 s with a session each and 1.1 s with one for the run.

    Two preprints, not one: with a single promotion the per-promotion and
    per-run counts are both 1, so the assertion could not fail.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(_two_preprints_bib_text())

    sessions = []
    real_session = promote_service.batch_write_session

    def _counting_session(*args, **kwargs):
        sessions.append(args[0] if args else kwargs.get("path"))
        return real_session(*args, **kwargs)

    monkeypatch.setattr(promote_service, "batch_write_session", _counting_session)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_echoing_title,
    )

    assert result["summary"]["created"] == 2, result["items"]
    assert len(sessions) == 1, f"{len(sessions)} batch sessions for 2 promotions"
    # Both promotions landed: one session must not mean one write.
    written = bib_path.read_text()
    assert written.count("Proceedings of Parsing") == 2, written
    assert "Published version: " in written


def _fake_search_echoing_title_with_pdf(query: str, *, server_url: str):
    """As `_fake_search_echoing_title`, but the candidate offers a PDF to fetch."""
    results = _fake_search_echoing_title(query, server_url=server_url)
    results[0]["record"]["pdf_url"] = "https://example.com/paper.pdf"
    return results


def test_promote_makes_no_network_call_while_holding_the_bib_lock(tmp_path, monkeypatch):
    """A sweep's provider calls must all be over before the run's session opens.

    That session holds the *exclusive* bib lock for as long as it is open, and
    `bib_repository.LOCK_TIMEOUT_SECONDS` is 300 — so a concurrent `pzi add`
    (the browser extension talking to `pzi server`) does not queue behind it, it
    gives up and fails. Promotion's network work is slow enough to reach that
    ceiling on its own: Semantic Scholar alone is gated at 6 s per request.

    Spans both of the sweep's network sites, not just the one that regressed:
    candidate discovery and the PDF download. Two preprints, not one, because
    with a single promotion there is no discovery left to do once the session is
    open and the assertion could not fail.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(_two_preprints_bib_text())

    holding_lock = False
    under_lock: list[str] = []
    real_session = promote_service.batch_write_session
    real_discovery = promote_service.find_published_candidate_with_diagnostics

    @contextmanager
    def _lock_tracking_session(*args, **kwargs):
        nonlocal holding_lock
        with real_session(*args, **kwargs) as session:
            holding_lock = True
            try:
                yield session
            finally:
                holding_lock = False

    def _tracked_discovery(**kwargs):
        if holding_lock:
            under_lock.append("candidate discovery")
        return real_discovery(**kwargs)

    def _tracked_pdf_fetch(**kwargs):
        if holding_lock:
            under_lock.append("pdf fetch")
        return PdfSourceOutcome(
            local_pdf_path=None, warning=None, errors=[], record=kwargs["record"]
        )

    monkeypatch.setattr(promote_service, "batch_write_session", _lock_tracking_session)
    monkeypatch.setattr(
        promote_service, "find_published_candidate_with_diagnostics", _tracked_discovery
    )
    monkeypatch.setattr(
        promote_service, "fetch_and_store_pdf_trying_sources", _tracked_pdf_fetch
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_echoing_title_with_pdf,
    )

    # The run has to have done the work, or "no network under the lock" is only
    # a statement about a run that promoted nothing.
    assert result["summary"]["created"] == 2, result["items"]
    assert bib_path.read_text().count("Proceedings of Parsing") == 2
    assert under_lock == [], f"network under the exclusive bib lock: {under_lock}"


# ── Item 576: a bounded, instrumented, breaker-guarded sweep ────────────
#
# `promote` walks 60% of a 22k-entry library at a per-candidate cost set by the
# slowest provider's polite interval, so an unbounded run is hours. These pin
# the four things that make it usable, and the two numbers the plan's remaining
# decisions are chosen from.


def _promote_library(tmp_path, count: int):
    """A library of `count` arXiv preprints, and a config naming it."""
    entries = "\n".join(
        f"@article{{pre{i:03d},\n  title = {{Preprint {i}}},\n  author = {{Doe, Jane}},\n"
        f"  year = {{2023}},\n  doi = {{10.48550/arXiv.2301.{i:05d}}}\n}}\n"
        for i in range(count)
    )
    bib = tmp_path / "lib.bib"
    bib.write_text(entries)
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "p"\npath = "{bib}"\ndefault = true\n')
    return str(config)


def _resolving_finder(calls: list):
    def finder(*, record, breaker=None, **kw):
        calls.append(record.get("citekey"))
        doi = str(record.get("doi") or "")
        return {
            "candidate": {
                "title": record.get("title"), "authors": ["Doe, Jane"], "year": 2024,
                "doi": doi.replace("10.48550/arXiv.", "10.1000/pub."),
                "journal": "Published Journal", "item_type": "journalArticle",
            },
            "confidence": 95, "provider_errors": [], "metadata_diagnostics": [],
            "reason": None,
        }
    return finder


def test_limit_stops_the_work_not_just_the_output(tmp_path, monkeypatch):
    """The bound has to prevent provider calls, or it saves nothing."""
    import pzi.promote_service as ps

    config = _promote_library(tmp_path, 10)
    calls: list = []
    monkeypatch.setattr(ps, "find_published_candidate_with_diagnostics", _resolving_finder(calls))

    result = ps.promote_bib(
        config_path=config, home_dir=str(tmp_path), bib_selector=None,
        dry_run=True, limit=4,
    )
    assert len(calls) == 4, "a bounded run must not query providers past its budget"
    assert result["summary"]["checked"] == 4


def test_a_bounded_run_reports_what_is_left(tmp_path, monkeypatch):
    """Without this a partial pass is indistinguishable from a complete one."""
    import pzi.promote_service as ps

    config = _promote_library(tmp_path, 10)
    monkeypatch.setattr(ps, "find_published_candidate_with_diagnostics", _resolving_finder([]))

    summary = ps.promote_bib(
        config_path=config, home_dir=str(tmp_path), bib_selector=None,
        dry_run=True, limit=4,
    )["summary"]
    assert summary["eligible"] == 10
    assert summary["remaining"] == 6

    full = ps.promote_bib(
        config_path=config, home_dir=str(tmp_path), bib_selector=None, dry_run=True,
    )["summary"]
    assert full["remaining"] == 0, "an unbounded run has nothing left over"


def test_the_run_reports_the_two_numbers_the_plan_decides_from(tmp_path, monkeypatch):
    """Resolve rate and seconds-per-candidate; see PLAN.md section F, steps 3-4."""
    import pzi.promote_service as ps

    config = _promote_library(tmp_path, 4)
    seen: list = []

    def half_resolving(*, record, breaker=None, **kw):
        seen.append(record.get("citekey"))
        if len(seen) % 2:
            return {"candidate": None, "provider_errors": [],
                    "metadata_diagnostics": [], "reason": None}
        return _resolving_finder([])(record=record, breaker=breaker, **kw)

    monkeypatch.setattr(ps, "find_published_candidate_with_diagnostics", half_resolving)
    summary = ps.promote_bib(
        config_path=config, home_dir=str(tmp_path), bib_selector=None, dry_run=True,
    )["summary"]
    assert summary["resolve_rate"] == 0.5
    assert summary["seconds_per_candidate"] >= 0


def test_every_verdict_is_streamed_as_it_is_reached(tmp_path, monkeypatch):
    """An interrupted sweep keeps its work only if the caller sees items live."""
    import pzi.promote_service as ps

    config = _promote_library(tmp_path, 5)
    monkeypatch.setattr(ps, "find_published_candidate_with_diagnostics", _resolving_finder([]))

    streamed: list = []
    result = ps.promote_bib(
        config_path=config, home_dir=str(tmp_path), bib_selector=None, dry_run=True,
        on_item=lambda item, done, total: streamed.append(item["preprint_citekey"]),
    )
    assert [i["preprint_citekey"] for i in result["items"]] == streamed
    assert len(streamed) == 5


def test_a_dead_provider_is_dropped_for_the_rest_of_the_sweep(tmp_path, monkeypatch):
    """Otherwise a provider that has stopped answering costs a timeout 13,462 times."""
    import pzi.promote_planning as pp
    from pzi.fetch_helpers import ProviderBreaker

    attempts: list = []

    def always_fails(_title, **kw):
        attempts.append(1)
        raise OSError("connection refused")

    breaker = ProviderBreaker(threshold=3)
    for _ in range(10):
        pp._try_provider(
            always_fails, "A Title", name="dblp",
            contact_email=None, provider_errors=[], breaker=breaker,
        )
    assert len(attempts) == 3, "the provider is dialled until it trips, then never again"
    assert breaker.is_open("dblp")


def test_a_skipped_provider_still_says_why_the_preprint_was_not_promoted(tmp_path):
    """A silent skip and a provider that answered 'unknown' must not look alike."""
    import pzi.promote_planning as pp
    from pzi.fetch_helpers import ProviderBreaker

    breaker = ProviderBreaker(threshold=1)
    breaker.record_failure("s2", "timeout")
    errors: list = []
    pp._try_provider(
        lambda *_a, **_k: None, "A Title", name="s2",
        contact_email=None, provider_errors=errors, breaker=breaker,
    )
    assert errors and "skipped" in errors[0]


# ── The provider that needed the breaker most was the one bypassing it ──
#
# Semantic Scholar is not routed through `_try_provider` (it alone returns a
# `(record, error)` pair), so item 576's breaker did not cover it. Keyless it is
# the slowest interval *and* the one that 429s, and a 429 is retried twice
# honouring Retry-After up to 30 s — up to a minute per candidate, paid on every
# candidate of a 13,462-entry sweep. Found by a real run taking >30 minutes for
# `--limit 100`.


def _s2_probe(monkeypatch, s2_fn):
    """Drive discovery with only S2 live, and count how often it is dialled."""
    import pzi.promote_planning as pp
    from pzi.fetch_helpers import ProviderBreaker

    monkeypatch.setattr(pp, "fetch_search_translations", lambda *a, **k: [])
    breaker = ProviderBreaker(threshold=3)
    record = {"citekey": "pre1", "title": "A Preprint Title", "authors": ["Doe, J"]}
    for _ in range(10):
        pp.find_published_candidate_with_diagnostics(
            record=record, server_url="http://127.0.0.1:1",
            fetch_search=lambda *a, **k: [],
            fetch_crossref=lambda *a, **k: None,
            fetch_openalex=lambda *a, **k: None,
            fetch_dblp=lambda *a, **k: None,
            fetch_openreview=lambda *a, **k: None,
            fetch_s2=s2_fn, s2_api_key=None, breaker=breaker,
        )
    return breaker


def test_a_rate_limited_s2_is_dropped_for_the_rest_of_the_sweep(monkeypatch) -> None:
    from urllib.error import HTTPError

    dials = []

    def s2(_title):
        dials.append(1)
        raise HTTPError("https://api.semanticscholar.org/x", 429, "Too Many", {}, None)

    breaker = _s2_probe(monkeypatch, s2)
    assert len(dials) == 3, "S2 is dialled until it trips, then skipped"
    assert breaker.is_open("s2")


def test_an_s2_quota_refusal_sent_as_http_200_also_trips_it(monkeypatch) -> None:
    """S2 answers a quota refusal with 200 and an error body, so status is not the test."""
    dials = []

    def s2(_title):
        dials.append(1)
        return None, "rate limit exceeded"

    breaker = _s2_probe(monkeypatch, s2)
    assert len(dials) == 3
    assert breaker.is_open("s2")


def test_an_s2_that_answers_is_never_tripped(monkeypatch) -> None:
    dials = []

    def s2(_title):
        dials.append(1)
        return {"title": "A Preprint Title", "venue": "A Journal", "year": 2024}, None

    breaker = _s2_probe(monkeypatch, s2)
    assert len(dials) == 10, "a working provider keeps being asked"
    assert not breaker.is_open("s2")


# ── Where a promote sweep's time actually goes ──────────────────────────
#
# A real run measured ~40 s per candidate against a ~6 s rate-limit floor, and
# the other 34 s could only be guessed at. Three cost models were built on that
# guess and all three were wrong, so the breakdown is now reported per candidate
# rather than inferred.


def test_the_slowest_provider_is_named_first(monkeypatch) -> None:
    import time

    import pzi.promote_planning as pp

    def costing(seconds):
        def fn(_title, **_kw):
            time.sleep(seconds)
            return None
        return fn

    result = pp.find_published_candidate_with_diagnostics(
        record={"citekey": "p1", "title": "A Preprint", "authors": ["Doe, J"]},
        server_url="http://127.0.0.1:1",
        fetch_search=lambda *a, **k: [],
        fetch_crossref=costing(0.01),
        fetch_openalex=costing(0.01),
        fetch_dblp=costing(0.20),
        fetch_openreview=costing(0.01),
        fetch_s2=lambda _t: (None, None),
        s2_api_key=None,
    )
    timing = next(
        (line for line in result.get("metadata_diagnostics") or []
         if line.startswith("timing:")),
        None,
    )
    assert timing is not None, result.get("metadata_diagnostics")
    # Slowest first, so the line answers "what should I fix" at a glance.
    after_dash = timing.split("—", 1)[1]
    assert after_dash.strip().startswith("dblp"), timing


def test_a_candidate_that_found_nothing_still_reports_its_cost() -> None:
    """The expensive case is the one that finds nothing, so it must be measured."""
    import pzi.promote_planning as pp

    result = pp.find_published_candidate_with_diagnostics(
        record={"citekey": "p1", "title": "A Preprint", "authors": ["Doe, J"]},
        server_url="http://127.0.0.1:1",
        fetch_search=lambda *a, **k: [],
        fetch_crossref=lambda *a, **k: None,
        fetch_openalex=lambda *a, **k: None,
        fetch_dblp=lambda *a, **k: None,
        fetch_openreview=lambda *a, **k: None,
        fetch_s2=lambda _t: (None, None),
        s2_api_key=None,
    )
    assert result["candidate"] is None
    assert any(
        line.startswith("timing:") for line in result.get("metadata_diagnostics") or []
    ), result.get("metadata_diagnostics")


# ── A replaced entry keeps a pointer to the preprint it came from ────────
#
# `--replace` stripped `arxiv_id`, which lost the one useful pointer back to the
# preprint. It cannot simply be kept: `has_preprint_identity` reads `arxiv_id`,
# and `eprint` round-trips into it, so a restored id re-selects the entry on
# every future sweep — the loop promotion exists to end. So it is kept under a
# name nothing classifies on.


def _promoted_record():
    from pzi.promote_service import _merge_published_metadata

    preprint = {
        "citekey": "yeh-decoupled-2021", "title": "Decoupled Contrastive Learning",
        "authors": ["Yeh, Chun-Hsiao"], "year": 2021, "arxiv_id": "2110.06848",
        "doi": "10.48550/arXiv.2110.06848", "venue": "arXiv",
    }
    candidate = {
        "title": "Decoupled Contrastive Learning", "authors": ["Yeh, Chun-Hsiao"],
        "year": 2022, "doi": "10.1007/978-3-031-19809-0_38",
        "venue": "Lecture Notes in Computer Science", "item_type": "conferencePaper",
    }
    return _merge_published_metadata(preprint, candidate)


def test_a_replaced_entry_keeps_a_pointer_to_its_preprint() -> None:
    merged = _promoted_record()
    assert merged.get("preprint_arxiv_id") == "2110.06848"
    assert "arxiv_id" not in merged, "the identity field must still go"


def test_the_kept_pointer_does_not_make_it_a_candidate_again() -> None:
    """The whole reason it is not stored as `arxiv_id` or `eprint`."""
    from pzi.identifiers import has_preprint_identity

    assert has_preprint_identity(_promoted_record()) is False


def test_the_pointer_survives_a_write_and_read_without_re_flagging() -> None:
    """Round-trip, because `eprint` round-trips *into* `arxiv_id` and this must not."""
    from pzi.bib_merge import resolve_entry_type
    from pzi.bibtex import bibtex_entry_to_record, record_to_bibtex_entry
    from pzi.identifiers import has_preprint_identity

    merged = _promoted_record()
    entry = record_to_bibtex_entry(merged, entry_type=resolve_entry_type(merged))
    assert "pzi-preprint-arxiv-id" in entry["fields"]
    assert "eprint" not in entry["fields"], (
        "must not render in a bibliography, and must not round-trip into arxiv_id"
    )
    back = bibtex_entry_to_record(entry)
    assert back.get("preprint_arxiv_id") == "2110.06848"
    assert has_preprint_identity(back) is False


def test_a_preprint_with_no_arxiv_id_gains_no_empty_pointer() -> None:
    from pzi.promote_service import _merge_published_metadata

    merged = _merge_published_metadata(
        {"citekey": "x-2021", "title": "T", "venue": "biorxiv.org"},
        {"title": "T", "year": 2022, "doi": "10.1000/pub.1", "venue": "A Journal"},
    )
    assert "preprint_arxiv_id" not in merged


# --- The negative-lookup ledger (item 578) --------------------------------
#
# `promote` is a periodic audit over ~13k candidates, most of which answer
# "not published yet". These pin that the answer is remembered, that it is
# only remembered when it is really an answer, and that the horizon governs
# when it is asked again.

def _search_finds_nothing(query: str, *, server_url: str):
    return []


def _provider_finds_nothing(title: str):
    return None


def _s2_finds_nothing(title: str):
    return (None, None)


def _s2_is_rate_limited(title: str):
    return (None, "rate limit exceeded")


#: Every provider stubbed to "asked, found nothing" — a genuine negative.
_NOTHING_FOUND = {
    "fetch_search": _search_finds_nothing,
    "fetch_crossref": _provider_finds_nothing,
    "fetch_openalex": _provider_finds_nothing,
    "fetch_dblp": _provider_finds_nothing,
    "fetch_openreview": _provider_finds_nothing,
    "fetch_s2": _s2_finds_nothing,
}


def _ledger_setup(tmp_path, **config_kwargs):
    """A library with one preprint and an isolated `pzi_data_home`.

    `pzi_data_home` is pinned rather than defaulted: the default honours
    `$XDG_DATA_HOME`, so a defaulted test would write the developer's real
    data directory on any machine that sets it.
    """
    bib_path = tmp_path / "ml.bib"
    data_home = tmp_path / "data"
    config_path = _write_config(
        tmp_path, bib_path, pzi_data_home=str(data_home), **config_kwargs
    )
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    return config_path, promote_ledger.ledger_path(data_home)


def _promote(config_path, tmp_path, **kwargs):
    kwargs.setdefault("dry_run", True)
    kwargs.setdefault("keep_preprint", False)
    return promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        **kwargs,
    )


def test_promote_records_a_preprint_that_is_still_unpublished(tmp_path):
    config_path, ledger_file = _ledger_setup(tmp_path)

    result = _promote(config_path, tmp_path, **_NOTHING_FOUND)

    assert result["summary"]["skipped_no_candidate"] == 1
    assert promote_ledger.load(ledger_file)["bibs"]["ml"].keys() == {"smith2024graph"}


def test_promote_skips_a_preprint_it_checked_inside_the_horizon(tmp_path):
    """The whole point: the second sweep must not redo the first sweep's work."""
    config_path, ledger_file = _ledger_setup(tmp_path)
    _promote(config_path, tmp_path, **_NOTHING_FOUND)
    assert promote_ledger.load(ledger_file)["bibs"]["ml"]

    def _must_not_be_called(query: str, *, server_url: str):
        raise AssertionError("a recently-checked preprint was looked up again")

    second = _promote(config_path, tmp_path, fetch_search=_must_not_be_called)

    summary = second["summary"]
    assert summary["skipped_recently_checked"] == 1
    assert summary["checked"] == 0
    # Not merely unchecked — not *eligible*, so the progress denominator and
    # `remaining` describe the work a follow-up run would really face.
    assert summary["eligible"] == 0
    assert summary["remaining"] == 0
    assert second["items"] == []


def test_promote_re_asks_once_the_horizon_has_passed(tmp_path):
    config_path, ledger_file = _ledger_setup(tmp_path, promote_recheck_after_days=30)
    _promote(config_path, tmp_path, **_NOTHING_FOUND)

    # Age the recorded answer past the horizon, exactly as the clock would.
    stale = promote_ledger.record_checked(
        {}, "ml", "smith2024graph",
        now=promote_ledger.utc_now() - timedelta(days=31),
    )
    promote_ledger.save(ledger_file, stale)

    result = _promote(config_path, tmp_path, **_NOTHING_FOUND)

    assert result["summary"]["skipped_recently_checked"] == 0
    assert result["summary"]["checked"] == 1


def test_promote_does_not_record_when_a_provider_failed(tmp_path):
    """An outage is not an answer.

    Recording it would freeze a transient failure into a month of silence over
    exactly the entries the sweep exists to surface — the same reasoning that
    keeps `_is_transient_error_body` out of the HTTP cache.
    """
    config_path, ledger_file = _ledger_setup(tmp_path)

    result = _promote(
        config_path, tmp_path, **{**_NOTHING_FOUND, "fetch_s2": _s2_is_rate_limited}
    )

    assert result["summary"]["skipped_no_candidate"] == 1
    assert result["summary"]["provider_errors"] >= 1
    assert promote_ledger.load(ledger_file) == {}


def test_promote_records_under_dry_run_but_still_does_not_touch_the_bib(tmp_path):
    """The lookup really happened, and the sidecar is not the library."""
    config_path, ledger_file = _ledger_setup(tmp_path)
    bib_path = tmp_path / "ml.bib"
    before = bib_path.read_text()

    _promote(config_path, tmp_path, dry_run=True, **_NOTHING_FOUND)

    assert promote_ledger.load(ledger_file)["bibs"]["ml"]
    assert bib_path.read_text() == before


def test_a_zero_horizon_writes_no_ledger_and_skips_nothing(tmp_path):
    """Off means off at both ends, so the setting fully restores the old behaviour."""
    config_path, ledger_file = _ledger_setup(tmp_path, promote_recheck_after_days=0)

    first = _promote(config_path, tmp_path, **_NOTHING_FOUND)
    second = _promote(config_path, tmp_path, **_NOTHING_FOUND)

    assert not ledger_file.exists()
    assert first["summary"]["checked"] == 1
    assert second["summary"]["checked"] == 1
    assert second["summary"]["skipped_recently_checked"] == 0


def test_a_corrupt_ledger_does_not_fail_the_run(tmp_path):
    config_path, ledger_file = _ledger_setup(tmp_path)
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    ledger_file.write_text("{ not json", encoding="utf-8")

    result = _promote(config_path, tmp_path, **_NOTHING_FOUND)

    assert result["status"] == "ok"
    assert result["summary"]["checked"] == 1
    # And the run repairs it on the way out.
    assert promote_ledger.load(ledger_file)["bibs"]["ml"]


# --- Chunked writes (item 570) --------------------------------------------

def _seed_many_preprints(tmp_path, bib_path, config_path, count):
    for i in range(count):
        _add_via_config(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record={
                "citekey": f"pre{i:03d}",
                "title": f"Preprint{i} On Structured Prediction",
                "arxiv_id": f"2401.{10000 + i}",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            bib_selector=None,
            dry_run=False,
        )


def _search_matching_title(query: str, *, server_url: str):
    """A published version carrying the same title, so the gate accepts it."""
    match = re.search(r"Preprint(\d+)", query)
    if match is None:
        return []
    return [
        {
            "item_type": "journalArticle",
            "record": {
                "title": f"Preprint{match.group(1)} On Structured Prediction",
                "venue": "Journal of Parsing",
                "doi": f"10.9/p{match.group(1)}",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            "attachments": [],
        }
    ]


def test_an_interrupted_sweep_keeps_the_promotions_it_already_wrote(tmp_path):
    """The reason writes are chunked rather than batched into one session.

    These sweeps run for hours over thousands of candidates. Holding every
    write until the end makes the run transactional, but it also means a Ctrl-C
    throws away every promotion it had found. Chunking bounds that loss to the
    current chunk.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, promote_recheck_after_days=0)
    _seed_many_preprints(tmp_path, bib_path, config_path, promote_service._WRITE_CHUNK + 3)

    calls = {"n": 0}
    real_discovery = promote_service.find_published_candidate_with_diagnostics

    def _interrupt_after_a_full_chunk(**kwargs):
        calls["n"] += 1
        if calls["n"] > promote_service._WRITE_CHUNK + 1:
            raise KeyboardInterrupt("user pressed ctrl-c mid-sweep")
        return real_discovery(**kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            promote_service,
            "find_published_candidate_with_diagnostics",
            _interrupt_after_a_full_chunk,
        )
        with pytest.raises(KeyboardInterrupt):
            promote_bib(
                config_path=str(config_path),
                home_dir=str(tmp_path),
                bib_selector=None,
                keep_preprint=False,
                dry_run=False,
                fetch_search=_search_matching_title,
            )

    written = bib_path.read_text()
    assert written.count("Journal of Parsing") == promote_service._WRITE_CHUNK


def test_promotions_are_written_in_one_session_per_chunk(tmp_path):
    """Not one session per promotion: each re-parses and re-serialises the file.

    Measured on a synthetic 4,010-entry library, 60 promotions took ~11.8 s with
    a session apiece and ~0.85 s chunked, writing the same bytes.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, promote_recheck_after_days=0)
    count = promote_service._WRITE_CHUNK + 3
    _seed_many_preprints(tmp_path, bib_path, config_path, count)

    sessions = {"n": 0}
    real_session = promote_service.batch_write_session

    @contextmanager
    def _counting_session(*args, **kwargs):
        sessions["n"] += 1
        with real_session(*args, **kwargs) as session:
            yield session

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(promote_service, "batch_write_session", _counting_session)
        result = promote_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            bib_selector=None,
            keep_preprint=False,
            dry_run=False,
            fetch_search=_search_matching_title,
        )

    assert result["summary"]["updated"] == count
    # One full chunk, then the remainder — not `count` sessions.
    assert sessions["n"] == 2


def test_one_backup_for_the_run_even_across_chunks(tmp_path):
    """The `.bak` is the library as it stood before the run, so later chunks
    must not overwrite it with an already-promoted state."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, promote_recheck_after_days=0)
    _seed_many_preprints(tmp_path, bib_path, config_path, promote_service._WRITE_CHUNK + 3)
    before = bib_path.read_text()

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_search_matching_title,
    )

    backups = list(tmp_path.glob("*.bak*"))
    assert len(backups) == 1, [b.name for b in backups]
    assert backups[0].read_text() == before
    promoted = [item for item in result["items"] if item["action"] == "update"]
    assert {item.get("backup_path") for item in promoted} == {str(backups[0])}


def test_a_preprint_that_vanished_before_the_write_is_reported_not_counted(tmp_path):
    """Nothing was malformed and nothing was lost — the target is simply gone."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, promote_recheck_after_days=0)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    real_apply = promote_service._apply_in_place_promotion

    def _apply_to_a_missing_citekey(session, preprint_ck, candidate, **kwargs):
        return real_apply(session, "gone-from-the-library", candidate, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            promote_service, "_apply_in_place_promotion", _apply_to_a_missing_citekey
        )
        result = promote_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            bib_selector=None,
            keep_preprint=False,
            dry_run=False,
            fetch_search=_fake_search_with_venue,
        )

    assert [item["action"] for item in result["items"]] == ["error"]
    assert "disappeared" in result["items"][0]["note"]
    assert result["summary"]["updated"] == 0
    assert result["summary"]["skipped_failed"] == 0


def test_a_fork_whose_second_plan_fails_inserts_nothing(tmp_path):
    """Keep mode's two writes are one unit, or a failure leaves a live orphan.

    The insert used to be applied before the note was planned, so a failure in
    between still committed the published entry — the session goes on to write —
    while the run reported the promotion failed and deleted the PDF that
    committed entry's `file =` pointed at.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, promote_recheck_after_days=0)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def _second_plan_fails(*args, **kwargs):
        raise RuntimeError("planning the cross-reference note failed")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(promote_service, "_plan_note_update", _second_plan_fails)
        result = promote_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            bib_selector=None,
            keep_preprint=True,
            dry_run=False,
            fetch_search=_fake_search_with_venue,
        )

    assert result["summary"]["created"] == 0
    assert result["summary"]["skipped_failed"] == 1
    # The published entry must not be in the library behind a reported failure.
    written = bib_path.read_text()
    assert "Journal of Parsing" not in written
    assert written.count("@") == 1
