from pathlib import Path

from pzi.bib_repository import (
    apply_write_plan,
    execute_write_plan,
    parse_bibtex,
    read_bib_file,
    serialize_bibtex,
    update_bib_entry,
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
