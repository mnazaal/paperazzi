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
        ("auth + year", "smith2024"),
        ("auth.lower + year", "smith2024"),
        ("auth.upper", "smith"),  # citekey is lowercased by final sanitize
        ("title.lower", "thegraphneuralnetworksoftomorrow"),
        ("'fixed' + year", "fixed2024"),
        ('"dq" + year', "dq2024"),
        ("doi", "101foo"),
        ("venue.lower", "neurips"),
        ("shorttitle(3)", "graphneuralnetworks"),
        ("shorttitle(3,5)", "graphneuranetwo"),
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
    out = format_citekey("auth + year", _RECORD, {"smith2024"})
    assert out != "smith2024"
    assert out.startswith("smith2024")


def test_format_citekey_fold_filter_strips_accents() -> None:
    record = {"authors": ["Müller, Anna"], "title": "X", "year": 2020}
    assert format_citekey("auth.fold.lower + year", record, set()) == "muller2020"


def test_format_citekey_empty_base_falls_back_to_generated() -> None:
    # A template that renders to nothing falls back to the generated base.
    record = {"authors": ["Smith, John"], "title": "Graphs", "year": 2024}
    out = format_citekey("{{ unsupportedVar }}", record, set())
    assert out  # non-empty generated key
