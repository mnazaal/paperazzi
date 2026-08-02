import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from pzi.add_service import ensure_citekey_for_write
from pzi.bib_repository import (
    ConcurrentEditError,
    _write_bib_text_atomic,
    apply_write_plan,
    execute_write_plan,
    parse_bib_library,
    parse_bibtex,
    plan_bib_write,
    preview_write_plan,
    read_bib_file,
    update_bib_entry,
    with_bib_lock,
)
from pzi.bib_serialize import _balance_braces, _safe_citekey, _safe_field_value, serialize_bibtex
from pzi.bibtex import record_to_bibtex_entry
from pzi.errors import PziError


def test_parse_bibtex_reads_entries_and_fields() -> None:
    entries = parse_bibtex(
        """
@article{smith2024graph,
  title = {Graph Parsers},
  doi = {10.1/foo},
}
""".strip()
    )

    assert entries == [
        {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {
                "title": "Graph Parsers",
                "doi": "10.1/foo",
            },
        }
    ]


def test_serialize_bibtex_writes_deterministic_output() -> None:
    text = serialize_bibtex(
        [
            {
                "entry_type": "article",
                "citekey": "smith2024graph",
                "fields": {
                    "title": "Graph Parsers",
                    "doi": "10.1/foo",
                },
            }
        ]
    )

    assert (
        text
        == "@article{smith2024graph,\n  doi = {10.1/foo},\n  title = {Graph Parsers}\n}\n"
    )


def test_apply_write_plan_appends_insert_entry() -> None:
    updated = apply_write_plan(
        [],
        {
            "action": "insert",
            "index": None,
            "record": {"citekey": "smith2024graph"},
            "entry": {
                "entry_type": "article",
                "citekey": "smith2024graph",
                "fields": {"title": "Graph Parsers"},
            },
            "changed_fields": ["citekey", "title"],
        },
    )

    assert updated == [
        {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"title": "Graph Parsers"},
        }
    ]


def test_apply_write_plan_replaces_updated_entry() -> None:
    updated = apply_write_plan(
        [
            {
                "entry_type": "article",
                "citekey": "smith2024graph",
                "fields": {"title": "Old Title"},
            }
        ],
        {
            "action": "update",
            "index": 0,
            "record": {"citekey": "smith2024graph"},
            "entry": {
                "entry_type": "article",
                "citekey": "smith2024graph",
                "fields": {"title": "New Title"},
            },
            "changed_fields": ["title"],
        },
    )

    assert updated == [
        {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"title": "New Title"},
        }
    ]


def test_read_bib_file_returns_entries_and_records(tmp_path: Path) -> None:
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{smith2024graph,
  author = {Smith, Jane and Doe, John},
  doi = {10.1/foo},
  title = {Graph Parsers},
  year = {2024},
}
""".strip()
    )

    result = read_bib_file(str(path))

    assert result["entries"][0]["citekey"] == "smith2024graph"
    assert result["records"] == [
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "authors": ["Smith, Jane", "Doe, John"],
            "year": 2024,
            "venue": None,
            "doi": "10.1/foo",
            "arxiv_id": None,
            "canonical_url": None,
            "source_url": None,
            "pdf_url": None,
            "abstract_url": None,
            "tags": [],
            "note": None,
            "local_pdf_path": None,
            "abstract": None,
        }
    ]


def test_execute_write_plan_updates_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{smith2024graph,
  title = {Old Title},
}
""".strip()
    )

    updated = execute_write_plan(
        str(path),
        {
            "action": "update",
            "index": 0,
            "record": {"citekey": "smith2024graph", "title": "New Title"},
            "entry": {
                "entry_type": "article",
                "citekey": "smith2024graph",
                "fields": {"title": "New Title"},
            },
            "changed_fields": ["title"],
        },
    )

    assert updated == [
        {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"title": "New Title"},
        }
    ]
    assert path.read_text() == "@article{smith2024graph,\n  title = {New Title}\n}\n"


