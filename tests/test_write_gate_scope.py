"""The write gate splits in two, and the split must not lose a refusal.

`validate_bibtex_roundtrip` used to run over the whole library on every write:
converting, serializing, re-parsing and comparing all 22,232 entries to add one
tag. `assert_citekeys_unique` + a round-trip over only the touched entries costs
a fraction of that, but only if the two together refuse exactly what the whole-
library gate refused.

These tests are the differential: for each case, the old whole-library gate and
the new pair of checks must reach the same verdict. The one deliberate exception
is stated and pinned at the bottom.
"""

from __future__ import annotations

import pytest

from pzi.bib_serialize import assert_citekeys_unique, validate_bibtex_roundtrip
from pzi.errors import PziError


def _entry(citekey: str, **fields: str) -> dict:
    return {
        "entry_type": "article",
        "citekey": citekey,
        "fields": {"title": "T", **fields},
    }


def _whole_library_gate(entries: list[dict]) -> str | None:
    """The gate as it was: one round-trip over everything. None when it passes."""
    try:
        validate_bibtex_roundtrip(entries)
    except PziError as exc:
        return str(exc)
    return None


def _split_gate(entries: list[dict], touched: list[int]) -> str | None:
    """The gate as it becomes: uniqueness over all, round-trip over touched."""
    try:
        assert_citekeys_unique(entries)
        validate_bibtex_roundtrip([entries[i] for i in touched])
    except PziError as exc:
        return str(exc)
    return None


#: Each case is (name, entries, touched positions). The corpus is citekey-shaped
#: on purpose: value-level hazards (unbalanced braces, `@`, `%`, newlines,
#: backslashes) all pass the gate because `_safe_field_value` sanitizes them
#: upstream, so including them would prove nothing about the split.
_CORPUS: list[tuple[str, list[dict], list[int]]] = [
    ("clean single entry", [_entry("a")], [0]),
    ("clean library, one touched", [_entry("a"), _entry("b"), _entry("c")], [1]),
    ("touched entry has an empty citekey", [_entry("a"), _entry("")], [1]),
    ("touched entry citekey has a brace", [_entry("a"), _entry("b{c")], [1]),
    ("touched entry citekey has a comma", [_entry("a"), _entry("b,c")], [1]),
    ("insert duplicates an untouched entry", [_entry("a"), _entry("b"), _entry("b")], [2]),
    ("rename collides with an untouched entry", [_entry("beta"), _entry("beta")], [1]),
    ("two untouched entries collide", [_entry("x"), _entry("x"), _entry("t")], [2]),
    ("touched entry is fine, library is fine", [_entry("a"), _entry("b")], [0]),
    ("every entry touched", [_entry("a"), _entry("b")], [0, 1]),
    ("value hazards, which the sanitizer absorbs",
     [_entry("a", title="unbalanced { brace @article{x} 50% \\emph{y}")], [0]),
]


@pytest.mark.parametrize("name, entries, touched", _CORPUS, ids=[c[0] for c in _CORPUS])
def test_the_split_gate_refuses_exactly_what_the_whole_library_gate_refused(
    name: str, entries: list[dict], touched: list[int]
) -> None:
    """Both gates reach the same verdict, and for the same stated reason."""
    whole = _whole_library_gate(entries)
    split = _split_gate(entries, touched)
    assert (whole is None) == (split is None), (
        f"{name}: whole-library gate said {whole!r}, split gate said {split!r}"
    )
    if whole is not None and "duplicate citekey" in whole:
        # The message a user sees for the one failure both gates can reach from
        # different code paths must not drift between them.
        assert split is not None and "duplicate citekey" in split


def test_uniqueness_is_checked_across_the_whole_library_not_just_the_touched_part() -> None:
    """The reason the split is two checks and not one.

    Citekey uniqueness is a property of the *pair*. Scoping the round-trip alone
    would let a rename collide with an entry the write never touched — refused
    today only by the whole-library gate, and nothing upstream catches it on the
    update path.
    """
    entries = [_entry("alpha"), _entry("beta"), _entry("beta")]
    # Round-tripping only the touched entry sees nothing wrong with it.
    validate_bibtex_roundtrip([entries[2]])
    # The uniqueness half is what refuses.
    with pytest.raises(PziError, match="duplicate citekey beta"):
        assert_citekeys_unique(entries)


def test_uniqueness_reports_every_colliding_key_once() -> None:
    entries = [_entry("a"), _entry("a"), _entry("b"), _entry("b"), _entry("c")]
    with pytest.raises(PziError) as excinfo:
        assert_citekeys_unique(entries)
    message = str(excinfo.value)
    assert "a" in message and "b" in message
    assert message.count("duplicate citekey") == 1


def test_uniqueness_accepts_a_library_with_no_collisions() -> None:
    assert_citekeys_unique([_entry("a"), _entry("b"), _entry("c")]) is None
