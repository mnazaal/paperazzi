import pytest

from pzi.bibtex import (
    USER_OWNED_FIELDS,
    BibtexEntry,
    apply_record_to_entry,
    bibtex_entry_to_record,
    changed_fields,
    merge_projected_entry,
    parse_file_field,
    primary_pdf_path,
    record_to_bibtex_entry,
)
from pzi.errors import PziError


def test_record_to_bibtex_entry_maps_core_fields() -> None:
    entry = record_to_bibtex_entry(
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "authors": ["Smith, Jane", "Doe, John"],
            "year": 2024,
            "venue": "Journal of Parsing",
            "doi": "10.1145/3368089.3409741",
            "canonical_url": "https://example.com/paper",
            "local_pdf_path": "papers/smith2024graph.pdf",
            "tags": ["graphs", "ml"],
            "note": "Possibly similar to smith2023graph",
            "arxiv_id": "2401.12345",
        }
    )

    assert entry == {
        "entry_type": "article",
        "citekey": "smith2024graph",
        "fields": {
            "title": "Graph Parsers",
            "author": "Smith, Jane and Doe, John",
            "year": "2024",
            "journal": "Journal of Parsing",
            "doi": "10.1145/3368089.3409741",
            "url": "https://example.com/paper",
            "file": "papers/smith2024graph.pdf",
            "keywords": "graphs, ml",
            "note": "Possibly similar to smith2023graph",
            "eprint": "2401.12345",
            "archiveprefix": "arXiv",
        },
    }


def test_record_to_bibtex_entry_puts_venue_in_booktitle_for_proceedings() -> None:
    # A conference paper's venue is its `booktitle`; emitting `journal` for an
    # @inproceedings is bibliographically wrong and breaks styles that require
    # booktitle.
    entry = record_to_bibtex_entry(
        {"citekey": "smith2024graph", "title": "Graph Parsers", "venue": "GraphConf"},
        entry_type="inproceedings",
    )

    assert entry["fields"]["booktitle"] == "GraphConf"
    assert "journal" not in entry["fields"]


def test_apply_record_to_entry_keeps_a_proceedings_venue_in_booktitle() -> None:
    # The projection now emits `booktitle` for proceedings types, so the merge
    # must read the venue from either home — reading only `journal` would drop
    # the venue of every @inproceedings entry it touched.
    entry = {
        "entry_type": "inproceedings",
        "citekey": "smith2024graph",
        "fields": {"title": "Graph Parsers", "booktitle": "GraphConf"},
    }

    merged = apply_record_to_entry(
        entry, {"citekey": "smith2024graph", "title": "Graph Parsers", "venue": "GraphConf"}
    )

    assert merged["fields"]["booktitle"] == "GraphConf"
    assert "journal" not in merged["fields"]


def test_record_to_bibtex_entry_keeps_note_and_auxiliary_urls_in_own_fields() -> None:
    # Regression: note, pdf_url, and abstract_url used to be packed into one
    # `note` field with " | " delimiters and "PDF:"/"Abstract:" labels — a
    # note containing that same text would corrupt the parse. Each value now
    # gets its own BibTeX field.
    entry = record_to_bibtex_entry(
        {
            "citekey": "smith2024graph",
            "pdf_url": "https://example.com/paper.pdf",
            "abstract_url": "https://example.com/abstract",
            "note": "Imported from web | PDF: not-a-real-url",
        }
    )

    assert entry["fields"]["note"] == "Imported from web | PDF: not-a-real-url"
    assert entry["fields"]["pzi-pdf-url"] == "https://example.com/paper.pdf"
    assert entry["fields"]["pzi-abstract-url"] == "https://example.com/abstract"


def test_record_to_bibtex_entry_uses_source_url_when_canonical_missing() -> None:
    entry = record_to_bibtex_entry(
        {
            "citekey": "smith2024graph",
            "source_url": "https://example.com/source",
        }
    )

    assert entry["fields"]["url"] == "https://example.com/source"


