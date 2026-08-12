"""Reproducing a Better BibTeX citation key exactly.

A library exported from Zotero/BBT has keys like `tsiotras-algorithmic-2017`.
Pointing pzi at that library and setting `citekey_format` to the *same* BBT
formula silently produced `tsiotras2017`: the quoted `"-"` separators were
parsed and then deleted by the final sanitizer, and `shorttitle(1, 0)` rendered
empty because `m` was read as a per-word truncation length. Neither failed
loudly, so new captures would drift from every existing key in the library.

BBT's documented signature is `shorttitle(n=3, m=0)` — n words selected, m of
them capitalized, 0 meaning none. A truncation reading cannot be right: m
defaults to 0, so every default `shorttitle()` would render empty.
"""

import pytest

from pzi.format_templates import format_citekey

_TSIOTRAS = {
    "authors": ["Tsiotras, Panagiotis"],
    "title": "Algorithmic Motion Planning",
    "year": 2017,
}


def test_reproduces_the_users_better_bibtex_key() -> None:
    template = 'auth.lower + "-" + shorttitle(1, 0).lower + "-" + year'

    assert format_citekey(template, _TSIOTRAS, set()) == "tsiotras-algorithmic-2017"


def test_single_quoted_separators_work_too() -> None:
    template = "auth.lower + '-' + shorttitle(1,0).lower + '-' + year"

    assert format_citekey(template, _TSIOTRAS, set()) == "tsiotras-algorithmic-2017"


@pytest.mark.parametrize("separator", ["-", "_", ":", "."])
def test_each_bibtex_safe_separator_survives(separator: str) -> None:
    template = f'auth.lower + "{separator}" + year'

    assert format_citekey(template, _TSIOTRAS, set()) == f"tsiotras{separator}2017"


def test_m_capitalizes_words_rather_than_truncating_them() -> None:
    """BBT: "the first n words of the title, capitalize the first m"."""
    assert format_citekey("shorttitle(3, 3)", _TSIOTRAS, set()) == "AlgorithmicMotionPlanning"
    # m=1 capitalizes only the first of the three selected words.
    assert format_citekey("shorttitle(3, 1)", _TSIOTRAS, set()) == "Algorithmicmotionplanning"


def test_zero_capitalized_words_is_the_documented_default() -> None:
    assert format_citekey("shorttitle(3)", _TSIOTRAS, set()) == "algorithmicmotionplanning"
    assert format_citekey("shorttitle(3, 0)", _TSIOTRAS, set()) == "algorithmicmotionplanning"


def test_an_empty_component_does_not_leave_a_doubled_separator() -> None:
    """A record with no year must not yield `tsiotras-algorithmic-`."""
    no_year = {k: v for k, v in _TSIOTRAS.items() if k != "year"}
    template = 'auth.lower + "-" + shorttitle(1, 0).lower + "-" + year'

    assert format_citekey(template, no_year, set()) == "tsiotras-algorithmic"


def test_a_variable_cannot_smuggle_punctuation_into_the_key() -> None:
    """Only quoted literals may introduce a separator.

    `title` is emitted raw by `_render_bbt_part`, so loosening the final
    sanitizer without sanitizing per part would let spaces and punctuation
    through into the key.
    """
    messy = {"authors": ["O'Neill, Máire"], "title": "Fast, Cheap & Out of Control!", "year": 2020}

    key = format_citekey("auth.lower + title.lower + year", messy, set())

    assert key == "oneillfastcheapoutofcontrol2020"


def test_the_built_in_scheme_is_untouched_by_any_of_this() -> None:
    """No template means the built-in generator, which must not gain separators."""
    key = format_citekey(None, _TSIOTRAS, set())

    assert key == "tsiotras2017algorithmic"


def test_hyphenated_surnames_keep_their_hyphen() -> None:
    """BBT's `auth` yields `domingo-enrich`, not `domingoenrich`.

    A hyphen inside a family name is part of the name, not a separator the
    template introduced, and a library full of `domingo-enrich-learning-2022`
    keys cannot be reproduced without it.
    """
    record = {
        "authors": ["Domingo-Enrich, Carles"],
        "title": "Learning Gradient Fields",
        "year": 2022,
    }
    template = 'auth.lower + "-" + shorttitle(1, 0).lower + "-" + year'

    assert format_citekey(template, record, set()) == "domingo-enrich-learning-2022"


@pytest.mark.parametrize(
    "title,expected_word",
    [
        ("Towards Digesting the Alphabet-Soup", "digesting"),
        ("From Learning to Meta-Learning", "learning"),
        ("On the Unified View", "unified"),
        ("A Study of Networks", "study"),
    ],
)
def test_better_bibtex_skipwords_are_dropped(title: str, expected_word: str) -> None:
    """pzi skipped 10 stopwords; BBT's default list has well over a hundred.

    Every mismatch of this shape produced a key built on the wrong title word
    ("towards" instead of "digesting"), which is worse than an obviously
    malformed key because it looks plausible.
    """
    record = {"authors": ["Raedt, Luc De"], "title": title, "year": 2008}

    assert format_citekey("shorttitle(1, 0).lower", record, set()) == expected_word
