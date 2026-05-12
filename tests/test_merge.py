from pzi.merge import merge_entries


def test_merge_entries_unions_tags_and_preserves_existing_pdf_path() -> None:
    decision = merge_entries(
        {
            "tags": ["graphs", "ml"],
            "local_pdf_path": "papers/smith2024graph.pdf",
            "title": "Graph Parsers",
        },
        {
            "tags": ["ml", "nlp"],
            "local_pdf_path": "papers/new.pdf",
            "title": "Graph Parsers for Structured Search",
        },
    )

    assert decision == {
        "merged": {
            "tags": ["graphs", "ml", "nlp"],
            "local_pdf_path": "papers/smith2024graph.pdf",
            "title": "Graph Parsers for Structured Search",
        },
        "changed_fields": ["tags", "title"],
    }


def test_merge_entries_prefers_longer_author_list() -> None:
    decision = merge_entries(
        {
            "authors": ["Smith, Jane"],
        },
        {
            "authors": ["Smith, Jane", "Doe, John"],
        },
    )

    assert decision == {
        "merged": {
            "authors": ["Smith, Jane", "Doe, John"],
        },
        "changed_fields": ["authors"],
    }


def test_merge_entries_keeps_existing_year_when_present() -> None:
    decision = merge_entries(
        {
            "year": 2024,
        },
        {
            "year": 2023,
        },
    )

    assert decision == {
        "merged": {"year": 2024},
        "changed_fields": [],
    }


def test_merge_entries_fills_missing_identifier_fields() -> None:
    decision = merge_entries(
        {
            "title": "Graph Parsers",
            "doi": None,
        },
        {
            "title": "Graph Parsers",
            "doi": "10.1145/3368089.3409741",
            "canonical_url": "https://example.com/paper",
        },
    )

    assert decision == {
        "merged": {
            "title": "Graph Parsers",
            "doi": "10.1145/3368089.3409741",
            "canonical_url": "https://example.com/paper",
        },
        "changed_fields": ["canonical_url", "doi"],
    }


def test_merge_entries_preserves_existing_citekey() -> None:
    decision = merge_entries(
        {
            "citekey": "smith2024graph",
        },
        {
            "citekey": "smith2024graph2",
        },
    )

    assert decision == {
        "merged": {"citekey": "smith2024graph"},
        "changed_fields": [],
    }


def test_merge_entries_uses_incoming_title_when_existing_missing() -> None:
    decision = merge_entries(
        {
            "title": None,
        },
        {
            "title": "Graph Parsers",
        },
    )

    assert decision == {
        "merged": {"title": "Graph Parsers"},
        "changed_fields": ["title"],
    }
