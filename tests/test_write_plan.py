from pzi.bib_repository import plan_bib_write


def test_plan_bib_write_returns_insert_for_new_record() -> None:
    plan = plan_bib_write(
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
        },
        [],
    )

    assert plan == {
        "action": "insert",
        "index": None,
        "record": {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
        },
        "entry": {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {
                "title": "Graph Parsers",
                "doi": "10.1/foo",
            },
        },
        "changed_fields": ["citekey", "doi", "title"],
    }


def test_plan_bib_write_returns_update_for_exact_match() -> None:
    plan = plan_bib_write(
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "doi": "10.1/foo",
            "tags": ["graphs"],
        },
        [
            {
                "citekey": "smith2024graph",
                "title": "Graph Parsers",
                "doi": "10.1/foo",
                "local_pdf_path": "papers/smith2024graph.pdf",
            }
        ],
    )

    assert plan == {
        "action": "update",
        "index": 0,
        "record": {
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "doi": "10.1/foo",
            "local_pdf_path": "papers/smith2024graph.pdf",
            "tags": ["graphs"],
        },
        "entry": {
            "entry_type": "article",
            "citekey": "smith2024graph",
            "fields": {
                "title": "Graph Parsers for Structured Search",
                "doi": "10.1/foo",
                "file": "papers/smith2024graph.pdf",
                "keywords": "graphs",
            },
        },
        "changed_fields": ["tags", "title"],
    }


def test_plan_bib_write_preserves_existing_user_owned_fields_on_update() -> None:
    plan = plan_bib_write(
        {
            "citekey": "ignored-new-key",
            "doi": "10.1/foo",
            "canonical_url": "https://example.com/paper",
        },
        [
            {
                "citekey": "smith2024graph",
                "doi": "10.1/foo",
                "local_pdf_path": "papers/smith2024graph.pdf",
            }
        ],
    )

    assert plan["action"] == "update"
    assert plan["record"].get("citekey") == "smith2024graph"
    assert plan["record"].get("local_pdf_path") == "papers/smith2024graph.pdf"
    assert plan["changed_fields"] == ["canonical_url"]


def test_plan_bib_write_adopts_incoming_local_pdf_path_when_missing() -> None:
    plan = plan_bib_write(
        {
            "citekey": "ignored-new-key",
            "doi": "10.1/foo",
            "local_pdf_path": "papers/smith2024graph.pdf",
        },
        [
            {
                "citekey": "smith2024graph",
                "doi": "10.1/foo",
            }
        ],
    )

    assert plan["action"] == "update"
    assert plan["record"].get("citekey") == "smith2024graph"
    assert plan["record"].get("local_pdf_path") == "papers/smith2024graph.pdf"
    assert plan["changed_fields"] == ["local_pdf_path"]


def test_plan_bib_write_uses_requested_entry_type() -> None:
    plan = plan_bib_write(
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
            "venue": "GraphConf",
        },
        [],
        entry_type="inproceedings",
    )

    assert plan["entry"]["entry_type"] == "inproceedings"
