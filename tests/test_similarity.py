from pzi.similarity import (
    author_overlap,
    compute_similarity_hint,
    jaccard_similarity,
    normalize_title,
    title_tokens,
)


def test_normalize_title_strips_punctuation_and_lowercases() -> None:
    assert normalize_title("Graph Parsers: A Survey") == "graph parsers a survey"


def test_title_tokens_drops_short_tokens() -> None:
    assert title_tokens("A survey of graph parsers") == {
        "survey",
        "graph",
        "parsers",
    }


def test_jaccard_similarity_on_overlapping_sets() -> None:
    similarity = jaccard_similarity({"graph", "parsers"}, {"graph", "parsers", "ml"})
    assert abs(similarity - 2 / 3) < 1e-9


def test_author_overlap_by_family_name() -> None:
    assert author_overlap(["Smith, Jane", "Doe, John"], ["Jane Smith"]) == 1


def test_compute_similarity_hint_picks_best_match() -> None:
    existing = [
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "authors": ["Smith, Jane"],
            "year": 2024,
        },
        {
            "citekey": "jones2024vision",
            "title": "Vision Transformers at Scale",
            "authors": ["Jones, Mary"],
            "year": 2024,
        },
    ]
    hint = compute_similarity_hint(
        {
            "citekey": "smith2025graphproceedings",
            "title": "Graph Parsers for Structured Search: Extended",
            "authors": ["Smith, Jane"],
            "year": 2025,
        },
        existing,
    )
    assert hint == "smith2024graph"


def test_compute_similarity_hint_respects_year_window() -> None:
    existing = [
        {
            "citekey": "smith2015graph",
            "title": "Graph Parsers",
            "authors": ["Smith, Jane"],
            "year": 2015,
        }
    ]
    hint = compute_similarity_hint(
        {"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024},
        existing,
    )
    assert hint is None


def test_compute_similarity_hint_none_when_no_title() -> None:
    existing = [
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "authors": ["Smith, Jane"],
            "year": 2024,
        }
    ]
    assert compute_similarity_hint({"title": None}, existing) is None
