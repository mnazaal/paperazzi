"""A ledger answer belongs to the exact entry it was given for, not to its citekey.

`check` and `update --promote` skip an entry the ledger says was answered inside
the horizon.  Keyed by citekey alone, a paper deleted and re-added under the
same key inherited the old paper's "verified" / "not published yet" answer until
the horizon ran out.  These go through the two public services and the sidecar
itself.
"""

import json
from datetime import UTC, datetime, timedelta

from pzi import ledger
from pzi.add_service import add_record_with_bib
from pzi.check_service import check_bib
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.promote_service import promote_bib

_NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


# --- pure identity rule ------------------------------------------------------


def _rec(**kw):
    return {"citekey": "k", "title": "Graph Parsers", **kw}


def test_identity_ignores_enrichment_but_sees_a_replaced_paper():
    base = _rec(doi="10.1000/ABC", year=2024, authors=["Smith, Jane"])
    enriched = _rec(
        doi="https://doi.org/10.1000/abc", year=2024, authors=["Smith, Jane"],
        venue="Journal of Parsing", pages="1--9", abstract="text", tags=["x"],
    )
    assert ledger.entry_identity(base) == ledger.entry_identity(enriched)
    assert ledger.entry_identity(base) != ledger.entry_identity(_rec(doi="10.1000/other"))
    assert ledger.entry_identity(_rec()) != ledger.entry_identity(_rec(title="Other"))
    assert ledger.entry_identity(_rec(arxiv_id="2401.1")) != ledger.entry_identity(
        _rec(arxiv_id="2401.2")
    )
    # Title case, punctuation and entities are not a different paper.
    assert ledger.entry_identity(_rec(title="graph  parsers!")) == ledger.entry_identity(_rec())


def test_a_recorded_answer_is_only_fresh_for_the_identity_it_was_recorded_for():
    state = ledger.record_checked({}, "ml", "k", now=_NOW, identity="aaa")
    kw = {"now": _NOW, "horizon_days": 30}
    assert ledger.is_recently_checked(state, "ml", "k", identity="aaa", **kw)
    assert not ledger.is_recently_checked(state, "ml", "k", identity="bbb", **kw)


def test_a_legacy_timestamp_only_state_is_readable_but_not_trusted():
    legacy = {"version": 1, "bibs": {"ml": {"k": _NOW.isoformat(timespec="seconds")}}}
    kw = {"now": _NOW, "horizon_days": 30}
    assert not ledger.is_recently_checked(legacy, "ml", "k", identity="aaa", **kw)
    # Still pruned by age, and re-recording upgrades it in place.
    assert ledger.prune(legacy, now=_NOW + timedelta(days=31), horizon_days=30)["bibs"] == {}
    upgraded = ledger.record_checked(legacy, "ml", "k", now=_NOW, identity="aaa")
    assert ledger.is_recently_checked(upgraded, "ml", "k", identity="aaa", **kw)
    assert ledger.prune(upgraded, now=_NOW, horizon_days=30)["bibs"]["ml"].keys() == {"k"}
    assert ledger.prune(upgraded, now=_NOW + timedelta(days=31), horizon_days=30)["bibs"] == {}


# --- check -------------------------------------------------------------------


