from pzi.citekeys import (
    generate_citekey,
    generate_citekey_base,
    resolve_citekey_collision,
)


def test_generate_citekey_base_from_author_year_title() -> None:
    assert (
        generate_citekey_base(
            {
                "authors": ["Smith, Jane", "Doe, John"],
                "title": "Graph Parsers for Structured Search",
                "year": 2024,
            }
        )
        == "smith2024graph"
    )


def test_generate_citekey_base_uses_last_token_for_uncommaed_author() -> None:
    assert (
        generate_citekey_base(
            {
                "authors": ["Jane Smith"],
                "title": "The Analysis of Systems",
                "year": 2021,
            }
        )
        == "smith2021analysis"
    )


def test_generate_citekey_base_skips_stopword_only_title_prefix() -> None:
    assert (
        generate_citekey_base(
            {
                "authors": ["Ng, Andrew"],
                "title": "The Structure of Learning",
                "year": 2019,
            }
        )
        == "ng2019structure"
    )


def test_generate_citekey_base_falls_back_when_metadata_missing() -> None:
    assert (
        generate_citekey_base(
            {
                "authors": [],
                "title": None,
                "year": None,
            }
        )
        == "unknownxxxxuntitled"
    )


def test_generate_citekey_base_transliterates_unicode() -> None:
    assert (
        generate_citekey_base(
            {
                "authors": ["García Márquez, Ana"],
                "title": "Café Graph Learning",
                "year": 2022,
            }
        )
        == "garciamarquez2022cafe"
    )


def test_resolve_citekey_collision_returns_base_when_available() -> None:
    assert (
        resolve_citekey_collision("smith2024graph", {"other2024paper"})
        == "smith2024graph"
    )


def test_resolve_citekey_collision_appends_numeric_suffix() -> None:
    assert (
        resolve_citekey_collision(
            "smith2024graph", {"smith2024graph", "smith2024graph2"}
        )
        == "smith2024graph3"
    )


def test_generate_citekey_combines_base_and_collision_resolution() -> None:
    assert (
        generate_citekey(
            {
                "authors": ["Smith, Jane"],
                "title": "Graph Parsers",
                "year": 2024,
            },
            {"smith2024graph"},
        )
        == "smith2024graph2"
    )
