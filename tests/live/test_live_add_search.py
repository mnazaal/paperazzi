"""Live smoke tests for end-to-end add, search, and tag workflows."""

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pzi.add_service import add_input_to_bib
from pzi.bib_repository import read_bib_file
from pzi.config import load_and_resolve_bib
from pzi.search_service import search_bib
from pzi.tag_service import list_tags
from pzi.ts_backend import backend_session

# Open-access DOI with PDF (PLOS ONE) — used by existing test_live_metadata.py
OA_DOI = "10.1371/journal.pone.0000308"
# Stable arXiv preprint
ARXIV_ID = "2301.07041"


def _write_config(bib_path: str, config_path: str) -> str:
    """Write a minimal pzi config pointing at a temp bib."""
    bib_path_abs = str(Path(bib_path).resolve())
    config_dir = os.path.dirname(config_path)
    papers_dir = os.path.join(config_dir, "papers")
    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "smoke"
path = "{bib_path_abs}"
papers_dir = "{papers_dir}"
default = true
"""
    config_dir_abs = os.path.dirname(config_path)
    os.makedirs(config_dir_abs, exist_ok=True)
    Path(config_path).write_text(config_text, encoding="utf-8")
    return config_path


@pytest.fixture(scope="module")
def running_translation_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Start a real translation-server for this module's tests, shared to avoid
    re-cloning/npm-installing per test. Skips (not fails) if it can't come up —
    this is a network-dependent smoke job, not a gate.
    """
    data_home = tmp_path_factory.mktemp("ts-data")
    config: dict[str, object] = {
        "translation_server_url": "http://127.0.0.1:1969",
        "pzi_data_home": str(data_home),
    }
    with backend_session(
        config, config_path="", home_dir=os.path.expanduser("~"),
        interactive=False, stdout=sys.stdout, stderr=sys.stderr,
    ) as backend:
        if not backend["ready"]:
            pytest.skip("translation-server could not be started for live tests")
        yield


@pytest.fixture
def live_config_path(tmp_path: Path, running_translation_server: None) -> str:
    bib_path = tmp_path / "smoke.bib"
    config_path = tmp_path / "config.toml"
    _write_config(str(bib_path), str(config_path))
    return str(config_path)


def _persisted_record(config_path: str, citekey: str) -> dict[str, object]:
    """Load the entry the add pipeline actually wrote to the bib.

    ``add_input_to_bib`` returns write-status fields (status/action/citekey/…),
    *not* the bibliographic metadata — that only lives in the serialized bib.
    Round-tripping through the file is also the honest end-to-end check.
    """
    resolved = load_and_resolve_bib(
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
