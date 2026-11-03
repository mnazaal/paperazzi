"""The prefix filter must find exactly what a scan of every record finds."""

from __future__ import annotations

import random

import pytest

from pzi.similarity import _prepare, author_overlap, best_fuzzy_match_details

VOCAB = [f"word{i}" for i in range(30)]
SURNAMES = ["Smith", "Jones", "Chen", "Garcia"]


def _scan_every_record(records, *, positions, title_threshold, year_window):
    """The specification, written as the naive scan: no index, no filter."""
    prepared = _prepare(records)
    found = {}
    for position in positions:
        query = prepared[position]
        if not query.tokens:
            continue
        best, best_score = None, 0.0
        for candidate, other in enumerate(prepared):
            if candidate == position or other.citekey is None or not other.tokens:
                continue
            shared = len(query.tokens & other.tokens)
            if not shared:
                continue
            similarity = shared / len(query.tokens | other.tokens)
            if similarity < title_threshold:
                continue
            if (
                query.year is not None
                and other.year is not None
                and abs(query.year - other.year) > year_window
            ):
                continue
            overlap = author_overlap(query.authors, other.authors)
            if overlap == 0 and similarity < 0.85:
                continue
            score = similarity + 0.1 * overlap
            if score > best_score:
                best_score, best = score, (other.citekey, similarity, overlap)
        if best is not None:
            found[position] = best
    return found


def _library(seed: int, size: int) -> list[dict]:
    rng = random.Random(seed)
    records = []
    base_titles = [rng.sample(VOCAB, rng.randint(1, 12)) for _ in range(size // 3 + 1)]
    for index in range(size):
        title = list(rng.choice(base_titles))
        for _ in range(rng.randint(0, 3)):
            if title and rng.random() < 0.5:
                title.pop(rng.randrange(len(title)))
            else:
                title.append(rng.choice(VOCAB))
        records.append(
            {
                "citekey": None if rng.random() < 0.05 else f"key{index}",
                "title": " ".join(title),
                "year": rng.choice([None, 2019, 2020, 2021, 2024]),
                "authors": [{"family": rng.choice(SURNAMES), "given": "A"}],
            }
        )
    return records


@pytest.mark.parametrize("threshold", [0.1, 0.3, 0.5, 0.6, 0.75, 0.9, 1.0])
@pytest.mark.parametrize("seed", range(8))
def test_filter_matches_the_full_scan(seed: int, threshold: float) -> None:
    records = _library(seed, 120)
    positions = range(len(records))
    actual = best_fuzzy_match_details(
        records, positions=positions, title_threshold=threshold, year_window=2
    )
    expected = _scan_every_record(
        records, positions=positions, title_threshold=threshold, year_window=2
    )
    assert {k: tuple(v) for k, v in actual.items()} == expected
