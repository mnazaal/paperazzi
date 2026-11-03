"""Three-way keyword merge: (current - plan's removals) + plan's additions."""
from __future__ import annotations

from itertools import combinations

import pytest

from pzi.bib_merge import _merge_keywords
from tests.test_rc3_write_fix import _concurrent

_TAGS = "abc"
_SUBSETS = [frozenset(c) for n in range(4) for c in combinations(_TAGS, n)]


def _join(tags: frozenset[str]) -> str | None:
    return ", ".join(sorted(tags)) or None


@pytest.mark.parametrize("base", _SUBSETS)
@pytest.mark.parametrize("plan", _SUBSETS)
@pytest.mark.parametrize("cur", _SUBSETS)
def test_merge_matches_set_arithmetic(base, plan, cur) -> None:
    got = _merge_keywords(_join(base), _join(plan), _join(cur))
    expected = (cur - (base - plan)) | (plan - base)
    assert set((got or "").split(", ")) - {""} == set(expected)


def test_other_writer_removing_every_tag_is_not_undone(tmp_path) -> None:
    outcome, rec = _concurrent(tmp_path, {"keywords": "a, b"}, {}, {"tags": ["mine"]})
    assert outcome == "wrote"
    assert rec["tags"] == ["mine"]


def test_other_writer_removing_one_tag_is_not_undone(tmp_path) -> None:
    outcome, rec = _concurrent(
        tmp_path, {"keywords": "a, b"}, {"keywords": "b"}, {"tags": ["mine"]}
    )
    assert outcome == "wrote" and sorted(rec["tags"]) == ["b", "mine"]
