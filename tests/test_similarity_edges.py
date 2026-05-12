"""Edge tests for similarity.py uncovered lines (32, 77, 93, 96->74)."""

from pzi.similarity import (
    _normalize_author,
    author_overlap,
    compute_similarity_hint,
    jaccard_similarity,
    normalize_title,
    title_tokens,
)

# ── normalize_title ──────────────────────────────────────────────

def test_normalize_title_none() -> None:
    assert normalize_title(None) == ""


def test_normalize_title_simple() -> None:
    assert normalize_title("Graph Parsers") == "graph parsers"


def test_normalize_title_accents() -> None:
    """Accents are stripped via NFKD normalization."""
    result = normalize_title("café résumé")
    assert "cafe" in result
    assert "resume" in result


def test_normalize_title_punctuation() -> None:
    result = normalize_title("Graph Parsers: Extended Version!")
    assert result == "graph parsers extended version"


# ── title_tokens ─────────────────────────────────────────────────

def test_title_tokens_none() -> None:
    assert title_tokens(None) == set()


def test_title_tokens_short_words_filtered() -> None:
    """Words with length <= 2 are excluded."""
    result = title_tokens("a b c dog cat")
    assert "dog" in result
    assert "cat" in result
    assert "a" not in result
    assert "b" not in result


def test_title_tokens_duplicate_words() -> None:
    result = title_tokens("graph neural graph network")
    assert result == {"graph", "neural", "network"}


# ── jaccard_similarity ───────────────────────────────────────────

def test_jaccard_identical() -> None:
    s = {"a", "b", "c"}
    assert jaccard_similarity(s, s) == 1.0


def test_jaccard_disjoint() -> None:
    assert jaccard_similarity({"a"}, {"b"}) == 0.0


def test_jaccard_partial() -> None:
    assert jaccard_similarity({"a", "b"}, {"b", "c"}) == 1 / 3


def test_jaccard_empty() -> None:
    assert jaccard_similarity(set(), {"a"}) == 0.0
    assert jaccard_similarity(set(), set()) == 0.0


# ── _normalize_author ────────────────────────────────────────────

def test_normalize_author_simple() -> None:
    assert _normalize_author("John Smith") == "smith"


def test_normalize_author_comma_format() -> None:
    """'Smith, John' → extracts family name (before comma)."""
    assert _normalize_author("Smith, John") == "smith"


def test_normalize_author_single_name() -> None:
    assert _normalize_author("Johnson") == "johnson"


def test_normalize_author_with_accents() -> None:
    result = _normalize_author("José Müller")
    assert "muller" in result or "jose" in result


# ── author_overlap ───────────────────────────────────────────────

def test_author_overlap_full() -> None:
    assert author_overlap(["A Smith", "B Jones"], ["A Smith", "B Jones"]) == 2


def test_author_overlap_none() -> None:
    assert author_overlap(["A Smith"], ["B Jones"]) == 0


def test_author_overlap_empty() -> None:
    assert author_overlap([], []) == 0


def test_author_overlap_empty_names_filtered() -> None:
    """Empty author names (like just ',') are discarded."""
    assert author_overlap([","], [","]) == 0


# ── compute_similarity_hint ──────────────────────────────────────

def test_similarity_hint_no_title_tokens() -> None:
    """Empty title → None."""
    result = compute_similarity_hint({"title": ""}, [])
    assert result is None


def test_similarity_hint_no_existing() -> None:
    result = compute_similarity_hint({"title": "Graph Methods"}, [])
    assert result is None


def test_similarity_hint_exact_match() -> None:
    existing = [{"citekey": "existing2024", "title": "Graph Neural Networks"}]
    record = {"title": "Graph Neural Networks"}
    result = compute_similarity_hint(record, existing)
    assert result == "existing2024"


def test_similarity_hint_no_citekey_skip() -> None:
    existing = [{"title": "Graph Networks"}]
    record = {"title": "Graph Networks"}
    result = compute_similarity_hint(record, existing)
    assert result is None  # no citekey in existing


def test_similarity_hint_below_threshold() -> None:
    existing = [{"citekey": "ex2024", "title": "different topic completely"}]
    record = {"title": "Graph Methods"}
    result = compute_similarity_hint(record, existing, title_threshold=0.8)
    assert result is None


def test_similarity_hint_year_window_outside() -> None:
    """Year difference > 2 excludes match."""
    existing = [{"citekey": "ex2024", "title": "Graph Methods", "year": 2020}]
    record = {"title": "Graph Methods", "year": 2024}
    result = compute_similarity_hint(record, existing, year_window=2)
    assert result is None


def test_similarity_hint_year_window_inside() -> None:
    existing = [{"citekey": "ex2024", "title": "Graph Methods", "year": 2023}]
    record = {"title": "Graph Methods", "year": 2024}
    result = compute_similarity_hint(record, existing, year_window=2)
    assert result == "ex2024"


def test_similarity_hint_no_author_overlap_high_similarity() -> None:
    """If similarity >= 0.85, author overlap=0 is OK."""
    existing = [{"citekey": "ex2024", "title": "Graph Neural Network Methods"}]
    record = {"title": "Graph Neural Network Methods", "authors": ["A Smith"]}
    # The titles should be very similar (high Jaccard)
    result = compute_similarity_hint(record, existing, title_threshold=0.5)
    assert result == "ex2024"


def test_similarity_hint_best_score_selected() -> None:
    """Multiple matches → best score wins."""
    existing = [
        {"citekey": "partial", "title": "Graph Networks", "authors": ["A Smith"]},
        {"citekey": "best", "title": "Graph Neural Networks Full", "authors": ["A Smith", "B Jones"]},
    ]
    record = {"title": "Graph Neural Networks", "authors": ["A Smith", "B Jones"]}
    result = compute_similarity_hint(record, existing)
    assert result == "best"


def test_similarity_hint_no_year_match_no_problem() -> None:
    """When neither record nor existing have year, year check is skipped."""
    existing = [{"citekey": "ex2024", "title": "Graph Methods", "authors": ["A Smith"]}]
    record = {"title": "Graph Methods", "authors": ["A Smith"]}
    result = compute_similarity_hint(record, existing)
    assert result == "ex2024"