def test_record_to_bibtex_entry_requires_citekey() -> None:
    """A `PziError` phrased for the user, not the internal attribute path.

    This is reachable from an entry hand-edited to `@article{,`, which parses
    fine and only fails here at write time — so "record.citekey must be a
    non-empty string" was shown to someone who has no record and no citekey,
    only a .bib file to repair.
    """
    with pytest.raises(PziError, match="no citekey"):
        record_to_bibtex_entry({})


def test_bibtex_entry_to_record_maps_fields_back() -> None:
    record = bibtex_entry_to_record(
        {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {
                "title": "Graph Parsers",
                "author": "Smith, Jane and Doe, John",
                "year": "2024",
                "journal": "Journal of Parsing",
                "doi": "10.1145/3368089.3409741",
                "url": "https://example.com/paper",
                "file": "papers/smith2024graph.pdf",
                "keywords": "graphs, ml",
                # Regression: a note containing "PDF:"-shaped text must survive
                # unmangled now that pdf_url has its own field.
                "note": "Possibly similar to smith2023graph | PDF: not-a-real-url",
                "pzi-pdf-url": "https://example.com/paper.pdf",
                "eprint": "2401.12345",
                "archiveprefix": "arXiv",
            },
        }
    )

    assert record == {
        "citekey": "smith2024graph",
        "title": "Graph Parsers",
        "authors": ["Smith, Jane", "Doe, John"],
        "year": 2024,
        "venue": "Journal of Parsing",
        "doi": "10.1145/3368089.3409741",
        "arxiv_id": "2401.12345",
        "canonical_url": "https://example.com/paper",
        "source_url": "https://example.com/paper",
        "pdf_url": "https://example.com/paper.pdf",
        "abstract_url": None,
        "tags": ["graphs", "ml"],
        "note": "Possibly similar to smith2023graph | PDF: not-a-real-url",
        "local_pdf_path": "papers/smith2024graph.pdf",
        "abstract": None,
    }


def test_bibtex_entry_to_record_ignores_non_numeric_year() -> None:
    record = bibtex_entry_to_record(
        {
            "entry_type": "article",
            "citekey": "smithxxxxgraph",
            "fields": {"year": "forthcoming"},
        }
    )

    assert record["year"] is None


def test_bibtex_entry_to_record_does_not_treat_biorxiv_eprint_as_arxiv() -> None:
    # Regression: any non-empty `eprint` used to be classified as an arXiv ID
    # regardless of `archiveprefix`, which fabricated an arxiv.org PDF URL
    # (via pdf_discovery's arxiv_id-based URL builder) for non-arXiv preprint
    # servers such as bioRxiv.
    record = bibtex_entry_to_record(
        {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {"eprint": "2024.01.01.123456", "archiveprefix": "bioRxiv"},
        }
    )

    assert record["arxiv_id"] is None


def test_bibtex_entry_to_record_uses_booktitle_as_fallback_venue() -> None:
    record = bibtex_entry_to_record(
        {
            "entry_type": "inproceedings",
            "citekey": "smith2024graph",
            "fields": {"booktitle": "Proceedings of GraphConf"},
        }
    )

    assert record["venue"] == "Proceedings of GraphConf"


def test_note_pdf_url_abstract_url_round_trip_is_byte_identical() -> None:
    # Regression: note used to be packed with " | " + "PDF:"/"Abstract:"
    # labels, so a user note containing that exact delimiter/label text
    # would be corrupted or misparsed on the next read. Each value now has
    # its own field, so the note round-trips byte-for-byte.
    tricky_note = "See also PDF: some other paper | Abstract: unrelated text"
    entry = record_to_bibtex_entry(
        {
            "citekey": "smith2024graph",
            "note": tricky_note,
            "pdf_url": "https://example.com/paper.pdf",
            "abstract_url": "https://example.com/abstract",
        }
    )

    assert entry["fields"]["note"] == tricky_note
    assert entry["fields"]["pzi-pdf-url"] == "https://example.com/paper.pdf"
    assert entry["fields"]["pzi-abstract-url"] == "https://example.com/abstract"

    record = bibtex_entry_to_record(entry)
    assert record["note"] == tricky_note
    assert record["pdf_url"] == "https://example.com/paper.pdf"
    assert record["abstract_url"] == "https://example.com/abstract"


