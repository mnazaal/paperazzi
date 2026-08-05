"""Write-path integrity: complete writes, gated sinks, canonical locks, fidelity.

Every test here pins a property that used to fail *silently* — a truncated
library reported as a successful write, an unparseable file committed by a sink
that skipped the round-trip gate, two writers holding "the" lock on one bib
through different path spellings. They are grouped in one file because they all
guard the same chokepoint (``_write_bib_text_atomic`` and its callers) rather
than any one command.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from pzi.bib_repository import (
    _write_bib_text_atomic,
    delete_bib_entry,
    execute_write_plan,
    plan_bib_write,
    read_bib_file,
    update_bib_entry,
    with_bib_lock,
)
from pzi.bibtex import BibtexEntry
from pzi.errors import PziError

ONE_ENTRY = """@article{smith2020,
  title = {A Title},
  year = {2020},
}
"""

TWO_ENTRIES = """@article{smith2020,
  title = {A Title},
  year = {2020},
}

@article{jones2021,
  title = {Another Title},
  year = {2021},
}
"""


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Complete writes
# ---------------------------------------------------------------------------


def test_short_write_leaves_the_original_bibliography_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial ``os.write`` must not become a truncated library on disk."""
    bib = tmp_path / "main.bib"
    path = _write(bib, ONE_ENTRY)
    real_write = os.write

    def _one_short_write(fd: int, data) -> int:
        monkeypatch.setattr(os, "write", real_write)
        return real_write(fd, bytes(data)[:7])

    monkeypatch.setattr(os, "write", _one_short_write)
    _write_bib_text_atomic(path, TWO_ENTRIES)

    assert bib.read_text(encoding="utf-8") == TWO_ENTRIES


def test_failed_write_removes_the_temporary_file_and_keeps_the_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bib = tmp_path / "main.bib"
    path = _write(bib, ONE_ENTRY)

    def _failing_write(fd: int, data) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", _failing_write)
    with pytest.raises(OSError):
        _write_bib_text_atomic(path, TWO_ENTRIES)

    assert bib.read_text(encoding="utf-8") == ONE_ENTRY
    assert list(tmp_path.glob(".bib-*.tmp")) == []


# ---------------------------------------------------------------------------
# Duplicate citekeys are refused, not written
# ---------------------------------------------------------------------------


def test_build_library_refuses_duplicate_citekeys() -> None:
    from bibtexparser.model import Entry, Field

    from pzi.bib_serialize import build_library

    blocks = [
        Entry(entry_type="article", key="dup", fields=[Field(key="title", value="One")]),
        Entry(entry_type="article", key="dup", fields=[Field(key="title", value="Two")]),
    ]
    with pytest.raises(PziError) as excinfo:
        build_library(blocks)  # type: ignore[arg-type]

    assert "duplicate citekey dup" in str(excinfo.value)


def test_update_bib_entry_refuses_a_rename_onto_an_existing_key(tmp_path: Path) -> None:
    """The round-trip gate the plan-based sinks always had, now on this one too."""
    bib = tmp_path / "main.bib"
    path = _write(bib, TWO_ENTRIES)

    def _rename(entry: BibtexEntry, _record) -> BibtexEntry:
        renamed = dict(entry)
        renamed["citekey"] = "jones2021"
        return renamed  # type: ignore[return-value]

    with pytest.raises(PziError):
        update_bib_entry(path, "smith2020", _rename)  # type: ignore[arg-type]

    assert bib.read_text(encoding="utf-8") == TWO_ENTRIES


def test_update_bib_entry_refuses_an_entry_that_cannot_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pzi import bib_repository

    bib = tmp_path / "main.bib"
    path = _write(bib, ONE_ENTRY)

    def _explode(_entries) -> None:
        raise PziError("write plan produces invalid BibTeX: synthetic")

    monkeypatch.setattr(bib_repository, "_validate_bibtex_roundtrip", _explode)

    def _touch(entry: BibtexEntry, _record) -> BibtexEntry:
        touched = dict(entry)
        touched["fields"] = {**entry["fields"], "keywords": "readme"}
        return touched  # type: ignore[return-value]

    with pytest.raises(PziError):
        update_bib_entry(path, "smith2020", _touch)  # type: ignore[arg-type]

    assert bib.read_text(encoding="utf-8") == ONE_ENTRY


