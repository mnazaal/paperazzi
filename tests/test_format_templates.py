"""Tests for the Zotero-template / Better-BibTeX formatting helpers."""

from __future__ import annotations

import pytest

from pzi.format_templates import (
    format_citekey,
    format_pdf_filename,
    render_zotero_template,
)

_RECORD = {
    "authors": ["Smith, John", "Doe, Jane"],
    "title": "The Graph Neural Networks of Tomorrow",
    "year": 2024,
    "doi": "10.1/foo",
    "venue": "NeurIPS",
    "citekey": "smith2024graph",
    "item_type": "journalArticle",
}


# --- render_zotero_template -------------------------------------------------


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{{ year }}", "2024"),
        ("{{ title }}", "The Graph Neural Networks of Tomorrow"),
        ("{{ firstCreator }}", "Smith and Doe"),  # Zotero's Creator column
        ("{{ auth }}", "Smith"),  # Better BibTeX's, one surname
        ("{{ authors }}", "Smith and Doe"),
        ("{{ citationKey }}", "smith2024graph"),
        ("{{ publicationTitle }}", "NeurIPS"),
        ("{{ doi }}", "10.1/foo"),
        ("{{ itemType }}", "journalArticle"),
        ("{{ unsupportedVar }}", ""),  # unknown vars render empty
    ],
)
def test_render_zotero_template_variables(template, expected) -> None:
    assert render_zotero_template(template, _RECORD) == expected


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{{ year prefix='[' suffix=']' }}", "[2024]"),
        ("{{ firstCreator case='upper' }}", "SMITH AND DOE"),
        ("{{ firstCreator case='lower' }}", "smith and doe"),
        ("{{ title case='hyphen' truncate='9' }}", "the-graph"),
        ("{{ title case='snake' truncate='9' }}", "the_graph"),
        ("{{ title start='4' truncate='5' }}", "Graph"),
        ("{{ title match='Graph' truncate='3' }}", "The"),
        ("{{ title match='ZZZ' }}", ""),  # match fails → empty
        ("{{ title replaceFrom='Graph' replaceTo='X' truncate='7' }}", "The X N"),
        ("{{ year start='notint' }}", "2024"),  # bad start ignored
        ("{{ year truncate='notint' }}", "2024"),  # bad truncate ignored
        # Invalid regexes must degrade safely (unsupported options render
        # empty/unchanged) rather than raising re.error out of a copied
        # Zotero template.
        ("{{ title match='[' }}", ""),
        ("{{ title replaceFrom='[' replaceTo='X' }}", "The Graph Neural Networks of Tomorrow"),
        # An unbalanced quote in the options list makes `shlex` raise during
        # iteration. Degrade to "no options" like the cases above rather than
        # letting a one-character config typo traceback out of every add/pdf.
        ("{{ title truncate='100 }}", "The Graph Neural Networks of Tomorrow"),
        ('{{ title suffix=" - }}', "The Graph Neural Networks of Tomorrow"),
    ],
)
def test_render_zotero_template_options(template, expected) -> None:
    assert render_zotero_template(template, _RECORD) == expected


def test_render_zotero_template_skips_single_initial_author() -> None:
    record = {"authors": ["N.", "Watanabe, Ken"]}
    assert render_zotero_template("{{ firstCreator }}", record) == "Watanabe"


# --- firstCreator, and the `auth` it is not ---------------------------------

_ONE = {"authors": ["Ghahramani, Zoubin"], "year": 1998, "title": "Factorial Learning"}
_TWO = {"authors": ["Chow, C", "Tsitsiklis, J"], "year": 1989, "title": "Dynamic Programming"}
_MANY = {
    "authors": ["Blondel, Vincent D", "Bournez, Olivier", "Koiran, Pascal"],
    "year": 2001,
    "title": "Saturated Linear Dynamical Systems",
}

#: The library-parity template: Zotero's documented default, with the `and`
#: reduced to a hyphen and `et al.`'s period dropped.
_LEGACY_ZOTERO = (
    r'{{ firstCreator suffix="-" replaceFrom="\s+and\s+" replaceTo="-" '
    r'replaceFrom2="\.$" replaceTo2="" }}'
    r'{{ year suffix="-" }}{{ title truncate="100" }}'
)