# --- apply_record_to_entry: record-owned fields only ------------------------
# Regression guard for the 2026-07 audit's top finding: mutating an entry
# regenerated it from NormalizedRecord, silently deleting every BibTeX field
# the record model does not carry (volume, pages, publisher, editor, isbn, ...)
# and rewriting booktitle as journal.


def _conference_entry() -> dict:
    return {
        "entry_type": "inproceedings",
        "citekey": "smith2020graph",
        "fields": {
            "title": "Graph Networks",
            "author": "Smith, Jane and Doe, John",
            "booktitle": "Proceedings of NeurIPS",
            "year": "2020",
            "volume": "33",
            "pages": "1--12",
            "publisher": "Curran Associates",
            "editor": "Editor, Ed",
            "isbn": "978-1-234-56789-0",
        },
    }


def test_apply_record_preserves_unmodelled_fields() -> None:
    entry = _conference_entry()
    record = bibtex_entry_to_record(entry)
    record["tags"] = ["ml"]

    updated = apply_record_to_entry(entry, record)

    for key in ("volume", "pages", "publisher", "editor", "isbn"):
        assert updated["fields"][key] == entry["fields"][key], f"{key} was dropped"
    assert updated["fields"]["keywords"] == "ml"


def test_apply_record_keeps_booktitle_as_booktitle() -> None:
    entry = _conference_entry()
    record = bibtex_entry_to_record(entry)
    record["tags"] = ["ml"]

    updated = apply_record_to_entry(entry, record)

    assert updated["fields"]["booktitle"] == "Proceedings of NeurIPS"
    assert "journal" not in updated["fields"]
    assert updated["entry_type"] == "inproceedings"


def test_apply_record_writes_changed_venue_back_to_booktitle() -> None:
    entry = _conference_entry()
    record = bibtex_entry_to_record(entry)
    record["venue"] = "Proceedings of ICML"

    updated = apply_record_to_entry(entry, record)

    assert updated["fields"]["booktitle"] == "Proceedings of ICML"
    assert "journal" not in updated["fields"]


def test_apply_record_uses_journal_when_entry_has_no_booktitle() -> None:
    entry = {
        "entry_type": "article",
        "citekey": "smith2024",
        "fields": {"title": "T", "journal": "Old Journal", "volume": "7"},
    }
    record = bibtex_entry_to_record(entry)
    record["venue"] = "New Journal"

    updated = apply_record_to_entry(entry, record)

    assert updated["fields"]["journal"] == "New Journal"
    assert "booktitle" not in updated["fields"]
    assert updated["fields"]["volume"] == "7"


def test_apply_record_removes_cleared_owned_field() -> None:
    entry = {
        "entry_type": "article",
        "citekey": "smith2024",
        "fields": {"title": "T", "keywords": "ml, graphs", "volume": "7"},
    }
    record = bibtex_entry_to_record(entry)
    record["tags"] = []

    updated = apply_record_to_entry(entry, record)

    assert "keywords" not in updated["fields"]
    assert updated["fields"]["volume"] == "7"


def test_apply_record_keeps_non_arxiv_eprint() -> None:
    entry = {
        "entry_type": "article",
        "citekey": "jones2023",
        "fields": {
            "title": "Preprint",
            "eprint": "2023.01.01.522",
            "archiveprefix": "bioRxiv",
        },
    }
    record = bibtex_entry_to_record(entry)
    assert record["arxiv_id"] is None  # not arXiv, so the record never owned it

    updated = apply_record_to_entry(entry, record)

    assert updated["fields"]["eprint"] == "2023.01.01.522"
    assert updated["fields"]["archiveprefix"] == "bioRxiv"


# ── changed_fields / USER_OWNED_FIELDS ───────────────────────────


