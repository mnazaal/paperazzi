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
    StalePlanError,
    _write_bib_text_atomic,
    batch_write_session,
    delete_bib_entry,
    execute_write_plan,
    merge_bib_entries,
    plan_bib_write,
    preview_write_plan,
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

    monkeypatch.setattr(bib_repository, "validate_bibtex_roundtrip", _explode)

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

    monkeypatch.setattr(bib_repository, "validate_bibtex_roundtrip", _explode)
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
    from pzi.bib_serialize import validate_bibtex_roundtrip

    _serialize_values_verbatim(monkeypatch)

    with pytest.raises(PziError, match="invalid BibTeX"):
        validate_bibtex_roundtrip([_entry(title="Graph Networks\\", year="2019")])


def test_the_gate_rejects_output_that_parses_back_as_different_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-tripping is not enough: the entry has to come back unchanged."""
    from pzi.bib_serialize import validate_bibtex_roundtrip

    _serialize_values_verbatim(monkeypatch)

    with pytest.raises(PziError, match="invalid BibTeX"):
        validate_bibtex_roundtrip([_entry(title="Fine}, evil = {injected", year="2019")])


def test_the_gate_rejects_output_that_loses_an_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pzi.bib_serialize import validate_bibtex_roundtrip

    _serialize_values_verbatim(monkeypatch)
    good = _entry(title="Fine", year="2019")
    broken: BibtexEntry = {**good, "citekey": "jones2020deep"}  # type: ignore[misc]
    broken["fields"] = {"title": "Trailing\\"}

    with pytest.raises(PziError, match="invalid BibTeX"):
        validate_bibtex_roundtrip([good, broken])


def test_the_gate_accepts_an_entry_that_survives_unchanged() -> None:
    from pzi.bib_serialize import validate_bibtex_roundtrip

    validate_bibtex_roundtrip([_entry(title="Graph Networks", year="2019")])


def test_the_gate_keeps_the_duplicate_citekey_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_library`'s refusal is already phrased for the user; do not rewrap it."""
    from pzi.bib_serialize import validate_bibtex_roundtrip

    with pytest.raises(PziError, match="duplicate citekey"):
        validate_bibtex_roundtrip([_entry(title="One"), _entry(title="Two")])


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

    `add_service` reads the library, then resolves metadata over the network and
    downloads a PDF, and only then executes — so an edit made in that window is
    on disk before the write lands. Nothing on the way in compares field
    content (`_validate_update_plan_against_current` checks only that the index
    is in range and the citekey still matches), so without the rebase the stale
    `plan["entry"]` was written verbatim and the edit vanished silently.

    The insert path already rebases onto the on-disk entry when it discovers a
    match; this pins that the update path does the same.

    Scope, deliberately: this covers the fields the rebase does protect — one
    unmodelled (`pages`) and one user-owned that the plan does not set (`note`).
    A field the stale plan *does* carry is still lost; see the KNOWN LIMITATION
    on `_rebase_update_plan_against_current`. That is not asserted here, because
    a test asserting the loss would read as certifying it.
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


def _plan_against_current_library(bib: Path, incoming: dict) -> dict:
    """Build an update plan the way a capture does, from the library as it is."""
    before = read_bib_file(str(bib))
    plan = plan_bib_write(
        incoming, before["records"], existing_entries=before["entries"]
    )
    assert plan["action"] == "update"
    return plan