def test_update_bib_entry_updates_matching_entry_under_lock(tmp_path: Path) -> None:
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{smith2024graph,
  title = {Old Title},
}
""".strip()
    )

    result = update_bib_entry(
        str(path),
        "smith2024graph",
        lambda entry, record: {
            "entry_type": entry["entry_type"],
            "citekey": entry["citekey"],
            "fields": {**entry["fields"], "title": "New Title"},
        },
    )

    assert result["found"] is True
    assert result["entry"] == {
        "entry_type": "article",
        "citekey": "smith2024graph",
        "fields": {"title": "New Title"},
    }
    assert result["record"]["title"] == "New Title"
    assert path.read_text() == "@article{smith2024graph,\n  title = {New Title}\n}\n"


def test_update_bib_entry_returns_not_found_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "library.bib"
    path.write_text("")

    result = update_bib_entry(
        str(path),
        "missing",
        lambda entry, record: entry,
    )

    assert result == {
        "found": False,
        "entries": [],
        "entry": None,
        "record": None,
    }


def test_with_bib_lock_creates_lock_file_and_releases(tmp_path: Path) -> None:
    bib_path = tmp_path / "library.bib"
    lock_file = Path(str(bib_path) + ".lock")
    with with_bib_lock(str(bib_path)):
        assert lock_file.exists()

    with with_bib_lock(str(bib_path)):
        pass


def test_with_bib_lock_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "library.bib"
    with with_bib_lock(str(nested)):
        assert nested.parent.exists()


# ---------------------------------------------------------------------------
# Concurrent edit detection
# ---------------------------------------------------------------------------


def test_execute_write_plan_raises_on_external_edit(tmp_path: Path) -> None:
    """External edit between content snapshot and lock raises ConcurrentEditError."""
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{smith2024graph,
  title = {Original},
}
""".strip()
    )

    plan = {
        "action": "update",
        "index": 0,
        "record": {"citekey": "smith2024graph", "title": "Updated"},
        "entry": {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"title": "Updated"},
        },
        "changed_fields": ["title"],
    }

    # Monkey-patch _read_bib_source so the under-lock read differs from the
    # pre-lock snapshot, simulating an external edit during lock acquisition.
    from pzi import bib_repository

    original_read = bib_repository._read_bib_source
    calls: list[int] = []

    def fake_read(p: str) -> str:
        calls.append(1)
        text = original_read(p)
        if len(calls) == 1:
            return text  # first call: pre-lock snapshot
        return text + "\n@misc{injected,\n  title = {Sneaked in},\n}\n"

    bib_repository._read_bib_source = fake_read  # type: ignore[assignment]
    try:
        with pytest.raises(ConcurrentEditError, match="modified externally"):
            execute_write_plan(str(path), plan)
    finally:
        bib_repository._read_bib_source = original_read  # type: ignore[assignment]