@pytest.mark.parametrize(
    "record,expected",
    [
        (_ONE, "Ghahramani"),
        (_TWO, "Chow and Tsitsiklis"),
        (_MANY, "Blondel et al."),
    ],
)
def test_first_creator_summarizes_the_creator_list(record, expected) -> None:
    # Zotero's `firstCreator` is the Creator column, not the first surname:
    # one name alone, two joined by `and`, three or more as `et al.`. Returning
    # names[0] for all three dropped every co-author from the default template.
    assert render_zotero_template("{{ firstCreator }}", record) == expected


@pytest.mark.parametrize(
    "record,expected",
    [
        (_ONE, "Ghahramani-1998-Factorial Learning.pdf"),
        (_TWO, "Chow-Tsitsiklis-1989-Dynamic Programming.pdf"),
        (_MANY, "Blondel et al-2001-Saturated Linear Dynamical Systems.pdf"),
    ],
)
def test_legacy_zotero_filenames_are_reproducible(record, expected) -> None:
    # The shape of a library renamed by Zotero itself. Reproducing all three
    # creator counts is the point of the feature; a two-author fixture alone
    # cannot tell `firstCreator` apart from `auth`, which is how the gap sat
    # here unnoticed.
    assert format_pdf_filename(_LEGACY_ZOTERO, record) == expected


def test_auth_keeps_one_meaning_across_both_dialects() -> None:
    # `auth` is Better BibTeX's, and a citekey component must stay one word.
    # It renders through `_render_bbt_part` in a formula and `_template_value`
    # in a `{{ }}` template — two call sites that once shared `firstCreator`'s
    # helper, so teaching that helper about co-authors rewrote every citekey.
    formula = 'auth.lower + "-" + shorttitle(1, 0).lower + "-" + year'
    assert format_citekey(formula, _MANY, set()) == "blondel-saturated-2001"
    assert render_zotero_template("{{ auth }}", _MANY) == "Blondel"


def test_second_replacement_pair_applies_after_the_first() -> None:
    template = (
        r'{{ title replaceFrom="Graph" replaceTo="Mesh" '
        r'replaceFrom2="Mesh" replaceTo2="Lattice" }}'
    )
    assert render_zotero_template(template, _RECORD) == "The Lattice Neural Networks of Tomorrow"


def test_replacement_accepts_zotero_capture_group_syntax() -> None:
    # Zotero documents `$1`; `re.sub` reads it literally, so a template copied
    # from Zotero's own documentation put a dollar sign in the filename.
    assert (
        render_zotero_template(r'{{ year replaceFrom="(20)(24)" replaceTo="$2$1" }}', _RECORD)
        == "2420"
    )
    assert (
        render_zotero_template(r'{{ year replaceFrom="^" replaceTo="$$" }}', _RECORD) == "$2024"
    )


# --- format_pdf_filename ----------------------------------------------------


def test_format_pdf_filename_uses_template() -> None:
    assert format_pdf_filename("{{ firstCreator }}{{ year }}", _RECORD) == "Smith and Doe2024.pdf"


def test_format_pdf_filename_falls_back_to_citekey() -> None:
    assert format_pdf_filename(None, _RECORD) == "smith2024graph.pdf"


def test_format_pdf_filename_falls_back_to_generated_when_no_citekey() -> None:
    record = {"authors": ["Smith, John"], "title": "Graphs", "year": 2024}
    out = format_pdf_filename(None, record)
    assert out.endswith(".pdf") and out != ".pdf"


def test_format_pdf_filename_strips_double_pdf_extension() -> None:
    assert format_pdf_filename("paper.pdf", {}) == "paper.pdf"


def test_format_pdf_filename_empty_stem_becomes_paper() -> None:
    # A template of forbidden-only characters renders non-empty but sanitizes
    # to nothing, exercising the "paper" fallback.
    assert format_pdf_filename("///", {}) == "paper.pdf"


def test_format_pdf_filename_truncates_overlong_stem() -> None:
    record = {"citekey": "x" * 400}
    out = format_pdf_filename(None, record)
    assert len(out.encode("utf-8")) <= 244  # 240 cap + ".pdf"


# --- format_citekey (Better BibTeX subset) ----------------------------------