def test_delete_bib_entry_validates_what_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pzi import bib_repository

    bib = tmp_path / "main.bib"
    path = _write(bib, TWO_ENTRIES)

    def _explode(_entries) -> None:
        raise PziError("write plan produces invalid BibTeX: synthetic")

    monkeypatch.setattr(bib_repository, "_validate_bibtex_roundtrip", _explode)
    with pytest.raises(PziError):
        delete_bib_entry(path, "smith2020")

    assert bib.read_text(encoding="utf-8") == TWO_ENTRIES


# ---------------------------------------------------------------------------
# Inserts are gated by the same parseability check as updates
# ---------------------------------------------------------------------------


MALFORMED = """@article{good1,
  title = {Fine},
  year = {2020},
}

@article{bad1,
  title = {Unclosed,
  year = {2021},
"""


def test_insert_into_a_partly_unparseable_bib_refuses_instead_of_writing(
    tmp_path: Path,
) -> None:
    bib = tmp_path / "main.bib"
    path = _write(bib, MALFORMED)
    before = bib.read_text(encoding="utf-8")

    plan = plan_bib_write(
        {"citekey": "new1", "title": "New Paper", "year": 2022},  # type: ignore[arg-type]
        [],
    )
    with pytest.raises(PziError, match="malformed BibTeX"):
        execute_write_plan(path, plan)

    assert bib.read_text(encoding="utf-8") == before
    assert "WARNING" not in bib.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The gate checks the parse *result*, not merely that parsing did not raise
# ---------------------------------------------------------------------------


def _serialize_values_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the value sanitizer, standing in for a serializer regression.

    The gate exists to catch output the serializer should never have produced,
    so every input that reaches it through the real sanitizer is by definition
    already safe. Removing the sanitizer is the only way to ask the gate the
    question it is there to answer.
    """
    from pzi import bib_serialize

    monkeypatch.setattr(bib_serialize, "_safe_field_value", lambda value: value)


def _entry(**fields: str) -> BibtexEntry:
    return {  # type: ignore[return-value]
        "entry_type": "article",
        "citekey": "smith2019graph",
        "fields": fields,
    }


def test_the_gate_rejects_output_the_parser_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing backslash escapes the writer's own closing brace.

    bibtexparser v2 files the result in ``failed_blocks`` instead of raising, so
    a gate that only watches for an exception passes it straight to disk.
    """
    from pzi.bib_serialize import _validate_bibtex_roundtrip

    _serialize_values_verbatim(monkeypatch)

    with pytest.raises(PziError, match="invalid BibTeX"):
        _validate_bibtex_roundtrip([_entry(title="Graph Networks\\", year="2019")])


def test_the_gate_rejects_output_that_parses_back_as_different_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-tripping is not enough: the entry has to come back unchanged."""
    from pzi.bib_serialize import _validate_bibtex_roundtrip

    _serialize_values_verbatim(monkeypatch)

    with pytest.raises(PziError, match="invalid BibTeX"):
        _validate_bibtex_roundtrip([_entry(title="Fine}, evil = {injected", year="2019")])


def test_the_gate_rejects_output_that_loses_an_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pzi.bib_serialize import _validate_bibtex_roundtrip

    _serialize_values_verbatim(monkeypatch)
    good = _entry(title="Fine", year="2019")
    broken: BibtexEntry = {**good, "citekey": "jones2020deep"}  # type: ignore[misc]
    broken["fields"] = {"title": "Trailing\\"}

    with pytest.raises(PziError, match="invalid BibTeX"):
        _validate_bibtex_roundtrip([good, broken])


def test_the_gate_accepts_an_entry_that_survives_unchanged() -> None:
    from pzi.bib_serialize import _validate_bibtex_roundtrip

    _validate_bibtex_roundtrip([_entry(title="Graph Networks", year="2019")])


def test_the_gate_keeps_the_duplicate_citekey_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_library`'s refusal is already phrased for the user; do not rewrap it."""
    from pzi.bib_serialize import _validate_bibtex_roundtrip

    with pytest.raises(PziError, match="duplicate citekey"):
        _validate_bibtex_roundtrip([_entry(title="One"), _entry(title="Two")])


