"""BibTeX backward-compatibility tests — formal guarantee that untouched
content (comments, @string, @preamble, unmodified entries) is preserved
byte-for-byte across write operations.

Note: bibtexparser v2 normalizes whitespace in @string definitions
(``@string{ jmlr`` → ``@string{jmlr``). The formal guarantee covers *content*
fidelity — comments, string *definitions*, ``@string`` macro *references*,
preamble blocks, and field values survive roundtrip without loss or
expansion.

Macro references survive a write, in the entries a plan touches as well as
those it does not: a rebuilt block keeps the source text of every field whose
value the plan did not change. Only the fields a write actually rewrites are
re-serialized from the record model.
"""

from pathlib import Path

import pytest

from pzi.bib_repository import (
    execute_write_plan,
    parse_bib_library,
    preview_write_plan,
    serialize_library,
    update_bib_entry,
)
from pzi.bib_serialize import describe_failed_blocks, detect_bib_layout, serialize_bibtex
from pzi.errors import PziError

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
    library = parse_bib_library(BIB_WITH_EXTRAS)
    result = serialize_library(library, layout=detect_bib_layout(BIB_WITH_EXTRAS))

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
    """Serialize → parse → serialize is idempotent.

    Each pass sniffs its own input, which is the stronger property: a layout
    read off the serializer's own output has to describe that output, or a
    second write would drift again.
    """
    library = parse_bib_library(BIB_WITH_EXTRAS)
    pass1 = serialize_library(library, layout=detect_bib_layout(BIB_WITH_EXTRAS))
    library2 = parse_bib_library(pass1)
    pass2 = serialize_library(library2, layout=detect_bib_layout(pass1))
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
    library = parse_bib_library(bib_path.read_text())
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
    after_library = parse_bib_library(after_content)
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
    after_library = parse_bib_library(result["new_source"])
    smith_fields = {f.key: f.value for f in after_library.entries[0].fields}
    # Smith's entry should still have original content (modulo v2 formatting)
    assert "John Smith" in smith_fields.get("author", "")
    assert "An Article" in smith_fields.get("title", "")
    # journal reference must be preserved, not expanded
    assert smith_fields.get("journal") == "jmlr"


def test_a_comment_flush_against_its_entry_stays_flush(tmp_path: Path) -> None:
    """A file may separate entries by a blank line and *not* separate comments.

    That is what a Better BibTeX export looks like: entries a blank line apart,
    each followed by a `% ==` quality report flush against the closing brace.
    `detect_bib_layout` sampled only entry→entry gaps (`_BLOCK_GAP_RE`'s
    lookahead admits `@` and nothing else) while the writer applied one
    separator at every boundary — so every comment gained a blank line before
    it. Measured on the real 22,232-entry library: **one `tag add` inserted
    18,650 blank lines**, one per comment block, against the 3,582 entry→entry
    gaps that were the only evidence being read.

    Lossless, and one-time rather than per-write, which is why nothing caught
    it: the content survives, the diff does not.
    """
    bib_path = tmp_path / "bbt-shaped.bib"
    source = (
        "@article{a2020,\n"
        "  title = {A},\n"
        "  year = {2020},\n"
        "}\n"
        "% == BibTeX quality report for a2020:\n"
        "% ? Title looks like it was stored in title-case in Zotero\n"
        "\n"
        "@article{b2021,\n"
        "  title = {B},\n"
        "  year = {2021},\n"
        "}\n"
        "% == BibTeX quality report for b2021:\n"
        "% ? unused Library catalog\n"
        "\n"
        "@article{c2022,\n"
        "  title = {C},\n"
        "  year = {2022},\n"
        "}\n"
    )
    bib_path.write_text(source)

    layout = detect_bib_layout(source)
    assert layout.block_separator == "\n", "entries are blank-line separated"
    assert layout.comment_separator == "", "comments are flush against the entry"

    # The whole file round-trips byte-for-byte, which is the property that
    # makes a one-entry edit a one-entry diff.
    assert serialize_library(parse_bib_library(source), layout=layout) == source

    # And through a real write: only the targeted entry moves.
    update_bib_entry(
        str(bib_path),
        "b2021",
        lambda entry, record: {
            **entry,
            "fields": {**entry["fields"], "keywords": "tagged"},
        },
    )
    after = bib_path.read_text()
    assert "keywords = {tagged}" in after
    assert after.count("\n\n") == source.count("\n\n"), (
        f"blank lines were added or removed:\n{after}"
    )
    assert "}\n% == BibTeX quality report for b2021:" in after, (
        f"a comment lost its flush position:\n{after}"
    )


