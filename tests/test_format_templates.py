from pzi.format_templates import format_citekey, format_pdf_filename, render_zotero_template

RECORD = {
    "authors": ["Smith, Jane", "Doe, John"],
    "year": 2024,
    "title": "A Study of Graph Parsers: Methods and Results.",
    "doi": "10.1234/ABC.DEF",
    "venue": "ICSE",
}


def test_render_zotero_default_file_template() -> None:
    template = (
        '{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}'
        '{{ title truncate="20" }}'
    )

    assert render_zotero_template(template, RECORD) == "Smith - 2024 - A Study of Graph Par"


def test_render_zotero_colon_variables_and_regex_replacement() -> None:
    template = (
        '{{ :firstCreator suffix="-" replaceFrom="\\s+and\\s+|\\." replaceTo="-" }}'
        '{{ :year suffix="-" }}'
        '{{ :title truncate="100" replaceFrom="\\s+" replaceTo="-" regexOpts="g" }}'
    )

    assert (
        render_zotero_template(template, RECORD)
        == "Smith-2024-A-Study-of-Graph-Parsers:-Methods-and-Results."
    )


def test_format_pdf_filename_sanitizes_path_separators_and_adds_extension() -> None:
    template = (
        '{{ firstCreator suffix="-" }}{{ year suffix="-" }}'
        '{{ title truncate="100" }}'
    )
    record = {**RECORD, "title": "Bad / Path: Paper"}

    assert format_pdf_filename(template, record) == "Smith-2024-Bad Path Paper.pdf"


def test_format_citekey_supports_zotero_template_and_collision_suffix() -> None:
    template = '{{ firstCreator }}{{ year }}{{ title truncate="5" }}'

    assert format_citekey(template, RECORD, {"smith2024astu"}) == "smith2024astu2"


def test_format_citekey_supports_common_better_bibtex_formula() -> None:
    assert format_citekey("auth.lower + shorttitle(3,3) + year", RECORD, set()) == "smithstu2024"
