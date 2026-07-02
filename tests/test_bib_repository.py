import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from pzi.bib_repository import (
    ConcurrentEditError,
    apply_write_plan,
    execute_write_plan,
    parse_bib_library,
    parse_bibtex,
    read_bib_file,
    serialize_bibtex,
    update_bib_entry,
    with_bib_lock,
    write_bib_file,
)
from pzi.bib_serialize import _balance_braces, _safe_citekey, _safe_field_value
from pzi.bibtex import record_to_bibtex_entry


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


def test_write_bib_file_keeps_absolute_file_paths_by_default(tmp_path: Path) -> None:
    bib_path = tmp_path / "refs.bib"
    pdf_path = tmp_path / "papers" / "smith2024.pdf"

    write_bib_file(
        str(bib_path),
        [
            {
                "entry_type": "article",
                "citekey": "smith2024",
                "fields": {"title": "T", "file": str(pdf_path)},
            }
        ],
    )

    assert f"file = {{{pdf_path}}}" in bib_path.read_text(encoding="utf-8")


def test_write_bib_file_can_write_relative_file_paths(tmp_path: Path) -> None:
    bib_path = tmp_path / "refs.bib"
    pdf_path = tmp_path / "papers" / "smith2024.pdf"

    write_bib_file(
        str(bib_path),
        [
            {
                "entry_type": "article",
                "citekey": "smith2024",
                "fields": {"title": "T", "file": str(pdf_path)},
            }
        ],
        file_path_style="relative",
    )

    assert "file = {papers/smith2024.pdf}" in bib_path.read_text(encoding="utf-8")


def test_write_bib_file_writes_through_a_symlink(tmp_path: Path) -> None:
    # `os.replace` treats a symlink destination as the directory entry to
    # replace, not the file it points at. Writing straight to a symlinked
    # path would silently delete the symlink and drop a regular file in its
    # place, detaching it from wherever it used to point (e.g. synced cloud
    # storage). The write must land on the real target and the symlink must
    # survive.
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_path = real_dir / "library.bib"
    real_path.write_text("", encoding="utf-8")
    link_path = tmp_path / "refs.bib"
    link_path.symlink_to(real_path)

    write_bib_file(
        str(link_path),
        [{"entry_type": "article", "citekey": "smith2024", "fields": {"title": "T"}}],
    )

    assert link_path.is_symlink()
    assert link_path.resolve() == real_path
    assert "smith2024" in real_path.read_text(encoding="utf-8")


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
    malicious = {
        "citekey": "evil2024} @article{injected, title={pwned}, x={",
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


def test_write_bib_file_preserves_original_and_cleans_up_temp_on_replace_failure(
    tmp_path: Path,
) -> None:
    """If the final os.replace fails (simulated crash mid-write), the original
    file must be untouched and no leftover .tmp file should remain."""
    path = tmp_path / "library.bib"
    path.write_text("original content\n")

    with patch("os.replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError, match="simulated crash"):
            write_bib_file(
                str(path),
                [
                    {
                        "entry_type": "article",
                        "citekey": "smith2024graph",
                        "fields": {"title": "New"},
                    }
                ],
            )

    assert path.read_text() == "original content\n"
    assert list(tmp_path.iterdir()) == [path]


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
