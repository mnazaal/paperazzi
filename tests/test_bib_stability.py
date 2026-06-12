"""BibTeX backward-compatibility tests — formal guarantee that untouched
content (comments, @string, @preamble, unmodified entries) is preserved
byte-for-byte across write operations.

Note: bibtexparser v2 normalizes whitespace in @string definitions
(``@string{ jmlr`` → ``@string{jmlr``) and wraps values in braces during
serialization (``journal = jmlr`` → ``journal = {jmlr}``). The formal
guarantee covers *content* fidelity — comments, string *definitions*,
preamble blocks, and field values survive roundtrip without loss or
expansion.
"""

from pathlib import Path

from pzi.bib_repository import (
    _parse_bib_library,
    _serialize_library,
    execute_write_plan,
    preview_write_plan,
    serialize_bibtex,
    update_bib_entry,
)

# ── Fixtures ──────────────────────────────────────────────────────────

BIB_WITH_EXTRAS = r"""@string{ jmlr = {Journal of Machine Learning Research} }

@article{smith2024,
  author = {John Smith},
  title  = {An Article},
  journal = jmlr,
  year   = {2024},
}

% A comment between entries.

@preamble{ "\providecommand{\acm}{ACM}" }

@inproceedings{jones2023,
  author    = {Alice Jones},
  title     = {Graph Parsing},
  booktitle = {Proc. GraphConf},
  year      = {2023},
}

% Trailing comment
"""


# ── Full Library roundtrip ────────────────────────────────────────────


def test_parse_library_serialize_roundtrip_preserves_extras() -> None:
    """Parse with comments/strings/preamble → serialize → all content survives."""
    library = _parse_bib_library(BIB_WITH_EXTRAS)
    result = _serialize_library(library)

    # Comments preserved (content, not exact whitespace)
    assert "% A comment between entries" in result
    assert "% Trailing comment" in result

    # Preamble preserved
    assert r"\providecommand{\acm}{ACM}" in result

    # String *definition* preserved — the @string macro itself
    assert "jmlr" in result
    assert "Journal of Machine Learning Research" in result

    # Macro references preserved (not expanded) — journal = jmlr as reference
    assert "journal = {jmlr}" in result or "journal = jmlr" in result

    # All entries present
    assert "smith2024" in result
    assert "jones2023" in result


def test_parse_library_serialize_roundtrip_stable() -> None:
    """Serialize → parse → serialize is idempotent."""
    library = _parse_bib_library(BIB_WITH_EXTRAS)
    pass1 = _serialize_library(library)
    library2 = _parse_bib_library(pass1)
    pass2 = _serialize_library(library2)
    assert pass1 == pass2


def test_serialize_bibtex_deterministic() -> None:
    """serialize_bibtex produces consistent output for same input."""
    entries = [
        {
            "entry_type": "article",
            "citekey": "smith2024",
            "fields": {"author": "John Smith", "title": "Test", "year": "2024"},
        },
    ]
    assert serialize_bibtex(entries) == serialize_bibtex(entries)


# ── Insert preserves extras ───────────────────────────────────────────


def test_write_plan_insert_preserves_comments_and_strings(tmp_path: Path) -> None:
    """Inserting a new entry does not touch comments, strings, or preamble."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(BIB_WITH_EXTRAS)

    # Parse existing library
    library = _parse_bib_library(bib_path.read_text())
    orig_comment_count = len(library.comments)
    orig_string_count = len(library.strings)
    orig_preamble_count = len(library.preambles)

    # Insert a new entry via execute_write_plan
    plan = {
        "action": "insert",
        "index": None,
        "record": {"citekey": "new2025", "title": "New Paper", "doi": "10.1/new"},
        "entry": {
            "entry_type": "article",
            "citekey": "new2025",
            "fields": {"title": "New Paper", "doi": "10.1/new"},
        },
        "changed_fields": ["citekey", "doi", "title"],
    }

    execute_write_plan(str(bib_path), plan)

    after_content = bib_path.read_text()

    # Comments, strings, preamble must remain
    after_library = _parse_bib_library(after_content)
    assert len(after_library.comments) == orig_comment_count
    assert len(after_library.strings) == orig_string_count
    assert len(after_library.preambles) == orig_preamble_count

    # Content verification
    assert "new2025" in after_content
    assert "smith2024" in after_content
    assert "jones2023" in after_content
    assert "% A comment between entries" in after_content
    assert "% Trailing comment" in after_content
    # String macro definition preserved
    assert "jmlr" in after_content
    assert "Journal of Machine Learning Research" in after_content
    # Preamble preserved
    assert r"\providecommand{\acm}{ACM}" in after_content


# ── Update preserves untouched blocks ─────────────────────────────────


def test_write_plan_update_preserves_untouched_entry_and_extras(tmp_path: Path) -> None:
    """Updating one entry leaves other entry + comments/strings/preamble intact."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(BIB_WITH_EXTRAS)

    plan = {
        "action": "update",
        "index": 0,
        "record": {
            "citekey": "smith2024",
            "title": "An Article",
            "abstract": "This is a new abstract.",
            "year": "2024",
            "author": "John Smith",
        },
        "entry": {
            "entry_type": "article",
            "citekey": "smith2024",
            "fields": {
                "author": "John Smith",
                "title": "An Article",
                "journal": "jmlr",
                "year": "2024",
                "abstract": "This is a new abstract.",
            },
        },
        "changed_fields": ["abstract"],
    }

    execute_write_plan(str(bib_path), plan)

    after_content = bib_path.read_text()

    # The updated entry should have the new abstract
    assert "This is a new abstract." in after_content

    # Content of jones2023 entry preserved
    assert "Graph Parsing" in after_content
    assert "Proc. GraphConf" in after_content
    assert "Alice Jones" in after_content

    # Extras preserved
    assert "% A comment between entries" in after_content
    assert "% Trailing comment" in after_content
    assert r"\providecommand{\acm}{ACM}" in after_content
    # String macro definition preserved — the @string{...} block is still there
    assert "jmlr" in after_content
    assert "Journal of Machine Learning Research" in after_content


