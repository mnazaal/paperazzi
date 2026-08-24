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

from pzi import add_service
from pzi.add_service import add_records_to_bib_batch
from pzi.bib_repository import BatchWriteSession, batch_write_session, plan_bib_write
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
        cast(Any, _insert_plan(_record("a", "10.1000/a", "A"), _entry("a", "10.1000/a", "A")))
    )
    session.apply_plan(
        cast(Any, _update_plan(0, _record("a", "10.1000/a", "A2"), _entry("a", "10.1000/a", "A2")))
    )

    assert len(session.entries) == len(session.records) == 1
    assert session.index[("doi", "10.1000/a")] == [0]
    session.check_consistency()


def test_update_changing_identity_drops_stale_index_key() -> None:
    # Seed one record, then update it to a different DOI. The old DOI key must
    # be removed so a later record carrying that old DOI is not falsely matched.
    session = _session([_record("a", "10.1000/old", "A")], [_entry("a", "10.1000/old", "A")])

    session.apply_plan(
        cast(Any, _update_plan(0, _record("a", "10.1000/new", "A"), _entry("a", "10.1000/new", "A")))
    )

    assert ("doi", "10.1000/old") not in session.index
    assert session.index[("doi", "10.1000/new")] == [0]

    # A new record carrying the *old* DOI must not dedup against the updated one.
    incoming = _record("c", "10.1000/old", "C")
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


def test_batch_update_keeps_fields_the_record_model_does_not_carry(
    tmp_path: Path,
) -> None:
    """A batch update must not drop `pages`/`publisher`/`booktitle` and friends.

    Both write sinks apply a plan's entry verbatim, so every update plan has to
    arrive already merged onto the entry on disk. This is the guard on that
    contract: build the plan the way the batch path does and check the
    unmodelled fields survive a round trip through the session.
    """
    bib_path = tmp_path / "lib.bib"
    bib_path.write_text(
        "@inproceedings{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  doi = {10.1000/graph},\n"
        "  booktitle = {GraphConf},\n"
        "  pages = {1--12},\n"
        "  publisher = {ACM}\n"
        "}\n"
    )

    with batch_write_session(str(bib_path)) as session:
        incoming = cast(
            NormalizedRecord,
            {"citekey": "smith2024graph", "doi": "10.1000/graph", "title": "Graph Parsers"},
        )
        plan = plan_bib_write(
            incoming,
            session.records,
            index=session.index,
            existing_entries=session.entries,
        )
        assert plan["action"] == "update"
        session.apply_plan(plan)

    written = bib_path.read_text()
    assert "pages = {1--12}" in written
    assert "publisher = {ACM}" in written
    assert "booktitle = {GraphConf}" in written
    assert "@inproceedings{smith2024graph" in written


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
        {"citekey": "a2024", "title": "Paper A", "doi": "10.1000/a",
         "pdf_url": "http://example.com/a.pdf"},
        {"citekey": "b2023", "title": "Paper B", "doi": "10.1000/b",
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
        {"citekey": "good2024", "title": "Good Paper", "doi": "10.1000/good",
         "pdf_url": "http://example.com/good.pdf"},
        {"citekey": "bad2023", "title": "Bad Paper", "doi": "10.1000/bad",
         "pdf_url": "http://example.com/bad.pdf"},
    ]

    # The second record fails its round-trip validation. (It used to be given a
    # citekey containing a NUL byte; composed citekeys are now sanitized before
    # they reach the writer, so the failure is injected at the gate instead.)
    real_validate = add_service.validate_bibtex_roundtrip

    def _validate(entries):
        if any(entry["citekey"] == "bad2023" for entry in entries):
            raise ValueError("synthetic per-record validation failure")
        return real_validate(entries)

    with patch.object(add_service, "validate_bibtex_roundtrip", _validate):
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


# --- The backup hook (item 570) -------------------------------------------
#
# `update --promote` overwrites entries with a different paper's metadata and
# strips their identity, which is destruction of the kind `delete` and
# `library merge` back up. Hoisting those writes into a session would have lost
# the undo, so the session takes the copy itself — under its own lock.

def _one_entry_bib(bib_path):
    bib_path.write_text(
        "@article{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  doi = {10.1000/graph},\n"
        "  year = {2024},\n"
        "}\n"
    )
    return bib_path.read_text()


def _retitle(session, title):
    plan = plan_bib_write(
        cast(
            NormalizedRecord,
            {"citekey": "smith2024graph", "doi": "10.1000/graph", "title": title},
        ),
        session.records,
        index=session.index,
        existing_entries=session.entries,
    )
    session.apply_plan(plan)


def test_batch_write_session_backs_up_the_file_it_replaced(tmp_path):
    bib_path = tmp_path / "ml.bib"
    before = _one_entry_bib(bib_path)
    backup = tmp_path / "ml.bib.promote.bak"

    with batch_write_session(str(bib_path), backup_path=backup) as session:
        _retitle(session, "Graph Parsers, Revisited")

    # The backup is the content that was replaced, not the content written.
    assert backup.read_text() == before
    assert bib_path.read_text() != before
    assert "Revisited" in bib_path.read_text()


def test_batch_write_session_writes_no_backup_when_nothing_changed(tmp_path):
    """A `.bak` of a file nothing replaced invites restoring over live content."""
    bib_path = tmp_path / "ml.bib"
    _one_entry_bib(bib_path)
    backup = tmp_path / "ml.bib.promote.bak"

    with batch_write_session(str(bib_path), backup_path=backup) as session:
        _retitle(session, "Graph Parsers")  # the title it already has

    assert not backup.exists()


def test_batch_write_session_takes_no_backup_when_not_asked(tmp_path):
    """The default is unchanged: `import` and friends leave no `.bak`."""
    bib_path = tmp_path / "ml.bib"
    _one_entry_bib(bib_path)

    with batch_write_session(str(bib_path)) as session:
        _retitle(session, "Graph Parsers, Revisited")

    assert list(tmp_path.glob("*.bak*")) == []


def test_batch_write_session_leaves_no_backup_when_the_batch_raises(tmp_path):
    """Nothing was written, so there is nothing to undo."""
    bib_path = tmp_path / "ml.bib"
    before = _one_entry_bib(bib_path)
    backup = tmp_path / "ml.bib.promote.bak"

    with pytest.raises(RuntimeError):
        with batch_write_session(str(bib_path), backup_path=backup) as session:
            _retitle(session, "Graph Parsers, Revisited")
            raise RuntimeError("caller changed its mind")

    assert not backup.exists()
    assert bib_path.read_text() == before
