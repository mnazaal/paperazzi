"""BibTeX backward-compatibility tests — formal guarantee that untouched
content (comments, @string, @preamble, unmodified entries) is preserved
byte-for-byte across write operations.

Note: bibtexparser v2 normalizes whitespace in @string definitions
(``@string{ jmlr`` → ``@string{jmlr``). The formal guarantee covers *content*
fidelity — comments, string *definitions*, ``@string`` macro *references*,
preamble blocks, and field values survive roundtrip without loss or
expansion.

Macro references are guaranteed for entries a write plan does not touch. The
one entry a plan rewrites is re-serialized from the internal record model,
which carries no enclosings, so that entry's own references do not survive —
see ``test_update_severs_macros_only_in_the_entry_it_touches``. That is the
narrowed residue of the old behavior, in which any write severed the macro
references of every entry in the file.
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

    # Macro references preserved as references. `journal = {jmlr}` is NOT a
    # preserved reference — it is a literal string that happens to spell the
    # macro's name, and it no longer resolves to the @string definition above.
    assert "journal = jmlr" in result
    assert "journal = {jmlr}" not in result

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
    # Assert on the raw text too: the parsed value is `jmlr` whether the field is
    # a live macro reference or the literal string `{jmlr}`, because the parse
    # strips enclosings. Only the source text distinguishes the two.
    assert "journal = jmlr" in after
    assert "journal = {jmlr}" not in after


def test_write_plan_update_keeps_macro_references_in_other_entries(
    tmp_path: Path,
) -> None:
    """Editing one entry must not sever @string references in the rest of the file.

    Every write re-serializes the whole library, so a rebuild of untouched entry
    blocks would flatten `journal = jmlr` to the literal `{jmlr}` across the
    file on the first mutation of any entry.
    """
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        r"""@string{jmlr = {Journal of Machine Learning Research}}

@article{smith2024,
  author = {John Smith},
  title = {An Article},
  journal = jmlr,
  year = {2024},
}

@article{other2022,
  author = {Ada Other},
  title = {Another Article},
  journal = jmlr,
  year = {2022},
}
"""
    )

    execute_write_plan(
        str(bib_path),
        {
            "action": "update",
            "index": 0,
            "record": {"citekey": "smith2024", "title": "An Article"},
            "entry": {
                "entry_type": "article",
                "citekey": "smith2024",
                "fields": {
                    "author": "John Smith",
                    "title": "An Article",
                    "journal": "jmlr",
                    "year": "2024",
                    "abstract": "Newly added.",
                },
            },
            "changed_fields": ["abstract"],
        },
    )
    after = bib_path.read_text()

    assert "Newly added." in after
    # The untouched entry keeps its macro reference verbatim.
    assert "@article{other2022" in after
    other_block = after.split("@article{other2022", 1)[1]
    assert "journal = jmlr," in other_block
    assert "journal = {jmlr}" not in other_block


def test_update_severs_macros_only_in_the_entry_it_touches(tmp_path: Path) -> None:
    """Pins the known residue: the edited entry loses its macro references.

    The entry a write rebuilds is serialized from the internal record model,
    which carries no enclosings, so its own ``journal = jmlr`` comes back as the
    literal ``{jmlr}``. The blast radius is one entry — every other entry, and
    the ``@string`` definition itself, keep their references. Narrowing this
    further means teaching the touched-entry rebuild to keep the original field
    text for fields the plan did not change.

    (Through the service layer the same field comes back *expanded* rather than
    flattened, because ``tag_service`` sources its record from
    ``read_bib_file`` — bibtexparser's default stack, which resolves ``@string``
    — while ``update_bib_entry`` parses with a stack that does not. Unifying
    those two parses is a separate change.)
    """
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        r"""@string{jmlr = {Journal of Machine Learning Research}}

@article{smith2024,
  author = {John Smith},
  journal = jmlr,
  year = {2024},
}

@article{other2022,
  author = {Ada Other},
  journal = jmlr,
  year = {2022},
}
"""
    )

    update_bib_entry(
        str(bib_path),
        "smith2024",
        lambda entry, record: {
            "entry_type": entry["entry_type"],
            "citekey": entry["citekey"],
            "fields": {**entry["fields"], "keywords": "reading"},
        },
    )
    after = bib_path.read_text()

    # The @string definition survives, and so does the untouched entry's use of it.
    assert "@string{jmlr = {Journal of Machine Learning Research}}" in after
    other_block = after.split("@article{other2022", 1)[1]
    assert "journal = jmlr," in other_block

    # The touched entry's reference is severed — the residue this test pins.
    touched_block = after.split("@article{smith2024", 1)[1].split("@article{other2022", 1)[0]
    assert "journal = {jmlr}" in touched_block


def test_write_plan_update_still_encloses_rewritten_values(tmp_path: Path) -> None:
    """Reusing recorded enclosings must not let a plan write a value unenclosed.

    Preserved enclosings apply only to text read verbatim off disk. A field the
    plan rewrites is re-serialized from the record model, so it is brace-wrapped
    and passes through the injection guard — even when the field it replaces was
    a bare macro reference.
    """
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        r"""@string{jmlr = {Journal of Machine Learning Research}}

@article{smith2024,
  author = {John Smith},
  title = {An Article},
  journal = jmlr,
  year = {2024},
}
"""
    )

    execute_write_plan(
        str(bib_path),
        {
            "action": "update",
            "index": 0,
            "record": {"citekey": "smith2024", "title": "An Article"},
            "entry": {
                "entry_type": "article",
                "citekey": "smith2024",
                "fields": {
                    "author": "John Smith",
                    "title": "An Article",
                    "journal": "Some Journal}, title={pwned",
                    "year": "2024",
                },
            },
            "changed_fields": ["journal"],
        },
    )
    after = bib_path.read_text()

    # One entry, and no injected second field: the braces were neutralized.
    library = _parse_bib_library(after)
    assert len(library.entries) == 1
    assert library.entries[0].fields_dict["title"].value == "An Article"
    assert "pwned" not in library.entries[0].fields_dict["title"].value