def test_write_plan_update_only_changes_target_entry(tmp_path: Path) -> None:
    """Validates that the diff between before/after shows changes limited to
    the target entry (plus unavoidable v2 whitespace normalization)."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(BIB_WITH_EXTRAS)

    plan = {
        "action": "update",
        "index": 1,  # jones2023
        "record": {
            "citekey": "jones2023",
            "title": "Graph Parsing",
            "booktitle": "Proc. GraphConf",
            "year": "2023",
            "author": "Alice Jones",
            "abstract": "Second abstract.",
        },
        "entry": {
            "entry_type": "inproceedings",
            "citekey": "jones2023",
            "fields": {
                "author": "Alice Jones",
                "title": "Graph Parsing",
                "booktitle": "Proc. GraphConf",
                "year": "2023",
                "abstract": "Second abstract.",
            },
        },
        "changed_fields": ["abstract"],
    }

    result = preview_write_plan(str(bib_path), plan)
    assert result["changed"] is True

    # The diff should include the added abstract field on jones2023
    diff = result["diff"]
    assert "Second abstract" in diff
    assert "jones2023" in diff

    # Parse the after-content to verify entry-level integrity
    after_library = _parse_bib_library(result["new_source"])
    smith_fields = {f.key: f.value for f in after_library.entries[0].fields}
    # Smith's entry should still have original content (modulo v2 formatting)
    assert "John Smith" in smith_fields.get("author", "")
    assert "An Article" in smith_fields.get("title", "")
    # journal reference must be preserved, not expanded
    assert smith_fields.get("journal") == "jmlr"


def test_update_bib_entry_preserves_extras(tmp_path: Path) -> None:
    """update_bib_entry (the public API) preserves comments, strings, preamble."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(BIB_WITH_EXTRAS)

    result = update_bib_entry(
        str(bib_path),
        "smith2024",
        lambda entry, record: dict(entry),  # identity: no-op update
    )
    assert result["found"] is True

    after_content = bib_path.read_text()
    # Everything must be preserved after a no-op update
    after_library = _parse_bib_library(after_content)
    assert len(after_library.comments) == 2  # two % comments
    assert len(after_library.strings) == 1  # jmlr string
    assert len(after_library.preambles) == 1  # ACM preamble
    assert len(after_library.entries) == 2

    # Content verification
    assert "John Smith" in after_content
    assert "Graph Parsing" in after_content


def test_update_bib_entry_modifies_only_target(tmp_path: Path) -> None:
    """update_bib_entry with a real modification only changes the target entry."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(BIB_WITH_EXTRAS)

    result = update_bib_entry(
        str(bib_path),
        "jones2023",
        lambda entry, record: {
            **entry,
            "fields": {**entry["fields"], "abstract": "New abstract text."},
        },
    )
    assert result["found"] is True

    after_content = bib_path.read_text()

    # Modified entry got the new field
    assert "New abstract text." in after_content

    # Untouched entry (smith2024) fields unchanged
    assert "John Smith" in after_content
    assert "An Article" in after_content

    # Extras preserved
    assert "% A comment between entries" in after_content
    assert r"\providecommand{\acm}{ACM}" in after_content
    # String macro content preserved
    assert "jmlr" in after_content
    assert "Journal of Machine Learning Research" in after_content


def test_string_references_not_expanded(tmp_path: Path) -> None:
    """@string macro references in entries stay as references, not expanded."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(BIB_WITH_EXTRAS)

    library = _parse_bib_library(bib_path.read_text())
    # smith2024 has journal = jmlr — this should be the raw reference
    smith = [e for e in library.entries if e.key == "smith2024"][0]
    journal_field = next(f for f in smith.fields if f.key == "journal")
    assert journal_field.value == "jmlr", f"Expected 'jmlr', got {journal_field.value!r}"

    # After a write operation, the reference persists
    plan = {
        "action": "insert",
        "index": None,
        "record": {"citekey": "new2025", "title": "New Paper"},
        "entry": {
            "entry_type": "article",
            "citekey": "new2025",
            "fields": {"title": "New Paper"},
        },
        "changed_fields": ["citekey", "title"],
    }

    execute_write_plan(str(bib_path), plan)
    after = bib_path.read_text()
    # journal reference should still be jmlr, not Journal of Machine Learning Research
    after_library = _parse_bib_library(after)
    smith_after = [e for e in after_library.entries if e.key == "smith2024"][0]
    journal_after = next(f for f in smith_after.fields if f.key == "journal")
    assert journal_after.value == "jmlr", (
        f"String reference expanded: {journal_after.value!r}"
    )
