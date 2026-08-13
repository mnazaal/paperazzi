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
        ("{{ firstCreator }}", "Smith"),
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
        ("{{ firstCreator case='upper' }}", "SMITH"),
        ("{{ firstCreator case='lower' }}", "smith"),
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


# --- format_pdf_filename ----------------------------------------------------


def test_format_pdf_filename_uses_template() -> None:
    assert format_pdf_filename("{{ firstCreator }}{{ year }}", _RECORD) == "Smith2024.pdf"


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
    assert format_citekey("{{ firstCreator case='lower' }}{{ year }}", _RECORD, set()) == "smith2024"


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