def test_changed_fields_reports_a_removed_field() -> None:
    """Over the union of both key sets, so removals are named.

    Iterating the *after* mapping alone can only report fields that survived.
    That is why promotion applied the removal of `arxiv_id`, the arXiv DOI and
    the preprint URLs, and then reported none of them as changed.
    """
    before = {"title": "T", "arxiv_id": "2301.07041", "doi": "10.48550/arXiv.2301.07041"}
    after = {"title": "T", "doi": "10.1145/1327452"}

    assert changed_fields(before, after) == ["arxiv_id", "doi"]


def test_changed_fields_ignores_untouched_fields() -> None:
    assert changed_fields({"title": "T", "year": 2020}, {"title": "T", "year": 2020}) == []


def test_changed_fields_reports_an_added_field() -> None:
    assert changed_fields({"title": "T"}, {"title": "T", "venue": "NeurIPS"}) == ["venue"]


def test_user_owned_fields_is_shared_by_update_and_promote() -> None:
    """One definition, or a field is user-owned for one command and not the other.

    Two copies meant the same library could lose a note or a tag depending on
    which command last touched the entry.
    """
    from pzi import promote_service, update_service

    assert update_service._USER_OWNED_UPDATE_FIELDS is USER_OWNED_FIELDS
    assert promote_service.USER_OWNED_FIELDS is USER_OWNED_FIELDS


def test_merge_projected_entry_handles_an_eprint_without_a_prefix() -> None:
    """A bare `eprint` must not raise.

    `record_to_bibtex_entry` always writes `eprint` and `archiveprefix`
    together, so indexing the pair worked for every projection it produces. But
    this function also accepts an already-merged entry as the projection — which
    is what a rebase passes — and a bioRxiv-style bare `eprint` has no prefix.
    That combination raised `KeyError: 'archiveprefix'`, which the update
    service reported to the user as `update failed: 'archiveprefix'`.
    """
    existing: BibtexEntry = {
        "entry_type": "article",
        "citekey": "a2020",
        "fields": {"title": "T", "eprint": "2401.12345", "archiveprefix": "arXiv"},
    }
    projected: BibtexEntry = {
        "entry_type": "article",
        "citekey": "a2020",
        "fields": {"title": "T", "eprint": "2020.01.01.123456"},
    }

    merged = merge_projected_entry(existing, projected)

    assert merged["fields"]["eprint"] == "2020.01.01.123456"
    # The projection is authoritative for the pair, so a prefix it omits goes.
    assert "archiveprefix" not in merged["fields"]


# ── parse_file_field ─────────────────────────────────────────────
#
# Cases transcribed from the producers' own sources and test suites:
# JabRef `FileFieldParserTest.java`, Zotero `BibTeX.js`, Better BibTeX
# `entry.ts`. pzi writes a bare path; everything else here is what a library
# imported from those tools actually contains.

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # pzi's own form.
        ("papers/x.pdf", ["papers/x.pdf"]),
        ("/abs/papers/x.pdf", ["/abs/papers/x.pdf"]),
        # Zotero export: description:path:mimetype.
        ("Full Text PDF:/abs/x.pdf:application/pdf", ["/abs/x.pdf"]),
        # JabRef with an empty description.
        (":wei2005ahp.pdf:PDF", ["wei2005ahp.pdf"]),
        ("Desc:File.PDF:PDF", ["File.PDF"]),
        # Better BibTeX default: bare paths, several joined by ';'.
        ("papers/a.pdf;papers/b.pdf", ["papers/a.pdf", "papers/b.pdf"]),
        # Multiple composites — the PDF and its HTML snapshot.
        (
            "Full Text PDF:papers/x.pdf:application/pdf;Snapshot:papers/x.html:text/html",
            ["papers/x.pdf", "papers/x.html"],
        ),
        # Escaped ':' and ';' inside the description.
        ("test" + chr(92) + ":" + chr(92) + ";:wei2005ahp.pdf:PDF", ["wei2005ahp.pdf"]),
        # Windows drive letter, escaped and bare.
        ("desc:C\\:\\\\test.pdf:PDF", ["C:\\test.pdf"]),
        # JabRef's degenerate forms all mean a bare link.
        ("file.pdf::", ["file.pdf"]),
        (":file.pdf", ["file.pdf"]),
        # A 4th component is JabRef's source URL; the path is still second.
        ("desc:file.pdf:PDF:http://example.com", ["file.pdf"]),
        # Nothing at all.
        ("", []),
        ("   ", []),
        (None, []),
    ],
)
def test_parse_file_field(value, expected) -> None:
    assert parse_file_field(value) == expected


