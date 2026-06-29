from pzi.add_service import ensure_citekey_for_write
from pzi.bibtex import (
    generate_citekey,
    generate_citekey_base,
    normalize_authors,
    repair_split_initials,
    resolve_citekey_collision,
)

# ── ensure_citekey_for_write: the documented stable-handle policy ──────────────
# (reuse-on-match and collision-suffix are also exercised in test_service.py;
# these pin the force_new and generate-when-missing branches.)


def test_ensure_citekey_force_new_suffixes_instead_of_reusing() -> None:
    # An exact match would normally reuse the existing key; force_new must
    # instead mint a distinct suffixed key so the duplicate is a new entry.
    record = {"doi": "10.1/a", "title": "Paper"}
    existing = [{"doi": "10.1/a", "citekey": "smith2024paper"}]

    result = ensure_citekey_for_write(record, existing, force_new=True)  # type: ignore[arg-type]

    assert result["citekey"] == "smith2024paper-2"


def test_ensure_citekey_generates_base_when_missing_and_no_match() -> None:
    record = {"title": "Graph Parsers", "authors": ["Smith, John"], "year": 2024}

    result = ensure_citekey_for_write(record, [])  # type: ignore[arg-type]

    # Author-year-title base key (exact slug rules covered by generate_citekey tests).
    assert result["citekey"].startswith("smith2024")


def test_ensure_citekey_keeps_unique_explicit_key() -> None:
    record = {"citekey": "mykey2024", "doi": "10.1/x"}

    result = ensure_citekey_for_write(record, [])  # type: ignore[arg-type]

    assert result["citekey"] == "mykey2024"


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
            "smith2024graph", {"smith2024graph", "smith2024graph-2"}
        )
        == "smith2024graph-3"
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
        == "smith2024graph-2"
    )


def test_generate_citekey_base_skips_ieee_split_initial_tokens() -> None:
    """Zotero IEEE translator may split 'N. E. Poborchaya' into ['N.', 'E.', 'Poborchaya']."""
    assert (
        generate_citekey_base(
            {
                "authors": ["N.", "E.", "Poborchaya", "E.", "O.", "Lobova"],
                "title": "Analysis of the Use of the Kalman Filter",
                "year": 2022,
            }
        )
        == "poborchaya2022analysis"
    )


def test_generate_citekey_base_uses_first_non_initial_when_only_initials_first() -> None:
    """When first entries are bare initials, pick next real name."""
    assert (
        generate_citekey_base(
            {
                "authors": ["N.", "E.", "Poborchaya"],
                "title": "Graph Parsers",
                "year": 2024,
            }
        )
        == "poborchaya2024graph"
    )


def test_generate_citekey_base_does_not_skip_short_real_names() -> None:
    """Short but real names like 'Ng' or 'Wu' should not be skipped."""
    assert (
        generate_citekey_base(
            {
                "authors": ["Ng, Andrew"],
                "title": "Learning Machines",
                "year": 2015,
            }
        )
        == "ng2015learning"
    )
    assert (
        generate_citekey_base(
            {
                "authors": ["Wu"],
                "title": "Deep Learning",
                "year": 2015,
            }
        )
        == "wu2015deep"
    )


def test_generate_citekey_base_handles_string_author_listed_as_characters() -> None:
    """Defense: list('N. E. Poborchaya') produces character list — don't pick 'o'."""
    # This simulates the upstream bug where `list(author_string)` is called.
    char_list = list("N. E. Poborchaya and E. O. Lobova")
    assert (
        generate_citekey_base(
            {
                "authors": char_list,
                "title": "Analysis of Kalman Filter",
                "year": 2022,
            }
        )
        == "n2022analysis"
    )


# ---------------------------------------------------------------------------
# normalize_authors
# ---------------------------------------------------------------------------


def test_normalize_authors_none() -> None:
    assert normalize_authors(None) == []


def test_normalize_authors_empty_list() -> None:
    assert normalize_authors([]) == []


def test_normalize_authors_list_of_strings() -> None:
    assert normalize_authors(["Jane Smith", "John Doe"]) == ["Jane Smith", "John Doe"]


def test_normalize_authors_filters_falsy() -> None:
    assert normalize_authors(["Jane Smith", "", None]) == ["Jane Smith"]


def test_normalize_authors_single_string() -> None:
    assert normalize_authors("Jane Smith") == ["Jane Smith"]


def test_normalize_authors_string_with_and() -> None:
    assert normalize_authors("Jane Smith and John Doe") == [
        "Jane Smith",
        "John Doe",
    ]


def test_normalize_authors_empty_string() -> None:
    assert normalize_authors("") == []


def test_normalize_authors_unknown_type() -> None:
    assert normalize_authors(42) == []


def test_normalize_authors_preserves_list_order() -> None:
    authors = ["N. E. Poborchaya", "E. O. Lobova"]
    assert normalize_authors(authors) == authors


# ---------------------------------------------------------------------------
# repair_split_initials
# ---------------------------------------------------------------------------


def test_repair_split_initials_ieee_zotero_case() -> None:
    result = repair_split_initials(
        ["N.", "E.", "Poborchaya", "E.", "O.", "Lobova"]
    )
    assert result == ["N. E. Poborchaya", "E. O. Lobova"]


def test_repair_split_initials_passes_through_normal() -> None:
    result = repair_split_initials(["Jane Smith", "John Doe"])
    assert result == ["Jane Smith", "John Doe"]


def test_repair_split_initials_no_bare_initials() -> None:
    result = repair_split_initials(["Poborchaya", "Lobova"])
    assert result == ["Poborchaya", "Lobova"]


def test_repair_split_initials_empty() -> None:
    assert repair_split_initials([]) == []
    assert repair_split_initials(None) == []


def test_repair_split_initials_single_initial_plus_name() -> None:
    result = repair_split_initials(["N.", "Smith"])
    assert result == ["N. Smith"]


def test_repair_split_initials_preserves_comma_format() -> None:
    result = repair_split_initials(["Smith, Jane"])
    assert result == ["Smith, Jane"]


# ---------------------------------------------------------------------------
# citekey with repaired split-initial authors
# ---------------------------------------------------------------------------


def test_generate_citekey_base_with_split_initial_authors() -> None:
    """IEEE/Zotero split initials should repair — citekey uses real last name."""
    repaired = repair_split_initials(
        ["N.", "E.", "Poborchaya", "E.", "O.", "Lobova"]
    )
    assert (
        generate_citekey_base(
            {
                "authors": repaired,
                "title": "Analysis of Kalman Filter",
                "year": 2022,
            }
        )
        == "poborchaya2022analysis"
    )