def test_a_stale_update_plan_does_not_revert_a_concurrent_writers_fields(
    tmp_path: Path,
) -> None:
    """The fields a plan merely *carries* must not win over a concurrent edit.

    `plan["entry"]` is the whole entry, not a diff: it is built by merging onto
    the entry as it was at plan time, so it holds copies of fields this writer
    never decided. Letting those win reverted the other writer silently — a
    `keywords` value they added went back to the plan-time value, and `journal`,
    which the plan's projection omits entirely, was deleted outright. Both
    writers report success, so the loss is invisible until someone looks.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{a2020,\n"
        "  author = {Smith, Jane},\n"
        "  title = {Original Title},\n"
        "  doi = {10.1000/abc123},\n"
        "  year = {2020},\n"
        "  keywords = {original},\n"
        "}\n"
    )

    plan = _plan_against_current_library(
        bib,
        {
            "citekey": "a2020",
            "title": "Original Title",
            "authors": ["Smith, Jane"],
            "year": 2020,
            "doi": "10.1000/abc123",
            "abstract": "Freshly resolved abstract.",
        },
    )

    # Another writer edits the same entry while this one is still resolving.
    def _edit(entry, record):
        fields = {**entry["fields"]}
        fields["keywords"] = "original, added-by-the-other-writer"
        fields["journal"] = "NeurIPS"
        return {**entry, "fields": fields}

    update_bib_entry(str(bib), "a2020", _edit)

    execute_write_plan(str(bib), plan)

    written = bib.read_text()
    assert "added-by-the-other-writer" in written, f"keywords reverted:\n{written}"
    assert "NeurIPS" in written, f"journal deleted:\n{written}"
    # …and this writer's own contribution still landed.
    assert "Freshly resolved abstract." in written


def test_a_stale_update_plan_still_wins_the_fields_it_changed(tmp_path: Path) -> None:
    """Deferring to the current entry must not turn into ignoring the plan."""
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{a2020,\n"
        "  author = {Smith, Jane},\n"
        "  title = {Short Title},\n"
        "  doi = {10.1000/abc123},\n"
        "  year = {2020},\n"
        "}\n"
    )

    plan = _plan_against_current_library(
        bib,
        {
            "citekey": "a2020",
            "title": "The Full Title From Crossref",
            "authors": ["Smith, Jane"],
            "year": 2020,
            "doi": "10.1000/abc123",
        },
    )

    def _edit(entry, record):
        return {**entry, "fields": {**entry["fields"], "journal": "NeurIPS"}}

    update_bib_entry(str(bib), "a2020", _edit)

    execute_write_plan(str(bib), plan)

    written = bib.read_text()
    assert "The Full Title From Crossref" in written, "the plan's own change was lost"
    assert "NeurIPS" in written


def test_a_field_the_other_writer_deleted_is_not_resurrected(tmp_path: Path) -> None:
    """A deletion is an edit too, and the plan is only carrying the old value."""
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{a2020,\n"
        "  author = {Smith, Jane},\n"
        "  title = {Original Title},\n"
        "  doi = {10.1000/abc123},\n"
        "  year = {2020},\n"
        "  note = {written in haste},\n"
        "}\n"
    )

    plan = _plan_against_current_library(
        bib,
        {
            "citekey": "a2020",
            "title": "Original Title",
            "authors": ["Smith, Jane"],
            "year": 2020,
            "doi": "10.1000/abc123",
            "abstract": "Freshly resolved abstract.",
        },
    )

    def _drop_the_note(entry, record):
        fields = {key: value for key, value in entry["fields"].items() if key != "note"}
        return {**entry, "fields": fields}

    update_bib_entry(str(bib), "a2020", _drop_the_note)

    execute_write_plan(str(bib), plan)

    assert "written in haste" not in bib.read_text()


def test_a_hand_built_plan_without_a_base_still_applies_its_deletions(
    tmp_path: Path,
) -> None:
    """`promote --replace`'s preview depends on the older entry-level rebase.

    It assembles its plan literally (`promote_service._preview_in_place_update`)
    rather than through `plan_bib_write`, so it carries no `base_entry` — and it
    strips identity fields on purpose. Three-way merging such a plan would hand
    every stripped field back and make the preview disagree with the write it is
    previewing, so a plan with no base must keep behaving as it did.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@unpublished{a2020,\n"
        "  author = {Smith, Jane},\n"
        "  title = {Original Title},\n"
        "  year = {2020},\n"
        "  journal = {arXiv preprint},\n"
        "}\n"
    )
    before = read_bib_file(str(bib))
    stripped: BibtexEntry = {
        "entry_type": "article",
        "citekey": "a2020",
        "fields": {
            "author": "Smith, Jane",
            "title": "Original Title",
            "year": "2020",
        },
    }
    plan = {
        "action": "update",
        "index": 0,
        "record": before["records"][0],
        "entry": stripped,
        "changed_fields": [],
    }
    assert "base_entry" not in plan

    execute_write_plan(str(bib), plan)

    written = bib.read_text()
    assert "arXiv preprint" not in written, "a deliberate strip was undone"
    assert "@article" in written, "the deliberate retype was undone"