def test_parse_file_field_keeps_a_colon_bearing_path_intact() -> None:
    """pzi permits ':' in a citekey, so it can write such a filename itself.

    A two-component value is ambiguous, and reading it as `desc:path` would
    silently drop the first half of a real filename. Zotero's own arity rule —
    1 or 3+ components — is what makes this safe.
    """
    assert parse_file_field("papers/smith:2020.pdf") == ["papers/smith:2020.pdf"]


def test_primary_pdf_path_prefers_the_pdf_over_a_snapshot() -> None:
    """Zotero does not sort attachments, so the PDF may not be first."""
    value = "Snapshot:papers/x.html:text/html;Full Text PDF:papers/x.pdf:application/pdf"
    assert primary_pdf_path(value) == "papers/x.pdf"
    assert primary_pdf_path("papers/only.html") == "papers/only.html"
    assert primary_pdf_path("") is None


def test_an_unmodellable_year_survives_a_projection_that_carries_one() -> None:
    """`2021a` is a disambiguator; the record model cannot hold it.

    `_parse_year` returns None for `2021a`, `in press` and
    `{\\noopsort{1997}}1997`, so the record reads as having no year. Merge then
    treated the incoming provider year as filling a gap and wrote it over the
    user's suffix — losing exactly the character that distinguishes 2021a from
    2021b. The existing guard only fired when the *projection* had no year,
    which is not this case.
    """
    from pzi.bibtex import merge_projected_entry

    entry = {"entry_type": "article", "citekey": "a2021a", "fields": {"year": "2021a"}}
    projection = {"entry_type": "article", "citekey": "a2021a", "fields": {"year": "2021"}}

    merged = merge_projected_entry(entry, projection)

    assert merged["fields"]["year"] == "2021a"


def test_a_year_the_record_can_model_is_still_writable() -> None:
    """The guard must not freeze ordinary years."""
    from pzi.bibtex import merge_projected_entry

    entry = {"entry_type": "article", "citekey": "a2020", "fields": {"year": "2020"}}
    projection = {"entry_type": "article", "citekey": "a2020", "fields": {"year": "2021"}}

    merged = merge_projected_entry(entry, projection)

    assert merged["fields"]["year"] == "2021"


def test_a_missing_year_is_still_filled_from_the_projection() -> None:
    """"Absent" and "present but unmodellable" are different."""
    from pzi.bibtex import merge_projected_entry

    entry = {"entry_type": "article", "citekey": "a", "fields": {"title": "T"}}
    projection = {"entry_type": "article", "citekey": "a", "fields": {"year": "2021"}}

    merged = merge_projected_entry(entry, projection)

    assert merged["fields"]["year"] == "2021"


def test_a_pdf_path_with_separators_round_trips_through_the_file_field() -> None:
    """A `:` in a citekey made its PDF permanently invisible.

    The citekey doubles as the PDF filename stem and `:` is legal in a citekey,
    so `smith:2024:graphs` produced `papers/smith:2024:graphs.pdf`. That was
    written into `file` verbatim, and `parse_file_field` reads three
    colon-separated components as Zotero's `desc:path:mime` — so the path read
    back as `2024`. `pzi entries` reported `has_pdf: false` forever, and
    `pdf retry` would download it again, with the real file sitting on disk.
    """
    from pzi.bibtex import parse_file_field, record_to_bibtex_entry

    for path in (
        "/lib/papers/smith:2024:graphs.pdf",
        "/lib/papers/a;b.pdf",
        "/lib/pa{pers}/x.pdf",
        r"C:\Users\me\paper.pdf",
        "/lib/papers/ordinary.pdf",
    ):
        entry = record_to_bibtex_entry({"citekey": "k", "local_pdf_path": path})

        assert parse_file_field(entry["fields"]["file"]) == [path], path
