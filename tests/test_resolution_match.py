"""Tests for src/pzi/resolution_match.py."""

from pzi.resolution_match import score_match


def _rec(title, authors, **extra):
    return {"title": title, "authors": authors, **extra}


def test_perfect_match_scores_high_no_flags() -> None:
    entry = _rec("Attention Is All You Need", ["Vaswani, Ashish", "Shazeer, Noam"])
    cand = _rec("Attention Is All You Need", ["Vaswani, Ashish", "Shazeer, Noam"])
    result = score_match(entry, cand)
    assert result["score"] >= 95
    assert result["flags"] == []
    assert result["title_similarity"] == 100
    assert result["author_similarity"] == 100


def test_chimeric_high_title_wrong_authors() -> None:
    entry = _rec("Deep Residual Learning for Image Recognition", ["He, Kaiming"])
    cand = _rec(
        "Deep Residual Learning for Image Recognition", ["Nobody, Random", "Else, Someone"]
    )
    result = score_match(entry, cand)
    assert "chimeric" in result["flags"]
    assert "author_mismatch" in result["flags"]
    assert result["score"] < 70


def test_title_mismatch_penalized() -> None:
    entry = _rec("Graph Neural Networks for Molecules", ["Smith, Jane"])
    cand = _rec("A Completely Unrelated Paper About Birds", ["Smith, Jane"])
    result = score_match(entry, cand)
    assert "title_mismatch" in result["flags"]
    assert result["score"] < 50


def test_venue_mismatch_flagged() -> None:
    entry = _rec("Some Title Here", ["Smith, Jane"], venue="Nature")
    cand = _rec("Some Title Here", ["Smith, Jane"], venue="Science")
    result = score_match(entry, cand)
    assert "venue_mismatch" in result["flags"]


def test_multi_source_bonus_applied() -> None:
    entry = _rec("Some Title Here", ["Smith, Jane"])
    cand = _rec("Some Title Here", ["Smith, Jane"])
    one = score_match(entry, cand, author_sources=1)["score"]
    two = score_match(entry, cand, author_sources=2)["score"]
    # Bonus only matters when not already capped at 100.
    assert two >= one


def test_swapped_authors_flagged_not_chimeric() -> None:
    entry = _rec("Some Title Here", ["Smith, J", "Doe, A"])
    cand = _rec("Some Title Here", ["Doe, A", "Smith, J"])
    result = score_match(entry, cand)
    assert "authors_swapped" in result["flags"]
    assert "chimeric" not in result["flags"]  # same author set, just reordered


def test_fabricated_authors_penalized() -> None:
    entry = _rec("Some Title Here", ["Smith, J", "Ghost, A", "Phantom, B"])
    cand = _rec("Some Title Here", ["Smith, J"])
    result = score_match(entry, cand)
    assert "fabricated_author" in result["flags"]


def test_strict_flags_single_edit_title_typo() -> None:
    # A one-character typo leaves token-overlap high, so default does not flag it.
    entry = _rec("Privacys Is All You Need", ["Smith, Jane"])
    cand = _rec("Privacy Is All You Need", ["Smith, Jane"])
    assert "title_mismatch" not in score_match(entry, cand)["flags"]
    assert "title_mismatch" in score_match(entry, cand, strict=True)["flags"]


def test_strict_flags_truncated_author_list() -> None:
    entry = _rec("Known Paper", ["Smith, J"])  # only 1 of 3, no 'and others'
    cand = _rec("Known Paper", ["Smith, J", "Doe, A", "Roe, B"])
    assert "author_truncated" not in score_match(entry, cand)["flags"]
    assert "author_truncated" in score_match(entry, cand, strict=True)["flags"]


def test_strict_truncation_allows_and_others_sentinel() -> None:
    entry = _rec("Known Paper", ["Smith, J", "others"])  # discloses truncation
    cand = _rec("Known Paper", ["Smith, J", "Doe, A", "Roe, B"])
    assert "author_truncated" not in score_match(entry, cand, strict=True)["flags"]