def test_an_update_plan_whose_record_has_no_citekey_is_refused(
    tmp_path: Path,
) -> None:
    """The citekey guard must not be skippable by carrying no citekey.

    `_validate_update_plan_against_current` is the one thing standing between a
    write and the wrong entry, and it used to read
    `if planned_citekey and current_citekey != planned_citekey` — so a plan whose
    record has an empty or missing citekey skipped the comparison entirely and
    the rebase wrote onto whatever occupied `plan["index"]`. Measured against the
    unguarded code, this plan replaced `jones2021` outright: its citekey, title
    and year gone, the second entry now `@article{keyless2099}`.

    The planned entry's citekey is deliberately one the library does not already
    hold. Reusing `smith2020` here made the write collide with the first entry
    and get refused by the duplicate-citekey gate in `validate_bibtex_roundtrip`
    instead — so the test passed identically with this guard removed, certifying
    a downstream accident rather than the guard it names.

    No producer can build such a plan today (`record_to_bibtex_entry` refuses a
    keyless record at plan time), which is the reason to pin it: the guard should
    not depend on an upstream refusal for its own precondition.

    Refused as malformed, not stale, and that distinction is asserted: a
    `StalePlanError` sends `add_service` back to replan under a second lock and,
    when the replan does not help, deletes the downloaded PDF — an expensive
    round trip for a plan that is wrong in itself rather than out of date.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(TWO_ENTRIES)
    before = bib.read_text()

    keyless_record = {
        key: value for key, value in read_bib_file(str(bib))["records"][0].items()
        if key != "citekey"
    }
    plan = {
        "action": "update",
        "index": 1,  # the *other* entry
        "record": keyless_record,
        "entry": {
            "entry_type": "article",
            "citekey": "keyless2099",
            "fields": {"title": "Rewritten By A Keyless Plan"},
        },
        "changed_fields": ["title"],
    }

    with pytest.raises(PziError) as excinfo:
        execute_write_plan(str(bib), plan)

    assert not isinstance(excinfo.value, StalePlanError)
    assert bib.read_text() == before, "a refused plan still wrote"
    assert "jones2021" in bib.read_text(), "the entry it targeted was overwritten"


def _add_a_keyword(entry, record):
    """An updater that really changes something — an identity one never writes."""
    return {**entry, "fields": {**entry["fields"], "keywords": "ml"}}


@pytest.mark.parametrize(
    "key",
    [
        "författare-not",   # Swedish, hand-added — legal biblatex
        "Nyckelord",
        "année",
        "機械",
        "bdsk-url-1",       # BibDesk
        "date-added",
        "__markedentry",    # JabRef
        "a.b",
        "a+b",
        "a:b",
    ],
)
def test_a_unicode_field_key_does_not_block_writing_the_library(
    tmp_path: Path, key: str
) -> None:
    """A field key of letters and digits is legal BibTeX, whatever the alphabet.

    The gate was ASCII-only, so one hand-edited or biblatex-native key anywhere
    in a library made *every* write fail — including an `import` of unrelated
    entries, with a message naming an entry the user had not touched. It also
    made a dry run disagree with the real run, because only the real write
    validates the whole library.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(f"@article{{legacy2019,\n  title = {{T}},\n  {key} = {{v}}\n}}\n")

    update_bib_entry(str(bib), "legacy2019", _add_a_keyword)

    assert key.lower() in bib.read_text().lower()


