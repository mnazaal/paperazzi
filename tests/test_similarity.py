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


def test_compute_similarity_hint_rejects_title_match_with_no_shared_authors() -> None:
    """A merely-similar title needs author support; 0.85 is the bar without it."""
    existing = [
        {
            "citekey": "nguyen2024estimation",
            "title": "Graph Neural Networks for Molecular Property Estimation",
            "authors": ["Nguyen, Linh"],
            "year": 2024,
        },
    ]
    hint = compute_similarity_hint(
        {
            "citekey": "smith2024prediction",
            "title": "Graph Neural Networks for Molecular Property Prediction",
            "authors": ["Smith, Jane"],
            "year": 2024,
        },
        existing,
    )
    # Jaccard is 0.75 here — over the 0.6 title threshold, under the 0.85 the
    # scorer demands when no author is shared.
    assert hint is None


def test_compute_similarity_hint_accepts_strong_title_match_without_authors() -> None:
    """Above 0.85, title similarity alone carries the match."""
    existing = [
        {
            "citekey": "nguyen2024revisited",
            "title": "Graph Neural Networks for Molecular Property Prediction Revisited",
            "authors": ["Nguyen, Linh"],
            "year": 2024,
        },
    ]
    hint = compute_similarity_hint(
        {
            "citekey": "smith2024prediction",
            "title": "Graph Neural Networks for Molecular Property Prediction",
            "authors": ["Smith, Jane"],
            "year": 2024,
        },
        existing,
    )
    assert hint == "nguyen2024revisited"


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


def test_compute_similarity_hint_coerces_string_year() -> None:
    # Regression: browser-extension fallback metadata supplies year as a
    # string (embedded_year); a stray string reaching the year-window
    # comparison must not raise TypeError, and should still compare correctly.
    existing = [
        {
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "authors": ["Smith, Jane"],
            "year": 2023,
        }
    ]
    hint = compute_similarity_hint(
        {
            "title": "Graph Parsers for Structured Search",
            "authors": ["Smith, Jane"],
            "year": "2024",
        },
        existing,
    )
    assert hint == "smith2024graph"


