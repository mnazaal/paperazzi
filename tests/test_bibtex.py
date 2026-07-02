import pytest

from pzi.bibtex import bibtex_entry_to_record, record_to_bibtex_entry


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
    with pytest.raises(ValueError, match="record.citekey"):
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