# ---------------------------------------------------------------------------
# Canonical lock paths
# ---------------------------------------------------------------------------


def test_symlink_and_real_path_take_the_same_lock(tmp_path: Path) -> None:
    real = tmp_path / "real.bib"
    _write(real, ONE_ENTRY)
    alias = tmp_path / "alias.bib"
    alias.symlink_to(real)

    held = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with with_bib_lock(str(real)):
            held.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=_hold)
    holder.start()
    try:
        assert held.wait(timeout=10)
        with pytest.raises(PziError, match="waiting for the lock"):
            with with_bib_lock(str(alias), timeout=0.5):
                pass  # pragma: no cover — the lock must not be granted
    finally:
        release.set()
        holder.join(timeout=10)


# ---------------------------------------------------------------------------
# File fidelity: mode, line endings, BOM
# ---------------------------------------------------------------------------


def test_write_preserves_the_existing_file_mode(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    path = _write(bib, ONE_ENTRY)
    os.chmod(bib, 0o644)

    _write_bib_text_atomic(path, TWO_ENTRIES)

    assert stat.S_IMODE(os.stat(bib).st_mode) == 0o644


def test_write_preserves_crlf_line_endings(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    bib.write_bytes(ONE_ENTRY.replace("\n", "\r\n").encode("utf-8"))

    _write_bib_text_atomic(str(bib), TWO_ENTRIES)

    raw = bib.read_bytes()
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"\n") == TWO_ENTRIES.encode("utf-8")


def test_write_keeps_a_byte_order_mark_at_the_start_of_the_file(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    bib.write_bytes(b"\xef\xbb\xbf" + ONE_ENTRY.encode("utf-8"))

    result = read_bib_file(str(bib))
    assert [entry["citekey"] for entry in result["entries"]] == ["smith2020"]

    _write_bib_text_atomic(str(bib), TWO_ENTRIES)

    raw = bib.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf@article")


def test_update_plan_does_not_discard_an_edit_made_while_the_plan_was_built(
    tmp_path: Path,
) -> None:
    """An update must be rebased onto the entry as it is *under the lock*.

    `execute_write_plan` snapshots the file digest on the line before it takes
    the lock, so the window it guards is microseconds wide. The window that
    matters is much longer: `add_service` reads the library, then resolves
    metadata over the network and downloads a PDF, and only then executes. An
    edit landing in that window passes the digest check untouched, and
    `_validate_update_plan_against_current` only checks that the index is in
    range and the citekey still matches — never field content — so the stale
    `plan["entry"]` was written verbatim and the edit vanished silently.

    The insert path already rebases onto the on-disk entry when it discovers a
    match; this pins that the update path does the same.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{a2020,\n"
        "  author = {Smith, Jane},\n"
        "  title = {Original Title},\n"
        "  doi = {10.1000/abc123},\n"
        "  year = {2020}\n"
        "}\n"
    )

    # 1. Read the library and build a plan, exactly as a capture does.
    before = read_bib_file(str(bib))
    plan = plan_bib_write(
        {
            "citekey": "a2020",
            "title": "A Much Longer Title From Crossref",
            "authors": ["Smith, Jane"],
            "year": 2020,
            "doi": "10.1000/abc123",
        },
        before["records"],
        existing_entries=before["entries"],
    )
    assert plan["action"] == "update"

    # 2. The user edits the same entry in their editor while the network call
    #    is in flight — adding fields the capture knows nothing about.
    bib.write_text(
        "@article{a2020,\n"
        "  author = {Smith, Jane},\n"
        "  title = {Original Title},\n"
        "  doi = {10.1000/abc123},\n"
        "  year = {2020},\n"
        "  pages = {1--10},\n"
        "  note = {hand-written by the user}\n"
        "}\n"
    )

    # 3. The capture completes and commits its plan.
    execute_write_plan(str(bib), plan)

    written = bib.read_text()
    assert "pages = {1--10}" in written, f"external edit was overwritten:\n{written}"
    assert "hand-written by the user" in written, f"external edit was overwritten:\n{written}"
    # The update itself still applied.
    assert "A Much Longer Title From Crossref" in written
