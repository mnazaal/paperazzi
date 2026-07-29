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


def test_absent_author_list_is_not_scored_as_author_disagreement() -> None:
    """A candidate carrying no authors is missing evidence, not conflicting
    evidence: several providers return title + venue only, and scoring that as
    a mismatch rejects an exact title match outright."""
    entry = _rec("Attention Is All You Need", ["Vaswani, Ashish"])
    sparse = _rec("Attention Is All You Need", [])

    result = score_match(entry, sparse)

    assert result["flags"] == ["author_unknown"]
    assert result["score"] == 100


def test_author_disagreement_is_still_penalized() -> None:
    # The absent-evidence carve-out must not weaken the real mismatch case.
    entry = _rec("Attention Is All You Need", ["Vaswani, Ashish"])
    wrong = _rec("Attention Is All You Need", ["Nobody, Real"])

    result = score_match(entry, wrong)

    assert "chimeric" in result["flags"]
    assert "author_mismatch" in result["flags"]
    assert result["score"] < 60


# === DOI contradiction ===


def test_score_match_flags_a_doi_that_disagrees() -> None:
    """Two different DOIs are a contradiction, not a naming difference."""
    entry = {"title": "Graph Parsers", "authors": ["Smith, Jane"], "doi": "10.1145/3372297"}
    candidate = {"title": "Graph Parsers", "authors": ["Smith, Jane"], "doi": "10.1145/9999999"}

    match = score_match(entry, candidate)

    assert "doi_mismatch" in match["flags"]
    assert any("DOI disagrees" in c for c in match["contributions"])


def test_score_match_treats_the_same_doi_written_differently_as_agreement() -> None:
    entry = {"title": "Graph Parsers", "authors": ["Smith, Jane"], "doi": "10.1145/3372297"}
    candidate = {
        "title": "Graph Parsers",
        "authors": ["Smith, Jane"],
        "doi": "https://doi.org/10.1145/3372297",
    }

    assert "doi_mismatch" not in score_match(entry, candidate)["flags"]


def test_score_match_does_not_flag_a_missing_doi_on_either_side() -> None:
    """Provider records are often sparse; absent is not contradictory."""
    entry = {"title": "Graph Parsers", "authors": ["Smith, Jane"], "doi": "10.1145/3372297"}
    candidate = {"title": "Graph Parsers", "authors": ["Smith, Jane"]}

    assert "doi_mismatch" not in score_match(entry, candidate)["flags"]
    assert "doi_mismatch" not in score_match(candidate, entry)["flags"]


def test_score_match_does_not_flag_a_preprint_against_its_published_doi() -> None:
    """The promote path scores exactly this pairing.

    An arXiv preprint legitimately has a different DOI from the journal
    version, so penalizing it here would suppress valid promotions.
    """
    preprint = {
        "title": "Graph Parsers",
        "authors": ["Smith, Jane"],
        "doi": "10.48550/arXiv.2401.00001",
        "arxiv_id": "2401.00001",
    }
    published = {
        "title": "Graph Parsers",
        "authors": ["Smith, Jane"],
        "doi": "10.1145/3372297",
    }

    assert "doi_mismatch" not in score_match(preprint, published)["flags"]


# === given-name substitution ===


def test_score_match_flags_a_different_first_name_behind_a_matching_surname() -> None:
    """Surname-only comparison scored this as a perfect author match.

    `_normalize_author` discards given names, so "Yao, Shunyu" and "Yao, Denny"
    were indistinguishable — the exact shape of a fabricated citation that
    borrows a real surname.
    """
    entry = {"title": "Tree of Thoughts", "authors": ["Yao, Denny"]}
    candidate = {"title": "Tree of Thoughts", "authors": ["Yao, Shunyu"]}

    match = score_match(entry, candidate)

    assert "given_name_substitution" in match["flags"]
    assert match["score"] < 100


def test_score_match_accepts_initials_and_middle_names_as_the_same_person() -> None:
    for entry_author, cand_author in (
        ("Yao, S.", "Yao, Shunyu"),
        ("Yao, Shunyu K.", "Yao, Shunyu"),
        ("Yao, Shunyu", "Yao, S"),
    ):
        match = score_match(
            {"title": "Tree of Thoughts", "authors": [entry_author]},
            {"title": "Tree of Thoughts", "authors": [cand_author]},
        )
        assert "given_name_substitution" not in match["flags"], (entry_author, cand_author)


def test_score_match_leaves_unmatched_surnames_to_the_fabricated_author_check() -> None:
    """A surname with no counterpart is not a given-name substitution."""
    entry = {"title": "Tree of Thoughts", "authors": ["Nonexistent, Jane"]}
    candidate = {"title": "Tree of Thoughts", "authors": ["Yao, Shunyu"]}

    assert "given_name_substitution" not in score_match(entry, candidate)["flags"]