def test_execute_write_plan_succeeds_without_external_edit(tmp_path: Path) -> None:
    """Normal execution when no external edit occurs."""
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{smith2024graph,
  title = {Original},
}
""".strip()
    )

    plan = {
        "action": "update",
        "index": 0,
        "record": {"citekey": "smith2024graph", "title": "Updated"},
        "entry": {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"title": "Updated"},
        },
        "changed_fields": ["title"],
    }

    updated = execute_write_plan(str(path), plan)
    assert updated[0]["fields"]["title"] == "Updated"
    assert "Updated" in path.read_text()


def test_execute_write_plan_skips_check_for_new_file(tmp_path: Path) -> None:
    """When bib file does not exist yet, the empty snapshot matches and the write proceeds."""
    path = tmp_path / "new.bib"

    plan = {
        "action": "insert",
        "index": None,
        "record": {"citekey": "smith2024graph", "title": "New"},
        "entry": {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"title": "New"},
        },
        "changed_fields": ["title"],
    }

    updated = execute_write_plan(str(path), plan)
    assert updated[0]["fields"]["title"] == "New"
    assert "New" in path.read_text()


# === injection resistance: untrusted metadata must not corrupt the .bib ===


def test_serialize_neutralizes_bibtex_injection() -> None:
    """Hostile *field values* are neutralized at the serialization chokepoint.

    The citekey half of this is now enforced in two places instead of one: a
    composed key is sanitized where it enters (see
    `ensure_citekey_for_write`), and serialization *refuses* a key that could
    still break out — see `test_serialize_refuses_a_breakout_citekey`. Silently
    rewriting the key here was the mechanism that renamed hand-written
    citekeys off the user's disk.
    """
    malicious = {
        "citekey": ensure_citekey_for_write(
            {"citekey": "evil2024} @article{injected, title={pwned}, x={"},  # type: ignore[arg-type]
            [],
        )["citekey"],
        "title": "T} @string{m=1} @article{evil2, author={y",
        "authors": ["Bar} @article{z, t={"],
        "year": 2024,
        "abstract": "x\n@article{fromabstract, t={y}",
    }
    text = serialize_bibtex([record_to_bibtex_entry(malicious)])
    library = parse_bib_library(text)

    # The whole thing must round-trip as exactly one well-formed entry: nothing
    # broke out of the citekey or a field value to form an injected block.
    assert len(library.entries) == 1
    assert library.failed_blocks == []
    assert "@article{injected" not in text  # the breakout `{` was neutralized
    assert "@article{fromabstract" not in text


def test_serialize_refuses_a_breakout_citekey() -> None:
    malicious = {
        "citekey": "evil2024} @article{injected, title={pwned}, x={",
        "title": "T",
        "year": 2024,
    }
    with pytest.raises(PziError, match="cannot appear in a BibTeX entry key"):
        serialize_bibtex([record_to_bibtex_entry(malicious)])


def test_safe_citekey_strips_unsafe_characters() -> None:
    assert _safe_citekey("smith2020graph") == "smith2020graph"
    assert _safe_citekey("smith:2020-graph_v2") == "smith:2020-graph_v2"
    assert _safe_citekey("evil} @article{x,") == "evilarticlex"
    assert _safe_citekey("}{@, ") == "untitled"
    # Path separators are stripped so a citekey cannot carry path components
    # (it doubles as the PDF filename stem).
    assert _safe_citekey("../../etc/passwd") == "etcpasswd"
    assert _safe_citekey("a/b/c") == "abc"


def test_balance_braces_keeps_balanced_and_drops_stray() -> None:
    assert _balance_braces("The {DNA} story") == "The {DNA} story"
    assert _balance_braces("plain text") == "plain text"
    assert "}" not in _balance_braces("Foo} @article{x").replace("{", "")
    assert _balance_braces("{unclosed") == "unclosed"
    assert _balance_braces("unopened}") == "unopened"


def test_safe_field_value_strips_control_chars_but_keeps_tab_newline() -> None:
    out = _safe_field_value("Title\x00with\x07nul\x1f\tkeep-tab\nkeep-nl")
    assert "\x00" not in out and "\x07" not in out and "\x1f" not in out
    assert "\t" in out and "\n" in out


# === concurrency: with_bib_lock must serialize readers/writers correctly ===


def test_update_bib_entry_two_threads_no_lost_update_and_no_stale_lock(
    tmp_path: Path,
) -> None:
    """Regression/contention check for the central with_bib_lock invariant.

    Several threads race read-modify-write cycles through update_bib_entry.
    If the lock ever let two writers interleave, some increments would be
    lost (final count < total). If a lock were ever left stuck ("stale")
    after a holder released it, a waiting thread would hang past the join
    timeout instead of completing.
    """
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{counter2024entry,
  counter = {0},
}
""".strip()
    )

    increments_per_thread = 20
    thread_count = 4
    errors: list[BaseException] = []

    def bump() -> None:
        try:
            for _ in range(increments_per_thread):
                update_bib_entry(
                    str(path),
                    "counter2024entry",
                    lambda entry, record: {
                        **entry,
                        "fields": {
                            **entry["fields"],
                            "counter": str(int(entry["fields"]["counter"]) + 1),
                        },
                    },
                )
        except BaseException as exc:  # noqa: BLE001 - captured for the main thread
            errors.append(exc)

    threads = [threading.Thread(target=bump) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "thread still running — with_bib_lock appears stuck"

    assert not errors, errors

    final = read_bib_file(str(path))
    assert final["entries"][0]["fields"]["counter"] == str(
        thread_count * increments_per_thread
    )


# === crash injection: atomic bib writes must not corrupt or litter ===


def test_write_bib_text_atomic_preserves_original_and_cleans_up_temp_on_failure(
    tmp_path: Path,
) -> None:
    """If the final os.replace fails (simulated crash mid-write), the original
    file must be untouched and no leftover .tmp file should remain.

    Ported from the deleted `write_bib_file`; `docs/remediation-plan-2026-07.md`
    records this catching a real bug, so the guarantee outlives the function it
    was originally written against.
    """
    path = tmp_path / "library.bib"
    path.write_text("original content\n")

    with patch("os.replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError, match="simulated crash"):
            _write_bib_text_atomic(
                str(path), "@article{smith2024graph,\n  title = {New}\n}\n"
            )

    assert path.read_text() == "original content\n"
    assert list(tmp_path.iterdir()) == [path]


def test_write_bib_text_atomic_writes_through_a_symlink(tmp_path: Path) -> None:
    # `os.replace` treats a symlink destination as the directory entry to
    # replace, not the file it points at. Writing straight to a symlinked path
    # would silently delete the symlink and drop a regular file in its place,
    # detaching it from wherever it used to point (e.g. synced cloud storage).
    # Also ported from `write_bib_file`.
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_path = real_dir / "library.bib"
    real_path.write_text("", encoding="utf-8")
    link_path = tmp_path / "refs.bib"
    link_path.symlink_to(real_path)

    _write_bib_text_atomic(
        str(link_path), "@article{smith2024,\n  title = {T}\n}\n"
    )

    assert link_path.is_symlink()
    assert link_path.resolve() == real_path
    assert "smith2024" in real_path.read_text(encoding="utf-8")


# === malformed / unicode corpora ===


def test_read_bib_file_preserves_valid_entries_around_a_malformed_block(
    tmp_path: Path,
) -> None:
    """A syntactically broken block must not take down the whole file: valid
    entries before and after it (including non-ASCII fields) still load."""
    path = tmp_path / "library.bib"
    path.write_text(
        """
@article{good2024one,
  title = {A Valid Paper with Ünïcödé Ünïcödé},
  author = {Müller, Anna},
  year = {2024},
}

@article{broken2024,
  title = {Missing closing brace
  author = {Someone},
  year = {2024},
}

@article{good2024two,
  title = {Another Valid Entry},
  author = {Smith, Bob},
  year = {2023},
}
""".strip()
    )

    result = read_bib_file(str(path))

    citekeys = [r["citekey"] for r in result["records"]]
    assert citekeys == ["good2024one", "good2024two"]
    assert "Ünïcödé" in result["records"][0]["title"]


def test_execute_write_plan_rebase_keeps_on_disk_entry_type_and_fields(
    tmp_path: Path,
) -> None:
    """Rebasing a stale insert onto a now-present entry must merge, not overwrite.

    This is the race the rebase exists for: the plan was built from a snapshot
    taken before another writer committed the same paper. Rebasing it to an
    update must keep the on-disk entry's type and its unmodelled fields, exactly
    as a plan built against the current snapshot would.
    """
    path = tmp_path / "library.bib"
    path.write_text(
        """
@inproceedings{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, Ada},
  year = {2024},
  booktitle = {Proceedings of Things},
  volume = {12},
  pages = {1--10},
  publisher = {ACM},
  doi = {10.1/foo},
}
""".strip()
    )

    # Planned against an empty snapshot: the other writer's entry is not visible.
    plan = plan_bib_write(
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "authors": ["Smith, Ada"],
            "year": 2024,
            "venue": "Proceedings of Things",
            "doi": "10.1/foo",
        },
        [],
    )
    assert plan["action"] == "insert"
    assert plan["entry"]["entry_type"] == "article"

    updated = execute_write_plan(str(path), plan)

    assert len(updated) == 1
    entry = updated[0]
    assert entry["entry_type"] == "inproceedings"
    assert entry["fields"]["booktitle"] == "Proceedings of Things"
    assert "journal" not in entry["fields"]
    assert entry["fields"]["volume"] == "12"
    assert entry["fields"]["pages"] == "1--10"
    assert entry["fields"]["publisher"] == "ACM"


def test_delete_bib_entry_writes_the_backup_under_its_own_lock(tmp_path: Path) -> None:
    """The backup must reflect the file the delete actually operated on.

    It used to be copied before the exclusive lock was taken, so a writer
    interleaving in that window made the `.bak` a snapshot of a version that no
    longer existed — restoring it would revert that writer's work too.
    """
    from pzi.bib_repository import delete_bib_entry

    path = tmp_path / "library.bib"
    path.write_text(
        "@article{keep2024, title = {Keep}}\n@article{drop2024, title = {Drop}}\n"
    )
    backup = tmp_path / "library.bak"

    result = delete_bib_entry(str(path), "drop2024", backup_path=backup)

    assert result["found"] is True
    # The backup holds the pre-delete content, taken under the same lock.
    assert "drop2024" in backup.read_text()
    assert "keep2024" in backup.read_text()
    # ...and the live file no longer does.
    assert "drop2024" not in path.read_text()
    assert "keep2024" in path.read_text()


def test_delete_bib_entry_writes_no_backup_when_the_citekey_is_missing(
    tmp_path: Path,
) -> None:
    """A no-op delete should not leave a stray .bak behind."""
    from pzi.bib_repository import delete_bib_entry

    path = tmp_path / "library.bib"
    path.write_text("@article{keep2024, title = {Keep}}\n")
    backup = tmp_path / "library.bak"

    result = delete_bib_entry(str(path), "nosuch2024", backup_path=backup)

    assert result["found"] is False
    assert not backup.exists()


def test_with_bib_lock_times_out_instead_of_blocking_forever(tmp_path: Path) -> None:
    """A lock held by another process used to hang pzi silently and forever.

    `portalocker.lock` takes no timeout and blocks in the kernel, and
    `ConcurrentEditError` only fires *after* the lock is acquired — so nothing
    could produce the exit 5 that `exit_codes` documents for a locked bib.
    """
    import portalocker

    from pzi.bib_repository import with_bib_lock
    from pzi.errors import PziError
    from pzi.exit_codes import ENVIRONMENT

    path = tmp_path / "library.bib"
    path.write_text("@article{a2024, title = {A}}\n")
    lock_path = tmp_path / "library.bib.lock"

    # Hold the lock on a separate open-file-description, as another process would.
    with open(str(lock_path), "a") as holder:
        portalocker.lock(holder, portalocker.LOCK_EX | portalocker.LOCK_NB)
        with pytest.raises(PziError) as excinfo:
            with with_bib_lock(str(path), timeout=0.05):
                pytest.fail("acquired a lock that was already held exclusively")

    assert excinfo.value.code == ENVIRONMENT
    assert str(path) in str(excinfo.value)
    assert "timed out" in str(excinfo.value)


def test_with_bib_lock_still_acquires_a_free_lock(tmp_path: Path) -> None:
    """The non-blocking retry loop must not break the ordinary uncontended path."""
    from pzi.bib_repository import with_bib_lock

    path = tmp_path / "library.bib"
    path.write_text("@article{a2024, title = {A}}\n")

    with with_bib_lock(str(path)):
        pass
    # Released, so a second acquisition succeeds too.
    with with_bib_lock(str(path), shared=True):
        pass


def test_write_paths_parse_the_library_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """Each write path parsed the whole .bib twice, and dry-run-then-write four times.

    `_render_write_plan` re-read the same in-memory source, re-parsed it,
    re-projected every entry to a record and re-applied the plan — all of it
    already done by its callers, under the same lock, on the same string. This
    pins the collapse so it cannot silently regress.
    """
    from pzi import bib_repository

    calls = {"n": 0}
    real = bib_repository._parse_bib_library

    def counting(source: str):
        calls["n"] += 1
        return real(source)

    monkeypatch.setattr(bib_repository, "_parse_bib_library", counting)

    path = tmp_path / "library.bib"
    path.write_text("@article{a2024,\n  title = {A}\n}\n")

    insert = plan_bib_write({"citekey": "b2024", "title": "B"}, [])
    calls["n"] = 0
    execute_write_plan(str(path), insert)
    assert calls["n"] == 1, "insert re-parsed the library"

    records = read_bib_file(str(path))["records"]
    update = plan_bib_write({"citekey": "c2024", "title": "C"}, records)
    calls["n"] = 0
    preview_write_plan(str(path), update)
    assert calls["n"] == 1, "preview re-parsed the library"

    calls["n"] = 0
    update_bib_entry(
        str(path),
        "a2024",
        lambda entry, record: {**entry, "fields": {**entry["fields"], "year": "2024"}},
    )
    assert calls["n"] == 1, "update_bib_entry re-parsed the library"


def test_write_refusal_names_the_duplicate_and_its_real_line(tmp_path: Path) -> None:
    """The refusal message interpolated a 0-based line index.

    A duplicate whose second block starts on file line 4 was reported as
    "around line 3", and the message named no citekey at all — while
    `describe_failed_blocks` had a correct, specific message all along.
    """
    path = tmp_path / "library.bib"
    path.write_text(
        "@article{smith2024,\n"
        "  title = {First}\n"
        "}\n"
        "@article{smith2024,\n"
        "  title = {Second}\n"
        "}\n"
    )

    with pytest.raises(ValueError) as excinfo:
        update_bib_entry(
            str(path),
            "smith2024",
            lambda entry, record: {**entry, "fields": {**entry["fields"], "year": "2024"}},
        )

    message = str(excinfo.value)
    assert "duplicate citekey 'smith2024'" in message
    assert "at line 4" in message
    assert "line 3" not in message
    # The file is untouched: refusing to rewrite is the whole point.
    assert path.read_text().count("smith2024") == 2