def test_a_library_with_no_comments_is_written_exactly_as_before(
    tmp_path: Path,
) -> None:
    """No comment boundary to learn from means follow the entry convention.

    The comment separator is sniffed independently, so a file that offers no
    evidence about it must not acquire some other default — otherwise splitting
    the two separators would itself become a reformat for every library that
    has no comments at all.
    """
    source = (
        "@article{a2020,\n  title = {A},\n}\n\n@article{b2021,\n  title = {B},\n}\n"
    )
    layout = detect_bib_layout(source)
    assert layout.comment_separator == layout.block_separator == "\n"
    assert serialize_library(parse_bib_library(source), layout=layout) == source

    compact = "@article{a2020,\n  title = {A},\n}\n@article{b2021,\n  title = {B},\n}\n"
    compact_layout = detect_bib_layout(compact)
    assert compact_layout.comment_separator == compact_layout.block_separator == ""
    assert serialize_library(parse_bib_library(compact), layout=compact_layout) == compact


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
    after_library = parse_bib_library(after_content)
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

    library = parse_bib_library(bib_path.read_text())
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
    after_library = parse_bib_library(after)
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


def test_update_keeps_macro_references_in_the_entry_it_touches(tmp_path: Path) -> None:
    """Editing one field of an entry leaves that entry's *other* fields verbatim.

    The rebuilt block comes from the record model, which carries no enclosings,
    so writing it back wholesale replaced `journal = jmlr` with a literal. Only
    the field the write actually changed should be re-serialized.
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

    # The touched entry keeps its own reference, and gains only the new field.
    touched_block = after.split("@article{smith2024", 1)[1].split("@article{other2022", 1)[0]
    assert "journal = jmlr," in touched_block
    assert "journal = {jmlr}" not in touched_block
    assert "keywords = {reading}" in touched_block


def test_update_keeps_an_unresolved_concatenation_intact(tmp_path: Path) -> None:
    """`publisher = acm # { Press}` must not be brace-quoted into a literal.

    bibtexparser resolves a bare macro reference but leaves a concatenation as
    raw text, so it reaches the record model looking like an ordinary value.
    Writing it back enclosed would produce `{acm # { Press}}` — no longer a
    concatenation, and no longer referring to the macro.
    """
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        r"""@string{acm = {ACM}}

