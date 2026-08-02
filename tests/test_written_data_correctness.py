"""What ends up in the entry when nothing fails.

Nothing in this file is about crashes or error reporting. Each case wrote a
plausible-looking entry containing the wrong thing, at exit 0, with no warning.
"""

from __future__ import annotations

from pathlib import Path

from pzi.add_service import add_input_to_bib
from pzi.identifiers import classify_input

MINIMAL_CONFIG = """
api_listen_host = "127.0.0.1"
api_listen_port = 8765
translation_server_url = "http://127.0.0.1:1969"

[[bibs]]
name = "main"
path = "{bib_path}"
papers_dir = "{papers_dir}"
default = true
"""


def _config(tmp_path: Path) -> tuple[str, Path]:
    bib_path = tmp_path / "main.bib"
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        MINIMAL_CONFIG.format(bib_path=bib_path, papers_dir=papers), encoding="utf-8"
    )
    return str(config_path), bib_path


# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------


def test_a_conference_paper_from_the_translation_server_is_inproceedings(
    tmp_path: Path,
) -> None:
    """`item_type` is a sibling of `record` in a translation result, and the add
    path took only the record — so every conference paper became an @article
    with `journal = {proceedings title}`."""
    config_path, bib_path = _config(tmp_path)

    def _search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "title": "Dynamo: Amazon's Highly Available Key-value Store",
                    "authors": ["DeCandia, Giuseppe"],
                    "year": 2007,
                    "venue": "SOSP '07",
                    "doi": "10.1145/1327452.1327492",
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1145/1327452.1327492",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=_search,
    )

    assert result["status"] == "ok", result
    written = bib_path.read_text(encoding="utf-8")
    assert "@inproceedings{" in written
    assert "booktitle = {SOSP '07}" in written
    assert "journal" not in written


# ---------------------------------------------------------------------------
# Venue placement
# ---------------------------------------------------------------------------


def test_filling_a_venue_on_an_inproceedings_writes_booktitle() -> None:
    from pzi.bibtex import merge_projected_entry

    entry = {
        "entry_type": "inproceedings",
        "citekey": "k1",
        "fields": {"title": "T", "year": "2020"},
    }
    projected = {
        "entry_type": "inproceedings",
        "citekey": "k1",
        "fields": {"title": "T", "year": "2020", "booktitle": "NeurIPS"},
    }

    merged = merge_projected_entry(entry, projected)  # type: ignore[arg-type]

    assert merged["fields"].get("booktitle") == "NeurIPS"
    assert "journal" not in merged["fields"]


def test_filling_a_venue_on_an_article_still_writes_journal() -> None:
    from pzi.bibtex import merge_projected_entry

    entry = {"entry_type": "article", "citekey": "k1", "fields": {"title": "T"}}
    projected = {
        "entry_type": "article",
        "citekey": "k1",
        "fields": {"title": "T", "journal": "JMLR"},
    }

    merged = merge_projected_entry(entry, projected)  # type: ignore[arg-type]

    assert merged["fields"].get("journal") == "JMLR"


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def test_a_percent_encoded_doi_url_is_still_a_doi() -> None:
    """Classified as a plain URL, the entry was written with no identifier and
    never deduped."""
    classified = classify_input("https://doi.org/10.1145%2F1327452.1327492")

    assert classified["kind"] == "doi"
    assert classified["normalized"] == "10.1145/1327452.1327492"


# ---------------------------------------------------------------------------
# Promotion's duplicate guard
# ---------------------------------------------------------------------------


def test_promote_duplicate_guard_is_case_and_whitespace_insensitive() -> None:
    from pzi.promote_service import _find_duplicate_citekey

    records = [
        {"citekey": "existing2016", "doi": "10.1145/abc", "title": "Deep Residual Learning"},
    ]

    by_doi = _find_duplicate_citekey(
        {"doi": "10.1145/ABC", "title": "Something Else"},  # type: ignore[arg-type]
        records,  # type: ignore[arg-type]
        "preprint2015",
    )
    by_title = _find_duplicate_citekey(
        {"title": "Deep  Residual   Learning"},  # type: ignore[arg-type]
        records,  # type: ignore[arg-type]
        "preprint2015",
    )

    assert by_doi == "existing2016"
    assert by_title == "existing2016"