def _config(tmp_path, bib_path, **extra):
    config_path = tmp_path / "config.toml"
    prefix = "".join(f'{k} = "{v}"\n' for k, v in extra.items())
    config_path.write_text(
        f'{prefix}[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    return config_path


def _seed(tmp_path, config_path, **record):
    resolved = load_bib_target(
        config_path=str(config_path), home_dir=str(tmp_path), bib_selector=None
    )
    assert not isinstance(resolved, BibResolutionFailure)
    add_record_with_bib(bib=resolved[1], record=record, dry_run=False)


def _no_source(_title, **_kw):
    return None


def _check(tmp_path, config_path, calls):
    def crossref(title, **_kw):
        calls.append(title)
        return {"title": title, "authors": ["Smith, A"], "year": 2020}

    return check_bib(
        config_path=str(config_path), home_dir=str(tmp_path), bib_selector=None,
        recheck_after_days=30, fetch_crossref=crossref, fetch_openalex=_no_source,
        fetch_dblp=_no_source, fetch_openreview=_no_source, fetch_s2=_no_source,
    )


def _check_library(tmp_path):
    bib = tmp_path / "ml.bib"
    config_path = _config(tmp_path, bib, pzi_data_home=tmp_path / "data")
    _seed(tmp_path, config_path, citekey="a", title="Alpha Paper",
          authors=["Smith, A"], year=2020, doi="10.1000/alpha")
    return bib, config_path


def test_check_audits_a_replaced_title_but_skips_the_unchanged_entry(tmp_path):
    bib, config_path = _check_library(tmp_path)
    calls: list[str] = []
    _check(tmp_path, config_path, calls)
    assert calls == ["Alpha Paper"]

    calls.clear()
    assert _check(tmp_path, config_path, calls)["skipped_fresh"] == 1
    assert calls == []

    bib.write_text(bib.read_text().replace("Alpha Paper", "Beta Paper"))
    calls.clear()
    _check(tmp_path, config_path, calls)
    assert calls == ["Beta Paper"]


def test_check_audits_a_replaced_doi_with_the_same_title(tmp_path):
    bib, config_path = _check_library(tmp_path)
    calls: list[str] = []
    _check(tmp_path, config_path, calls)

    bib.write_text(bib.read_text().replace("10.1000/alpha", "10.1000/other"))
    calls.clear()
    _check(tmp_path, config_path, calls)
    assert calls == ["Alpha Paper"]


def test_check_stays_fresh_across_a_metadata_enrichment(tmp_path):
    bib, config_path = _check_library(tmp_path)
    calls: list[str] = []
    _check(tmp_path, config_path, calls)

    text = bib.read_text()
    bib.write_text(text.replace("doi", "journal = {Journal of Parsing},\n  pages = {1--9},\n  doi", 1))
    assert "Journal of Parsing" in bib.read_text()
    calls.clear()
    assert _check(tmp_path, config_path, calls)["skipped_fresh"] == 1
    assert calls == []


def test_check_re_audits_once_after_a_pre_identity_ledger_then_is_fresh(tmp_path):
    _bib, config_path = _check_library(tmp_path)
    ledger_file = ledger.ledger_path(tmp_path / "data", ledger.CHECK_FILENAME)
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    stamp = ledger.utc_now().isoformat(timespec="seconds")
    ledger_file.write_text(json.dumps({"version": 1, "bibs": {"ml": {"a": stamp}}}))

    calls: list[str] = []
    _check(tmp_path, config_path, calls)
    assert calls == ["Alpha Paper"]
    calls.clear()
    _check(tmp_path, config_path, calls)
    assert calls == []


# --- promote -----------------------------------------------------------------


def _nothing(*_a, **_kw):
    return []


def _none(_title):
    return None


def _promote(tmp_path, config_path, **kwargs):
    kwargs.setdefault("fetch_search", lambda q, *, server_url: [])
    return promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path), bib_selector=None,
        dry_run=True, keep_preprint=False, fetch_crossref=_none, fetch_openalex=_none,
        fetch_dblp=_none, fetch_openreview=_none, fetch_s2=lambda t: (None, None),
        **kwargs,
    )


def _promote_library(tmp_path):
    bib = tmp_path / "ml.bib"
    config_path = _config(tmp_path, bib, pzi_data_home=tmp_path / "data")
    _seed(tmp_path, config_path, citekey="p", title="Graph Parsers",
          arxiv_id="2401.12345", year=2024, authors=["Smith, Jane"])
    return bib, config_path


def _must_not_search(_q, *, server_url):
    raise AssertionError("an unchanged, recently checked preprint was looked up again")


def test_promote_audits_a_replaced_preprint_but_skips_the_unchanged_one(tmp_path):
    bib, config_path = _promote_library(tmp_path)
    assert _promote(tmp_path, config_path)["summary"]["checked"] == 1

    again = _promote(tmp_path, config_path, fetch_search=_must_not_search)
    assert again["summary"]["skipped_recently_checked"] == 1

    # Same citekey, different paper (new title and arXiv id).
    bib.write_text(
        bib.read_text().replace("Graph Parsers", "Tree Parsers").replace("2401.12345", "2402.99999")
    )
    replaced = _promote(tmp_path, config_path)
    assert replaced["summary"]["skipped_recently_checked"] == 0
    assert replaced["summary"]["checked"] == 1


def test_promote_audits_a_replaced_title_with_the_same_arxiv_id(tmp_path):
    bib, config_path = _promote_library(tmp_path)
    _promote(tmp_path, config_path)
    bib.write_text(bib.read_text().replace("Graph Parsers", "Tree Parsers"))
    assert _promote(tmp_path, config_path)["summary"]["checked"] == 1


def test_promote_stays_fresh_across_a_metadata_enrichment(tmp_path):
    bib, config_path = _promote_library(tmp_path)
    _promote(tmp_path, config_path)
    bib.write_text(bib.read_text().replace("year", "pages = {1--9},\n  year", 1))
    assert "pages" in bib.read_text()
    again = _promote(tmp_path, config_path, fetch_search=_must_not_search)
    assert again["summary"]["skipped_recently_checked"] == 1