@article{smith2024,
  author = {John Smith},
  publisher = acm # { Press},
  year = {2024},
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

    assert "publisher = acm # { Press}," in after
    assert "publisher = {acm" not in after


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
    library = parse_bib_library(after)
    assert len(library.entries) == 1
    assert library.entries[0].fields_dict["title"].value == "An Article"
    assert "pwned" not in library.entries[0].fields_dict["title"].value


def test_carriage_return_is_stripped_from_a_field_value(tmp_path: Path) -> None:
    """`\\r` must not survive into a value.

    `_CONTROL_CHARS` says in its comment that it keeps `\\t` and `\\n`, but the
    range also admits `\\x0d`. On a CRLF file `_write_bib_text_atomic` then
    replaces every `\\n` with `\\r\\n`, doubling the carriage return into
    `\\r\\r\\n`. Values read off disk are safe — `read_text` translates
    newlines — so this only bites text injected from a metadata provider.
    `validate_bibtex_roundtrip` cannot catch it either: it runs on the LF text,
    before the newline conversion.
    """
    bib = tmp_path / "crlf.bib"
    bib.write_bytes(b"@article{ab2021,\r\n  title = {T}\r\n}\r\n")

    def _add_abstract(entry, record):
        return {**entry, "fields": {**entry["fields"], "abstract": "para1\r\npara2"}}

    update_bib_entry(str(bib), "ab2021", _add_abstract)

    raw = bib.read_bytes()
    assert b"\r\r" not in raw, raw
    assert raw.count(b"\r\n") == raw.count(b"\n"), f"mixed line endings: {raw!r}"


def test_duplicate_field_key_refusal_does_not_leak_parser_internals(
    tmp_path: Path,
) -> None:
    """The refusal is shown to the user, so it must not carry library jargon.

    bibtexparser appends "Note: The entry (containing duplicate) is available as
    `failed_block.entry`" to its error, and the whole thing was spliced into the
    message — telling a user to inspect a Python attribute in order to fix their
    .bib file.
    """
    bib = tmp_path / "dup.bib"
    bib.write_text(
        "@article{a2020,\n  title = {One},\n  title = {Two}\n}\n"
    )

    with pytest.raises(PziError) as excinfo:
        update_bib_entry(str(bib), "a2020", lambda entry, record: entry)

    message = excinfo.value.message
    assert "duplicate" in message.lower()
    assert "failed_block" not in message, message
    assert "Note:" not in message, message


def test_an_entry_with_no_citekey_is_reported_on_read(tmp_path: Path) -> None:
    """`@article{,` parses fine, so nothing flagged it.

    bibtexparser accepts a keyless entry: it is not a failed block and not a
    mangled field, so `pzi entries` listed it with a blank citekey and exit 0.
    The cost surfaced later and elsewhere — every write to the library, even one
    touching a different entry, was refused by the serializer with "refusing to
    write an entry with an empty citekey", naming no file, no line, no entry.
    """
    src = "@article{,\n  title = {No Key}\n}\n\n@article{good2020,\n  title = {Fine}\n}\n"
    library = parse_bib_library(src)

    warnings = describe_failed_blocks(library)

    assert any("no citekey" in w for w in warnings), warnings


def test_a_write_names_the_entry_with_no_citekey(tmp_path: Path) -> None:
    """The refusal must say what to fix and where."""
    bib = tmp_path / "nokey.bib"
    bib.write_text(
        "@article{,\n  title = {No Key}\n}\n\n@article{good2020,\n  title = {Fine}\n}\n"
    )

    with pytest.raises(PziError) as excinfo:
        update_bib_entry(str(bib), "good2020", lambda entry, record: entry)

    message = excinfo.value.message
    assert "no citekey" in message, message
    assert "line 1" in message, message
    assert bib.read_text().startswith("@article{,"), "the file was rewritten"


def test_field_keys_differing_only_in_case_are_reported_on_read() -> None:
    """`Title` and `title` in one entry: one of them is silently deleted.

    Field keys are case-folded at the parse boundary, which is correct and is
    what makes a JabRef-style `Author =` readable. But the fold happens into a
    `dict`, so an entry carrying *both* spellings keeps only the last, and
    nothing notices: bibtexparser flags only byte-identical duplicate keys, and
    the round-trip gate compares the already-collapsed entry against itself. The
    first write that touches the entry commits the deletion.
    """
    src = (
        "@article{casedup2020,\n"
        "  Title = {The capitalised title the user wrote},\n"
        "  title = {a stray lowercase duplicate},\n"
        "  author = {A, B},\n"
        "}\n"
    )
    library = parse_bib_library(src)

    warnings = describe_failed_blocks(library)

    assert any("title" in w for w in warnings), warnings
    assert any("casedup2020" in w for w in warnings), warnings


def test_a_write_refuses_an_entry_whose_field_keys_collide_on_case(
    tmp_path: Path,
) -> None:
    """The deletion is committed by the write, so the write is where it stops."""
    bib = tmp_path / "casedup.bib"
    original = (
        "@article{casedup2020,\n"
        "  Title = {The capitalised title the user wrote},\n"
        "  title = {a stray lowercase duplicate},\n"
        "}\n\n"
        "@article{good2020,\n  title = {Fine}\n}\n"
    )
    bib.write_text(original)

    with pytest.raises(PziError) as excinfo:
        update_bib_entry(str(bib), "good2020", lambda entry, record: entry)

    assert "casedup2020" in excinfo.value.message, excinfo.value.message
    assert bib.read_text() == original, "the file was rewritten"


def test_a_batch_preview_runs_the_gates_the_batch_write_runs(tmp_path: Path) -> None:
    """`preview_batch_write` was the fifth path validating a different amount.

    `batch_write_session` runs `check_consistency` and
    `validate_bibtex_roundtrip` even when `write=False`, with a comment
    claiming four paths now validate the same thing. `preview_batch_write` —
    what `update --promote --dry-run` uses — ran neither, so a batch whose
    state every real write refuses previewed as a clean diff.
    """
    from pzi.bib_repository import (
        BatchWriteSession,
        batch_write_session,
        preview_batch_write,
    )

    bib = tmp_path / "batch.bib"
    bib.write_text("@article{target2020,\n  title = {Target}\n}\n")

    def desync(session: BatchWriteSession) -> None:
        # An entry with no matching record: the exact state `check_consistency`
        # exists to catch, since it would let a later record dedup against the
        # wrong entry.
        session.entries.append(
            {"entry_type": "article", "citekey": "ghost2020", "fields": {}}
        )
        session.touched.add(len(session.entries) - 1)

    with pytest.raises(RuntimeError):
        with batch_write_session(str(bib), write=False) as session:
            desync(session)

    with pytest.raises(RuntimeError):
        preview_batch_write(str(bib), desync)