# ---------------------------------------------------------------------------
# `update`'s acceptance gate
# ---------------------------------------------------------------------------


def test_update_refuses_a_candidate_whose_doi_contradicts_the_entry() -> None:
    from pzi.update_service import _candidate_rejection

    rejection = _candidate_rejection(
        {"doi": "10.1234/right", "title": "Graph Parsers"},
        {"record": {"doi": "10.1234/wrong", "title": "Graph Parsers"}},
        min_score=-1000,
    )

    assert rejection is not None
    assert "contradicts" in rejection


def test_update_accepts_a_preprints_differing_doi() -> None:
    """An arXiv DOI legitimately differs from the published one — that pairing
    is what `update --promote` exists for."""
    from pzi.update_service import _candidate_rejection

    rejection = _candidate_rejection(
        {"doi": "10.48550/arXiv.2401.00001", "arxiv_id": "2401.00001", "title": "T"},
        {"record": {"doi": "10.1234/published", "title": "T"}},
        min_score=-1000,
    )

    assert rejection is None


def test_update_refuses_a_candidate_below_the_confidence_floor() -> None:
    from pzi.update_service import _candidate_rejection

    # `metadata_confidence_min_score` defaults to 0, which rejects only
    # negative-scoring candidates; a configured floor rejects weak ones too.
    # Either way it is now a write gate rather than a warning.
    rejection = _candidate_rejection(
        {"title": "A very specific title about graph parsers", "authors": ["A", "B", "C"]},
        {"record": {"title": "Something else entirely"}},
        min_score=10,
    )

    assert rejection is not None
    assert "metadata_confidence_min_score=10" in rejection


# ---------------------------------------------------------------------------
# Provider fallthrough
# ---------------------------------------------------------------------------


def test_a_provider_transport_failure_does_not_abort_the_cascade() -> None:
    """A connection-refused translation-server aborted DOI resolution entirely,
    with Crossref and OpenAlex sitting right behind it."""
    from pzi.add_planning import safe_api_call

    errors: list[str] = []

    def _refused():
        raise OSError("connection refused")

    assert safe_api_call(_refused, errors=errors) == []
    assert errors and "unreachable" in errors[0]


def test_a_real_bug_still_propagates() -> None:
    from pzi.add_planning import safe_api_call

    def _bug():
        raise KeyError("provider contract changed")

    try:
        safe_api_call(_bug, errors=[])
    except KeyError:
        return
    raise AssertionError("a KeyError must not be absorbed as 'no results'")


# ---------------------------------------------------------------------------
# Concurrent edits
# ---------------------------------------------------------------------------


def test_tag_add_uses_the_record_as_it_is_under_the_lock(tmp_path: Path) -> None:
    """Both callbacks computed the new entry from a pre-lock snapshot and
    discarded the record the repository handed them, so a concurrent edit was
    silently reverted while the command reported success."""
    from pzi import tag_service

    bib_path = tmp_path / "main.bib"
    bib_path.write_text(
        "@article{a1,\n  title = {Original Title},\n  year = {2020},\n}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    papers = tmp_path / "papers"
    papers.mkdir()
    config_path.write_text(
        MINIMAL_CONFIG.format(bib_path=bib_path, papers_dir=papers), encoding="utf-8"
    )

    real_update = tag_service.update_bib_entry

    def _update_with_concurrent_edit(path, citekey, updater, **kwargs):
        # Another writer corrects the title between this run's read and the
        # exclusive lock.
        bib_path.write_text(
            "@article{a1,\n  title = {Corrected Title},\n  year = {2020},\n}\n",
            encoding="utf-8",
        )
        return real_update(path, citekey, updater, **kwargs)

    tag_service.update_bib_entry = _update_with_concurrent_edit  # type: ignore[assignment]
    try:
        result = tag_service.add_tags(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            bib_selector=None,
            citekey="a1",
            tags=["readme"],
        )
    finally:
        tag_service.update_bib_entry = real_update  # type: ignore[assignment]

    assert result["status"] == "ok"
    written = bib_path.read_text(encoding="utf-8")
    assert "Corrected Title" in written
    assert "keywords = {readme}" in written
