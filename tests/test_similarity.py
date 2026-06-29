from pzi.similarity import (
    author_overlap,
    author_surnames,
    authors_swapped,
    classify_given_pair,
    compute_similarity_hint,
    has_truncation_sentinel,
    is_truncation_sentinel,
    jaccard_similarity,
    levenshtein_within_1,
    normalize_title,
    title_tokens,
)


def test_levenshtein_within_1() -> None:
    assert levenshtein_within_1("privacy", "privacy") is True       # equal
    assert levenshtein_within_1("privacy", "privacys") is True      # insertion
    assert levenshtein_within_1("residual", "resicual") is True     # substitution
    assert levenshtein_within_1("xabc", "abc") is True              # leading insertion
    assert levenshtein_within_1("abc", "abxyc") is False            # distance 2
    assert levenshtein_within_1("graph", "trees") is False          # far apart


def test_truncation_sentinels() -> None:
    assert is_truncation_sentinel("others") is True
    assert is_truncation_sentinel("et al.") is True
    assert is_truncation_sentinel("Smith, Jane") is False
    assert has_truncation_sentinel(["Smith, J", "others"]) is True
    assert has_truncation_sentinel(["Smith, J", "Doe, A"]) is False


def test_author_overlap_decodes_html_entities() -> None:
    # DBLP emits &apos; / &amp;; these must match their decoded forms.
    assert author_overlap(["d&apos;Amore, Luca"], ["d'Amore, Luca"]) == 1
    assert author_overlap(["Smith &amp; Co"], ["Co, Ann"]) == 1


def test_author_surnames_order_and_forms() -> None:
    assert author_surnames(["Smith, Jane", "John Doe"]) == ["smith", "doe"]
    assert author_surnames(["", "  "]) == []


def test_authors_swapped_detects_reorder() -> None:
    assert authors_swapped(["Young, Z", "Doe, A"], ["Doe, A", "Young, Z"]) is True


def test_authors_swapped_false_for_same_order() -> None:
    assert authors_swapped(["Smith, J", "Doe, A"], ["Smith, J", "Doe, A"]) is False


def test_authors_swapped_escapes_alphabetized_candidate() -> None:
    # An alphabetized source (signalled by the caller) is a record artifact.
    assert (
        authors_swapped(
            ["Young, Z", "Adams, A"], ["Adams, A", "Young, Z"], candidate_alphabetized=True
        )
        is False
    )


def test_authors_swapped_false_for_different_sets() -> None:
    assert authors_swapped(["Smith, J", "Doe, A"], ["Smith, J", "Roe, B"]) is False


def test_classify_given_pair() -> None:
    assert classify_given_pair("John", "John") == "match"
    assert classify_given_pair("J", "John") == "variant"        # initial
    assert classify_given_pair("J.", "John") == "variant"
    assert classify_given_pair("Jon", "Jonathan") == "variant"  # prefix
    assert classify_given_pair("Shunyu", "Denny") == "substitution"
    assert classify_given_pair("", "John") == "variant"         # missing data


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