@pytest.mark.parametrize(
    "template,expected",
    [
        # Case is preserved now: BBT's `auth` yields the family name as
        # written, and `.lower` is what asks for lowercase. The old value
        # came from a final sanitizer that lowercased everything, which is
        # also why `.upper` below could never have an effect.
        ("auth + year", "Smith2024"),
        ("auth.lower + year", "smith2024"),
        ("auth.upper", "SMITH"),
        ("title.lower", "thegraphneuralnetworksoftomorrow"),
        ("'fixed' + year", "fixed2024"),
        ('"dq" + year', "dq2024"),
        ("doi", "101foo"),
        ("venue.lower", "neurips"),
        ("shorttitle(3)", "graphneuralnetworks"),
        # BBT: `m` is the number of words to capitalize, not a truncation
        # length. Five requested, three available, so all three.
        ("shorttitle(3,5)", "GraphNeuralNetworks"),
        ("shorttitle(1)", "graph"),
        # Regression: the unrecognized-field fallback used to look the value
        # up by the original filter-suffixed, mixed-case token
        # ("item_type.lower") instead of the parsed field name ("item_type"),
        # so any field without a dedicated branch (and any filter chain)
        # always rendered empty.
        ("item_type.lower", "journalarticle"),
    ],
)
def test_format_citekey_better_bibtex(template, expected) -> None:
    assert format_citekey(template, _RECORD, set()) == expected


def test_format_citekey_zotero_template() -> None:
    assert (
        format_citekey("{{ firstCreator case='lower' }}{{ year }}", _RECORD, set())
        == "smithanddoe2024"
    )


def test_format_citekey_no_template_generates_base() -> None:
    out = format_citekey(None, _RECORD, set())
    assert out and out.isascii()


def test_format_citekey_resolves_collision() -> None:
    out = format_citekey("auth + year", _RECORD, {"Smith2024"})
    assert out != "Smith2024"
    assert out.startswith("Smith2024")


def test_format_citekey_fold_filter_strips_accents() -> None:
    record = {"authors": ["Müller, Anna"], "title": "X", "year": 2020}
    assert format_citekey("auth.fold.lower + year", record, set()) == "muller2020"


def test_folding_matches_better_bibtex_rather_than_deleting() -> None:
    """NFKD has no combining form for a stroked letter, so ASCII-encoding
    *deleted* it: `Weiß`→`Wei` (BBT writes `weiss`), `Søndergaard`→`Sndergaard`,
    `Łukasz`→`ukasz` — a name missing a letter, in the key and the filename."""
    def _key(author: str) -> str:
        return format_citekey(
            "auth.lower", {"authors": [author], "title": "X", "year": 2020}, set()
        )

    assert _key("Weiß, Klaus") == "weiss"
    assert _key("Søndergaard, Ole") == "sondergaard"
    assert _key("Łukasz Kaiser") == "kaiser"
    # Umlauts stay BBT's way (`u`, not `ue`) — author *matching* folds them
    # differently on purpose, and reproducing a BBT key is what this dialect is
    # for.
    assert _key("Müller, Anna") == "muller"


def test_a_particle_name_keys_the_same_either_way_round() -> None:
    """One author, two storage forms, two different citekeys.

    `auth` took `text.split()[-1]` for an unreversed name, so
    `"van der Berg, Anna"` gave `vanderberg` and `"Anna van der Berg"` gave
    `berg`. `similarity` had already fixed the identical split for matching.
    """
    def _key(author: str) -> str:
        return format_citekey(
            "auth.lower", {"authors": [author], "title": "X", "year": 2020}, set()
        )

    assert _key("van der Berg, Anna") == _key("Anna van der Berg") == "vanderberg"


def test_format_citekey_empty_base_falls_back_to_generated() -> None:
    # A template that renders to nothing falls back to the generated base.
    record = {"authors": ["Smith, John"], "title": "Graphs", "year": 2024}
    out = format_citekey("{{ unsupportedVar }}", record, set())
    assert out  # non-empty generated key


def test_describe_template_error_flags_an_unbalanced_quote() -> None:
    """Config validation uses this so a typo is reported, not silently dropped."""
    from pzi.format_templates import describe_template_error

    assert describe_template_error('{{ title truncate="100 }}') is not None
    assert describe_template_error("{{ title truncate='100' }}") is None
    assert describe_template_error(None) is None
    # Better-BibTeX formulas take a different renderer, which is why this used
    # to assert `is None` — but "different renderer" was read as "no grammar",
    # so the dialect these templates are actually written in went unchecked.
    # An unterminated quote is a typo there too.
    assert describe_template_error("auth.lower + 's + year") is not None


