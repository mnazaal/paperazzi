"""Unit tests for the in-memory batch-write session.

`BatchWriteSession` owns the three structures that must move in lockstep across
a batch — parsed `entries`, projected `records`, and the identity `index` — and
folds each edit in through `apply_plan`. These cover the invariants that owner
must preserve: entries/records stay parallel; the identity index never carries a
stale key after a record's identity changes mid-batch (which would otherwise
cause a false exact-match for a later record); and `check_consistency` turns any
desync into a loud failure *before* the transactional write commits, via an
explicit raise that survives `python -O` (unlike the old `assert`).

PDF-cleanup tests (at the bottom) verify the two orphan-prevention paths in
`add_records_to_bib_batch`: per-record failure removes only that record's PDF,
and a commit-time failure removes all PDFs downloaded for applied records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from pzi.add_service import add_records_to_bib_batch
from pzi.bib_repository import BatchWriteSession
from pzi.bibtex import NormalizedRecord
from pzi.similarity import build_identity_index, find_exact_match

# Minimal valid PDF bytes (magic header is all is_pdf_bytes checks).
_FAKE_PDF = b"%PDF-1.4 fake"


def _fake_fetch_binary(url: str) -> tuple[bytes, str | None]:
    return _FAKE_PDF, "application/pdf"


def _make_bib(tmp_path: Path) -> dict:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    return {
        "name": "test",
        "path": str(tmp_path / "lib.bib"),
        "papers_dir": str(papers_dir),
    }


def _record(citekey: str, doi: str, title: str) -> NormalizedRecord:
    return cast(NormalizedRecord, {"citekey": citekey, "doi": doi, "title": title})


def _entry(citekey: str, doi: str, title: str) -> dict[str, Any]:
    return {
        "entry_type": "article",
        "citekey": citekey,
        "fields": {"doi": doi, "title": title},
    }


def _insert_plan(record: NormalizedRecord, entry: dict[str, Any]) -> dict[str, Any]:
    return {"action": "insert", "index": None, "record": record,
            "entry": entry, "changed_fields": []}


def _update_plan(idx: int, record: NormalizedRecord, entry: dict[str, Any]) -> dict[str, Any]:
    return {"action": "update", "index": idx, "record": record,
            "entry": entry, "changed_fields": ["doi"]}


def _session(
    records: list[NormalizedRecord], entries: list[dict[str, Any]]
) -> BatchWriteSession:
    return BatchWriteSession(
        entries=cast(Any, list(entries)),
        records=list(records),
        index=build_identity_index(records),
    )


def _positions(index: dict) -> dict:
    """Order-insensitive view of an identity index for comparison."""
    return {key: sorted(values) for key, values in index.items() if values}


def test_apply_plan_keeps_entries_and_records_parallel() -> None:
    session = _session([], [])

    session.apply_plan(
        cast(Any, _insert_plan(_record("a", "10.1/a", "A"), _entry("a", "10.1/a", "A")))
    )
    session.apply_plan(
        cast(Any, _update_plan(0, _record("a", "10.1/a", "A2"), _entry("a", "10.1/a", "A2")))
    )

    assert len(session.entries) == len(session.records) == 1
    assert session.index[("doi", "10.1/a")] == [0]
    session.check_consistency()


def test_update_changing_identity_drops_stale_index_key() -> None:
    # Seed one record, then update it to a different DOI. The old DOI key must
    # be removed so a later record carrying that old DOI is not falsely matched.
    session = _session([_record("a", "10.1/old", "A")], [_entry("a", "10.1/old", "A")])

    session.apply_plan(
        cast(Any, _update_plan(0, _record("a", "10.1/new", "A"), _entry("a", "10.1/new", "A")))
    )

    assert ("doi", "10.1/old") not in session.index
    assert session.index[("doi", "10.1/new")] == [0]

    # A new record carrying the *old* DOI must not dedup against the updated one.
    incoming = _record("c", "10.1/old", "C")
    assert find_exact_match(incoming, session.records, index=session.index) is None
    session.check_consistency()


def test_mixed_sequence_index_matches_a_full_rebuild() -> None:
    # A longer interleaving of inserts, an identity-changing update, an
    # identity-preserving update, and a shared-DOI insert. The incrementally
    # maintained index must end up equivalent to one rebuilt from scratch.
    session = _session([], [])
    for ck, doi in [("a", "10/a"), ("b", "10/b"), ("c", "10/c")]:
        session.apply_plan(cast(Any, _insert_plan(_record(ck, doi, ck), _entry(ck, doi, ck))))

    # record 0: identity change (10/a -> 10/a2)
    session.apply_plan(cast(Any, _update_plan(0, _record("a", "10/a2", "A"), _entry("a", "10/a2", "A"))))
    # record 2: same identity, content-only change
    session.apply_plan(cast(Any, _update_plan(2, _record("c", "10/c", "C2"), _entry("c", "10/c", "C2"))))
    # new record sharing record 1's DOI
    session.apply_plan(cast(Any, _insert_plan(_record("d", "10/b", "D"), _entry("d", "10/b", "D"))))

    session.check_consistency()
    assert _positions(session.index) == _positions(build_identity_index(session.records))


def test_check_consistency_detects_stale_index_key() -> None:
    session = _session([_record("a", "10/a", "A")], [_entry("a", "10/a", "A")])
    # A key not backed by any record — the exact failure mode the guard exists
    # to catch before a write commits.
    session.index[("doi", "10/ghost")] = [0]
    with pytest.raises(RuntimeError, match="identity index out of sync"):
        session.check_consistency()


def test_check_consistency_detects_length_desync() -> None:
    session = _session([_record("a", "10/a", "A")], [_entry("a", "10/a", "A")])
    session.records.append(_record("b", "10/b", "B"))
    with pytest.raises(RuntimeError, match="batch state desync"):
        session.check_consistency()


def test_apply_plan_update_without_index_raises() -> None:
    # The narrowing guard is an explicit raise (not an assert), so it still
    # fires under ``python -O``.
    session = _session([_record("a", "10/a", "A")], [_entry("a", "10/a", "A")])
    bad = _update_plan(0, _record("a", "10/a", "A2"), _entry("a", "10/a", "A2"))
    bad["index"] = None
    with pytest.raises(RuntimeError, match="concrete index"):
        session.apply_plan(cast(Any, bad))


# ---------------------------------------------------------------------------
# PDF-cleanup integration tests
# ---------------------------------------------------------------------------


def test_batch_commit_failure_removes_downloaded_pdfs(tmp_path: Path) -> None:
    """Commit-time failure (check_consistency) must not leave orphaned PDFs.

    When the batch fails after the loop — during check_consistency or roundtrip
    validation in batch_write_session — the bib is not written but PDFs already
    downloaded to disk would be orphaned without the outer cleanup guard.
    """
    bib = _make_bib(tmp_path)
    papers_dir = Path(bib["papers_dir"])

    records = [
        {"citekey": "a2024", "title": "Paper A", "doi": "10.1/a",
         "pdf_url": "http://example.com/a.pdf"},
        {"citekey": "b2023", "title": "Paper B", "doi": "10.1/b",
         "pdf_url": "http://example.com/b.pdf"},
    ]

    with patch(
        "pzi.bib_repository.BatchWriteSession.check_consistency",
        side_effect=RuntimeError("synthetic commit failure"),
    ):
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            add_records_to_bib_batch(
                bib=cast(Any, bib),
                records=records,
                dry_run=False,
                fetch_binary=_fake_fetch_binary,
            )

    assert not Path(bib["path"]).exists(), "bib must not be written on commit failure"
    assert list(papers_dir.glob("*.pdf")) == [], "no orphaned PDFs after commit failure"


def test_batch_per_record_failure_cleans_only_that_records_pdf(tmp_path: Path) -> None:
    """A mid-loop per-record failure removes only that record's PDF.

    The first record succeeds (PDF downloaded, plan applied, committed);
    the second fails during planning. Only the second record's PDF must be
    removed — the first record's PDF must survive because it is committed.
    """
    bib = _make_bib(tmp_path)
    papers_dir = Path(bib["papers_dir"])

    records = [
        {"citekey": "good2024", "title": "Good Paper", "doi": "10.1/good",
         "pdf_url": "http://example.com/good.pdf"},
        # Invalid entry_type-less record that will fail validate_bibtex_roundtrip.
        # We force the failure by passing a record whose citekey has illegal chars.
        {"citekey": "bad\x00key", "title": "Bad Paper", "doi": "10.1/bad",
         "pdf_url": "http://example.com/bad.pdf"},
    ]

    results = add_records_to_bib_batch(
        bib=cast(Any, bib),
        records=records,
        dry_run=False,
        fetch_binary=_fake_fetch_binary,
    )

    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"

    committed_pdfs = list(papers_dir.glob("*.pdf"))
    assert len(committed_pdfs) == 1, "only the good record's PDF should remain"
    assert "good2024" in committed_pdfs[0].name
