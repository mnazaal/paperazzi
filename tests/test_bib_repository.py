from pathlib import Path

import pytest

from pzi.bib_repository import (
    ConcurrentEditError,
    apply_write_plan,
    execute_write_plan,
    parse_bibtex,
    read_bib_file,
    serialize_bibtex,
    update_bib_entry,
    with_bib_lock,
    write_bib_file,
)


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
    """External edit between mtime snapshot and lock raises ConcurrentEditError."""
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

    # Monkey-patch _bib_mtime to simulate external edit between
    # snapshot and lock acquisition.
    from pzi import bib_repository

    original_mtime = bib_repository._bib_mtime

    calls: list[float] = []

    def fake_mtime(p: str) -> float | None:
        m = Path(p).stat().st_mtime if Path(p).exists() else None
        if m is not None and len(calls) == 0:
            calls.append(m)
            return m  # first call: snapshot
        # second call: simulate modification (return different value)
        calls.append(m + 100 if m else 100)
        return m + 100 if m else 100

    bib_repository._bib_mtime = fake_mtime  # type: ignore[assignment]
    try:
        with pytest.raises(ConcurrentEditError, match="modified externally"):
            execute_write_plan(str(path), plan)
    finally:
        bib_repository._bib_mtime = original_mtime  # type: ignore[assignment]


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
    """When bib file does not exist yet, mtime check is skipped."""
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