def test_describe_template_error_checks_better_bibtex_formulas() -> None:
    """`"{{" not in template` returned None, so *no* BBT formula was validated.

    The config-level check says it exists so "a typo would [not] silently drop
    the option from every citekey it generates" — which is exactly what an
    unknown variable or filter does in this dialect.
    """
    from pzi.format_templates import describe_template_error

    # The formula this project's own library was keyed with must stay valid.
    assert describe_template_error(
        'auth.lower + "-" + shorttitle(1,0).lower + "-" + year'
    ) is None
    assert describe_template_error("shorttitle(3,3)") is None
    assert describe_template_error("volume + pages") is None  # any record field

    assert "authr" in (describe_template_error("authr.lower + year") or "")
    assert "lowr" in (describe_template_error("auth.lowr + year") or "")
    assert describe_template_error("this is not a formula") is not None


# ── LaTeX markup in stored titles (2026-09-01) ──────────────────────────────

#: The title is what carries the markup, so the template has to render it —
#: with no template `format_pdf_filename` falls back to the citekey.
_TITLE_ONLY = "{{ title }}"


@pytest.mark.parametrize(
    ("title", "expected_in_name"),
    [
        # The case that produced 22 mangled files during the 2026-09-01 repair:
        # a Zotero/BBT export escapes specials into LaTeX *commands*, and the
        # filename sanitizer's brace/backslash stripping turned each command
        # into its own name as a word.
        (
            r"\$21\textasciicircum\textbraceleft st\textbraceright\$ "
            r"{{Century Statistical Disclosure Limitation}}",
            "Century Statistical Disclosure Limitation",
        ),
        # Accents: `encode('ascii','ignore')` cannot save these because the
        # markup is a command, not a composed character.
        (r"Kl{\"a}ser and the {{MiniMol}} Model", "Klaser"),
        (r"Schr{\"o}dinger {{PCA}}", "Schrodinger"),
        (r"Generalized P{\'o}lya Urn", "Polya"),
        # Case-protection braces are markup, never content.
        (r"{{Bayesian Prompt Ensembles}}", "Bayesian Prompt Ensembles"),
    ],
)
def test_latex_markup_never_reaches_a_filename(title, expected_in_name) -> None:
    """A stored title carries LaTeX; the file on disk should not name it.

    Before this, `\\textasciicircum` rendered as the literal word
    "textasciicircum" — the sanitizer deleted the backslash and braces and left
    the command name behind.
    """
    name = format_pdf_filename(_TITLE_ONLY, {"citekey": "x2024", "title": title})
    assert expected_in_name in name
    for command in ("textasciicircum", "textbraceleft", "textbraceright",
                    "textbackslash", "{{", "}}"):
        assert command not in name, f"{command!r} leaked into {name!r}"


def test_a_percent_in_a_title_does_not_truncate_the_filename() -> None:
    """`%` opens a LaTeX comment, so decoding eats the rest of the line.

    `bibtex.py` writes provider titles verbatim, so an unescaped `%` reaches
    the decoder and "50% Faster Training" would otherwise be filed as "50".
    """
    name = format_pdf_filename(
        _TITLE_ONLY, {"citekey": "x2024", "title": "50% Faster Training"}
    )
    assert "Faster Training" in name


def test_a_title_without_markup_is_untouched() -> None:
    """The no-markup path must not move: this is the overwhelming majority."""
    title = "Attention Is All You Need"
    assert format_pdf_filename(_TITLE_ONLY, {"citekey": "x2017", "title": title}) == (
        f"{title}.pdf"
    )


def test_an_unrenderable_macro_keeps_its_text_in_the_filename() -> None:
    """Decoding must never trade a paper's name for a prettier symbol.

    `pylatexenc` renders math it knows and silently drops a macro it does not:
    `$\\texttt{MiniMol}$` decoded to the empty string, so the model's own name
    vanished from its file. Detected per-macro rather than by length, because
    rendering legitimately shrinks text (`\\infty` is five characters, `∞` is
    one) and a length test flagged every correctly decoded formula.
    """
    name = format_pdf_filename(
        _TITLE_ONLY,
        {"citekey": "k2024", "title": r"$\texttt{MiniMol}$ - A Parameter-Efficient Model"},
    )
    assert "MiniMol" in name
    assert "texttt" not in name


def test_a_renderable_macro_still_decodes_to_its_symbol() -> None:
    """The fallback above must not fire on math the decoder handles: `\\mathcal{H}`
    becomes `ℋ`, which folds to `H` — the content is present, not lost."""
    name = format_pdf_filename(
        _TITLE_ONLY,
        {"citekey": "k2022", "title": r"On the Regret of $\mathcal{H}_{\infty}$ Control"},
    )
    assert "mathcal" not in name
    assert name.startswith("On the Regret of H")