def test_compute_similarity_hint_string_year_outside_window_excluded() -> None:
    existing = [
        {
            "citekey": "smith2015graph",
            "title": "Graph Parsers",
            "authors": ["Smith, Jane"],
            "year": 2015,
        }
    ]
    hint = compute_similarity_hint(
        {"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": "2024"},
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


def test_best_fuzzy_matches_agrees_with_scanning_one_record_at_a_time() -> None:
    """The fast path must be a pure speed-up, not a different answer.

    Randomized because the interesting cases are the ones nobody writes by
    hand: near-threshold Jaccard, ties broken by corpus order, titles of very
    different lengths, and records whose only shared token is a common word.
    """
    import random

    from pzi.similarity import best_fuzzy_matches, compute_similarity_hint

    words = (
        "graph neural network learning deep transformer attention model data "
        "efficient scalable robust sparse bayesian causal representation"
    ).split()

    for seed in range(25):
        rng = random.Random(seed)
        records = [
            {
                "citekey": f"k{i}",
                "title": " ".join(rng.sample(words, rng.randint(2, 8))),
                "authors": [f"Author{rng.randint(0, 6)}, A" for _ in range(rng.randint(0, 3))],
                "year": rng.choice([None, 2018 + rng.randint(0, 8)]),
            }
            for i in range(60)
        ]

        expected = {}
        for i, record in enumerate(records):
            others = [other for j, other in enumerate(records) if j != i]
            hint = compute_similarity_hint(record, others)
            if hint is not None:
                expected[i] = hint

        assert best_fuzzy_matches(records, positions=range(len(records))) == expected, seed


def test_best_fuzzy_matches_only_scores_the_positions_it_was_given() -> None:
    from pzi.similarity import best_fuzzy_matches

    records = [
        {"citekey": "a", "title": "Deep Residual Learning", "authors": ["He, K"], "year": 2016},
        {"citekey": "b", "title": "Deep Residual Learning", "authors": ["He, K"], "year": 2016},
        {"citekey": "c", "title": "Attention Is All You Need", "authors": ["V, A"], "year": 2017},
    ]

    # `b` is still available as a *candidate* for `a`, it is just not scored.
    assert best_fuzzy_matches(records, positions=[0]) == {0: "b"}


def test_best_fuzzy_matches_ignores_a_record_with_no_usable_title() -> None:
    from pzi.similarity import best_fuzzy_matches

    records = [
        {"citekey": "a", "title": "", "authors": ["He, K"], "year": 2016},
        {"citekey": "b", "title": "Deep Residual Learning", "authors": ["He, K"], "year": 2016},
    ]

    assert best_fuzzy_matches(records, positions=range(2)) == {}


def test_a_placeholder_doi_is_not_an_identity() -> None:
    """`doi = {n/a}` is the absence of a DOI, not a shared one.

    The canonical form fell back to a case-folded strip whenever the value was
    not DOI-shaped, so every entry carrying the same placeholder shared one
    identity — and `library dedupe` reported them as exact duplicates of each other.
    """
    from pzi.similarity import build_identity_index, find_exact_match

    records = [
        {"citekey": "a", "title": "Alpha", "doi": "n/a"},
        {"citekey": "b", "title": "Beta", "doi": "N/A"},
        {"citekey": "c", "title": "Gamma", "doi": "-"},
        {"citekey": "d", "title": "Delta", "doi": "TODO"},
    ]

    index = build_identity_index(records)  # type: ignore[arg-type]
    assert index == {}
    assert find_exact_match(records[0], records[1:]) is None  # type: ignore[arg-type]


def test_a_real_doi_is_still_an_identity_however_it_was_written() -> None:
    from pzi.similarity import find_exact_match

    stored = [{"citekey": "a", "title": "Alpha", "doi": "10.1145/abc"}]
    for spelling in ("10.1145/ABC", "10.1145/abc/", "https://doi.org/10.1145/abc"):
        incoming = {"citekey": "b", "title": "Alpha", "doi": spelling}
        assert find_exact_match(incoming, stored) == 0, spelling  # type: ignore[arg-type]


def test_a_shared_landing_url_does_not_match_two_different_papers() -> None:
    """A URL is a location, not an identifier.

    `canonical_doi` drops placeholder DOIs because "a placeholder is the
    absence of a DOI, not a shared one" — every entry carrying the same filler
    collapsed into one identity. `canonical_url` was taken verbatim and never
    got that reasoning, so two entries sharing a publisher landing page became
    one identity and `plan_bib_write` turned the insert into an *update*: the
    existing paper took the incoming one's title, DOI and abstract.
    """
    from pzi.similarity import find_exact_match

    existing = [
        {
            "citekey": "alpha2019",
            "title": "A Study of Alpha Particles",
            "canonical_url": "https://www.sciencedirect.com/journal/foo",
        }
    ]
    incoming = {
        "citekey": "beta2020",
        "title": "A Completely Different Paper About Beta Particles",
        "doi": "10.1000/beta",
        "canonical_url": "https://www.sciencedirect.com/journal/foo",
    }

    assert find_exact_match(incoming, existing) is None


def test_a_shared_url_still_matches_the_same_paper() -> None:
    """Re-capturing one page must still find the entry it already made."""
    from pzi.similarity import find_exact_match

    existing = [
        {
            "citekey": "alpha2019",
            "title": "A Study of Alpha Particles",
            "canonical_url": "https://example.org/papers/alpha",
        }
    ]
    incoming = {
        "title": "A Study of Alpha Particles",
        "canonical_url": "https://example.org/papers/alpha",
    }

    assert find_exact_match(incoming, existing) == 0


def test_url_spellings_of_one_page_are_one_identity() -> None:
    """Three spellings of a URL used to be three identities."""
    from pzi.similarity import find_exact_match

    existing = [
        {"citekey": "a2019", "title": "Alpha", "canonical_url": "https://example.org/p"}
    ]
    incoming = {"title": "Alpha", "canonical_url": "https://Example.org/p/"}

    assert find_exact_match(incoming, existing) == 0


def test_a_doi_match_does_not_need_the_title_to_agree() -> None:
    """Corroboration applies to URLs alone; a DOI is a real identifier."""
    from pzi.similarity import find_exact_match

    existing = [{"citekey": "a2019", "title": "Old Provisional Title", "doi": "10.1000/xyz"}]
    incoming = {"title": "The Final Published Title", "doi": "10.1000/xyz"}

    assert find_exact_match(incoming, existing) == 0
