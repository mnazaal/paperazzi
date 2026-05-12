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


def test_record_to_bibtex_entry_combines_note_and_auxiliary_urls() -> None:
    entry = record_to_bibtex_entry(
        {
            "citekey": "smith2024graph",
            "pdf_url": "https://example.com/paper.pdf",
            "abstract_url": "https://example.com/abstract",
            "note": "Imported from web",
        }
    )

    assert entry["fields"]["note"] == (
        "Imported from web | PDF: https://example.com/paper.pdf | "
        "Abstract: https://example.com/abstract"
    )


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
                "note": "Possibly similar to smith2023graph | PDF: https://example.com/paper.pdf",
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
        "tags": ["graphs", "ml"],
        "note": "Possibly similar to smith2023graph",
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


def test_bibtex_entry_to_record_uses_booktitle_as_fallback_venue() -> None:
    record = bibtex_entry_to_record(
        {
            "entry_type": "inproceedings",
            "citekey": "smith2024graph",
            "fields": {"booktitle": "Proceedings of GraphConf"},
        }
    )

    assert record["venue"] == "Proceedings of GraphConf"
