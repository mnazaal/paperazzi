"""Live smoke tests for end-to-end add, search, and tag workflows."""

import os
from collections.abc import Mapping

import pytest

from pzi.add_service import add_input_to_bib
from pzi.bib_repository import read_bib_file
from pzi.config import load_bib_target
from pzi.search_service import search_bib
from pzi.tag_service import list_tags

# Open-access DOI with PDF (PLOS ONE) — used by existing test_live_metadata.py
OA_DOI = "10.1371/journal.pone.0000308"
# Stable arXiv preprint
ARXIV_ID = "2301.07041"


def _persisted_record(config_path: str, citekey: str) -> dict[str, object]:
    """Load the entry the add pipeline actually wrote to the bib.

    ``add_input_to_bib`` returns write-status fields (status/action/citekey/…),
    *not* the bibliographic metadata — that only lives in the serialized bib.
    Round-tripping through the file is also the honest end-to-end check.
    """
    resolved = load_bib_target(
        config_path=config_path,
        home_dir=os.path.expanduser("~"),
        bib_selector=None,
    )
    assert not isinstance(resolved, list), f"failed to resolve bib: {resolved}"
    _config, bib = resolved
    for record in read_bib_file(bib["path"])["records"]:
        if record.get("citekey") == citekey:
            return record
    raise AssertionError(f"citekey {citekey!r} not found in persisted bib")


def _answering_provider(result: Mapping[str, object]) -> str | None:
    """Which metadata path answered this capture, per `metadata_diagnostics`.

    `add_planning` names its winner — `translation_server` for the translation
    path, `crossref`/`openalex`/`semantic_scholar` for the fallback cascade —
    and `add_service` lifts it onto `metadata_diagnostics` as `metadata from X`.
    Without this the two are indistinguishable from the outside, which is how
    the live job could pass on nothing but Crossref fallbacks while claiming to
    cover the translation-server path.
    """
    for line in result.get("metadata_diagnostics") or []:
        if isinstance(line, str) and line.startswith("metadata from "):
            return line.removeprefix("metadata from ").strip()
    return None


def test_live_add_oa_doi_metadata(live_config_path: str) -> None:
    """Add an open-access DOI; verify persisted metadata fields are populated."""
    result = add_input_to_bib(
        config_path=live_config_path,
        home_dir=os.path.expanduser("~"),
        value=OA_DOI,
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok", f"add failed: {result.get('message')} {result.get('errors')}"
    citekey = result["citekey"]
    assert citekey, "expected a citekey"

    record = _persisted_record(live_config_path, citekey)
    if not record.get("title"):
        pytest.skip("translation-server returned no metadata for the test DOI (third-party)")
    assert record.get("doi") == OA_DOI
    assert record.get("authors"), "expected authors"
    # The point of this job, and the input that can carry it. A bare DOI reaches
    # the translation server's *search* endpoint every run, so this assertion is
    # deterministic — unlike the arXiv-URL test below, where the identifier is
    # extracted and the DOI cascade may answer first. A capture the fallback
    # answered is a fine capture and a useless observation: it says nothing about
    # the path pzi actually leads with, which is the standing gap item 412
    # records.
    provider = _answering_provider(result)
    assert provider == "translation_server", (
        f"capture succeeded but {provider or 'no provider'} answered, not the "
        "translation server — the path this job exists to exercise did not run"
    )


def test_live_add_arxiv_url_metadata(live_config_path: str) -> None:
    """Add an arXiv URL; verify persisted metadata fields are populated."""
    result = add_input_to_bib(
        config_path=live_config_path,
        home_dir=os.path.expanduser("~"),
        value=f"https://arxiv.org/abs/{ARXIV_ID}",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok", f"add failed: {result.get('message')} {result.get('errors')}"
    citekey = result["citekey"]
    assert citekey, "expected a citekey"

    record = _persisted_record(live_config_path, citekey)
    if not record.get("title"):
        pytest.skip("translation-server returned no metadata for the test arXiv URL (third-party)")
    assert record.get("year"), "expected a year"
    assert record.get("arxiv_id") == ARXIV_ID or record.get("doi"), \
        "expected arXiv ID or DOI"
    # Deliberately *not* asserting which provider answered, unlike the DOI test
    # above. An arXiv URL is classified `doi` — the identifier is extracted from
    # the URL — so it goes down the DOI cascade first and only falls back to
    # web-translating the original URL when that comes up empty. Which of the two
    # answers is a third-party race, so pinning `translation_server` here made
    # this test fail on days when Crossref or OpenAlex happened to know the DOI.
    # The translation-server path is asserted where it is deterministic: the DOI
    # test above, which reaches the server's *search* endpoint every time.
    #
    # What is asserted is that *something* claimed the capture. That is not a
    # weaker version of the same check — it is the one that would have caught
    # the real bug here: two of the three translation-server return sites set no
    # provider at all, so a capture the server answered was indistinguishable
    # from one nothing answered.
    provider = _answering_provider(result)
    assert provider is not None, (
        "capture succeeded but no provider claimed it — every metadata path must "
        "name itself, or a translation-server capture cannot be told from a "
        "fallback (see `metadata_provider` in add_planning)"
    )


def test_live_tag_and_search(live_config_path: str) -> None:
    """Add an entry with tags, then verify tag listing and search.

    Tag storage is local round-trip logic (override -> bib ``keywords`` field ->
    parsed back to ``tags``), so it is asserted strictly once the add succeeds.
    """
    tags = ["live-smoke-test", "integration"]

    # Add with tags — the override key is ``tags`` (matches commands/add.py); the
    # bibtex layer serializes it to the ``keywords`` field and parses it back.
    result = add_input_to_bib(
        config_path=live_config_path,
        home_dir=os.path.expanduser("~"),
        value=OA_DOI,
        record_overrides={"tags": tags},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok", f"add failed: {result.get('message')} {result.get('errors')}"
    citekey = result["citekey"]

    # List tags for this citekey
    tag_result = list_tags(
        config_path=live_config_path,
        home_dir=os.path.expanduser("~"),
        bib_selector=None,
        citekey=citekey,
    )
    assert tag_result["status"] == "ok"
    tag_names = tag_result["tags"]
    assert "live-smoke-test" in tag_names

    # Search by tag
    search_result = search_bib(
        config_path=live_config_path,
        home_dir=os.path.expanduser("~"),
        bib_selector=None,
        tag="live-smoke-test",
    )
    assert search_result["status"] == "ok"
    assert len(search_result.get("matches", [])) >= 1, "expected at least one match"
    match_citekeys = [m["citekey"] for m in search_result["matches"]]
    assert citekey in match_citekeys, f"citekey {citekey} not found in search results"
