"""Identity comparison keeps non-ASCII letters (re-review: non-Latin titles)."""
from __future__ import annotations

import pytest

from pzi.bib_repository import same_paper
from pzi.ledger import entry_identity
from pzi.similarity import normalize_title, title_identity


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Глубокое обучение", "Теория игр"),
        ("深度学习", "强化学习"),
        ("量子 Computing", "光子 Computing"),
        ("Latin title", "Теория игр"),
    ],
)
def test_different_non_latin_titles_are_different_papers(a: str, b: str) -> None:
    assert not same_paper({"title": a}, {"title": b})
    assert entry_identity({"title": a}) != entry_identity({"title": b})


def test_same_non_latin_title_is_same_paper() -> None:
    assert same_paper({"title": "Глубокое  Обучение!"}, {"title": "глубокое обучение"})


def test_accented_latin_still_folds() -> None:
    assert title_identity("Café") == title_identity("Cafe") == "cafe"
    assert same_paper({"title": "Schrödinger"}, {"title": "Schrodinger"})


@pytest.mark.parametrize("t", ["Attention Is All You Need", "A  B-c: d?", "x2 y3", ""])
def test_ascii_identity_unchanged(t: str) -> None:
    assert title_identity(t) == normalize_title(t)


def test_ascii_ledger_digest_is_pinned() -> None:
    # Existing ledger entries must stay valid: this digest was produced by
    # the pre-change code for this record.
    assert entry_identity({"title": "Attention Is All You Need"}) == PINNED


PINNED = "788d9e1826120ca0"


@pytest.mark.parametrize(
    "t",
    [
        "$\\sigma_{n}$ estimate",
        "foo_ bar",
        "foo _bar",
        "foo__bar",
        "a_-b",
        "snake_case_name",
        "x_t notation",
        "Bound on $x_{t}$ and more",
        "_leading and trailing_",
        "Caf{\\'e} au lait",
        "A  B-c: d?",
        "x2 y3",
        "",
    ],
)
def test_ascii_digest_matches_pre_change_ledger_rule(t: str) -> None:
    # Independent oracle: the ledger hashed normalize_title before the
    # non-Latin change, and normalize_title is still the ASCII-only rule.
    import hashlib
    import json

    from pzi.similarity import extract_identities

    ids = sorted(
        f"{i['kind']}:{i['value']}"
        for i in extract_identities({"title": t})
        if i["kind"] in ("doi", "arxiv")
    )
    payload = json.dumps([ids, normalize_title(t)], ensure_ascii=True)
    old = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    assert entry_identity({"title": t}) == old
    assert title_identity(t) == normalize_title(t)