@pytest.mark.parametrize(
    "key",
    [
        "a b",   # a space is not a legal BibTeX field name
        "a/b",
        "a#b",
        "a@b",
        # `a=b` and `a,b` are deliberately absent: the parser truncates the
        # first to `a` and files the second as a failed block, so neither ever
        # reaches this gate.
    ],
)
def test_a_structurally_unsafe_field_key_is_still_refused(
    tmp_path: Path, key: str
) -> None:
    """Widening the gate to Unicode must not widen it to anything at all.

    bibtexparser round-trips `a b` happily, but that is leniency, not legality —
    these characters either break real BibTeX readers or could escape the
    `key = {value}` structure the serializer writes.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(f"@article{{legacy2019,\n  title = {{T}},\n  {key} = {{v}}\n}}\n")
    before = bib.read_text()

    with pytest.raises(PziError):
        update_bib_entry(str(bib), "legacy2019", _add_a_keyword)

    assert bib.read_text() == before


def test_a_batch_dry_run_and_the_real_write_reach_the_same_verdict(tmp_path: Path) -> None:
    """A preview must never disagree with the write it predicts.

    The original bug: `batch_write_session` returned before `check_consistency`
    and the round-trip gate, so `import --dry-run` said `would_import` at exit 0
    and `import` then exited 5 having written nothing.

    **The verdict this reaches changed on 2026-08-23 (item 567), the agreement
    did not.** The gate's round-trip half is now scoped to the entries a write
    touched, so a *pre-existing* entry that does not round-trip — here a field
    key containing a space, which is not legal BibTeX — no longer blocks an
    unrelated insert. Nothing is corrupted by allowing it: that entry is written
    back as the exact bytes it was read as (item 566), so the file keeps the
    illegal entry it already had. What is lost is a diagnostic, and item 574 is
    where it went — `library clean` reports it, rather than every unrelated
    write refusing. Citekey uniqueness is *not* scoped, because it is a property
    of the pair; see `tests/test_write_gate_scope.py`.
    """
    def attempt(*, write: bool) -> tuple[bool, str]:
        bib = tmp_path / f"lib-{write}.bib"
        bib.write_text(
            "@article{legacy2019,\n  title = {Hand Edited},\n  bad key = {v}\n}\n"
        )
        try:
            with batch_write_session(str(bib), write=write) as session:
                session.apply_plan(
                    plan_bib_write(
                        {"citekey": "new2021", "title": "New"},
                        session.records,
                        existing_entries=session.entries,
                    )
                )
        except PziError:
            return False, bib.read_text()
        return True, bib.read_text()

    preview_ok, _ = attempt(write=False)
    write_ok, written = attempt(write=True)

    assert preview_ok == write_ok, (
        "a dry run and the write it predicts must reach the same verdict"
    )
    # The verdict, pinned rather than assumed.
    assert write_ok is True
    # And the untouched illegal entry survives byte-for-byte, uncorrupted.
    assert "bad key = {v}" in written
    assert "new2021" in written


# ---------------------------------------------------------------------------
# A failed write leaves no `.bak`
# ---------------------------------------------------------------------------


def _retitle(entry: BibtexEntry, _record: object) -> BibtexEntry:
    return {**entry, "fields": {**entry["fields"], "title": "Changed"}}


#: Every function that copies the bib to a `.bak` under its own lock, keyed by
#: name so a failure names the site. `reindex_service` unlinks its backup when
#: the write it guards raises; these three are the same shape and must too — a
#: `.bak` that survives a failed write is a snapshot of a file nothing replaced,
#: and `backup_path_for` then hands the *next* run `.bak2`.
BACKUP_WRITERS = {
    "delete_bib_entry": lambda path, backup: delete_bib_entry(
        path, "smith2020", backup_path=backup
    ),
    "merge_bib_entries": lambda path, backup: merge_bib_entries(
        path, citekey_a="smith2020", citekey_b="jones2021", backup_path=backup
    ),
    "update_bib_entry": lambda path, backup: update_bib_entry(
        path, "smith2020", _retitle, backup_path=backup
    ),
}


@pytest.mark.parametrize("writer_name", sorted(BACKUP_WRITERS))
def test_a_failed_write_leaves_no_stale_backup(
    writer_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `.bak` is removed when the write it was taken for does not happen."""
    bib_path = _write(tmp_path / "lib.bib", TWO_ENTRIES)
    backup = Path(bib_path + ".bak")

    def explode(_path: str, _text: str) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("pzi.bib_repository._write_bib_text_atomic", explode)

    with pytest.raises(OSError):
        BACKUP_WRITERS[writer_name](bib_path, backup)

    assert not backup.exists(), f"{writer_name} left a stale backup at {backup}"
    assert Path(bib_path).read_text(encoding="utf-8") == TWO_ENTRIES


@pytest.mark.parametrize("writer_name", sorted(BACKUP_WRITERS))
def test_a_successful_write_keeps_its_backup(
    writer_name: str, tmp_path: Path
) -> None:
    """The other half of the invariant: the undo survives a write that happens."""
    bib_path = _write(tmp_path / "lib.bib", TWO_ENTRIES)
    backup = Path(bib_path + ".bak")

    BACKUP_WRITERS[writer_name](bib_path, backup)

    assert backup.read_text(encoding="utf-8") == TWO_ENTRIES
    assert Path(bib_path).read_text(encoding="utf-8") != TWO_ENTRIES


@pytest.mark.parametrize("action", ["insert", "update"])
def test_the_preview_and_the_write_render_the_same_source(
    action: str, tmp_path: Path
) -> None:
    """One plan, one rendering: the preview must predict the write exactly.

    `preview_write_plan` and `execute_write_plan` were byte-identical from the
    read to the render and now share `_prepare_write`. This asserts the property
    that sharing exists to hold — the pair that drifted before is the pair the
    dry run's whole value rests on.
    """
    original = (
        "@article{smith2020,\n  title = {A Title},\n  year = {2020},\n"
        "  doi = {10.1000/one},\n}\n"
    )
    bib_path = _write(tmp_path / "lib.bib", original)
    read = read_bib_file(bib_path)
    record = (
        {"citekey": "ignored", "doi": "10.1000/one", "abstract": "New."}
        if action == "update"
        else {"citekey": "fresh2022", "doi": "10.1000/two", "title": "Fresh"}
    )
    plan = plan_bib_write(
        record, read["records"], existing_entries=read["entries"]
    )
    assert plan["action"] == action

    preview = preview_write_plan(bib_path, plan)
    assert Path(bib_path).read_text(encoding="utf-8") == original, "preview wrote"

    execute_write_plan(bib_path, plan)

    assert preview["changed"] is True
    assert preview["new_source"] == Path(bib_path).read_text(encoding="utf-8")
