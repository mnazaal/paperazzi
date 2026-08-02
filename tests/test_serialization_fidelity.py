"""What a write must not change about an entry it was not asked to change.

Citekeys, field-key capitalization, field order, escaped braces. Each of these
used to be rewritten on every touched entry, silently, by commands whose stated
job was to add a tag or fill one missing field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pzi.bib_repository import read_bib_file, update_bib_entry
from pzi.bibtex import BibtexEntry, apply_record_to_entry
from pzi.errors import PziError


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def _add_keyword(entry: BibtexEntry, _record) -> BibtexEntry:
    touched = dict(entry)
    touched["fields"] = {**entry["fields"], "keywords": "readme"}
    return touched  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Citekeys read off disk survive verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "citekey",
    ["Müller2020", "o'brien2019", "smith&jones2021", "lee(2022)", "naïve/case"],
)
def test_an_on_disk_citekey_survives_a_write_byte_identically(
    tmp_path: Path, citekey: str
) -> None:
    bib = tmp_path / "main.bib"
    path = _write(
        bib,
        f"@article{{{citekey},\n  title = {{A Title}},\n  year = {{2020}},\n}}\n",
    )

    result = update_bib_entry(path, citekey, _add_keyword)  # type: ignore[arg-type]

    assert result["found"] is True
    assert f"@article{{{citekey}," in bib.read_text(encoding="utf-8")


def test_a_composed_citekey_is_still_sanitized() -> None:
    from pzi.add_service import ensure_citekey_for_write

    record = ensure_citekey_for_write(
        {"citekey": "evil{2020},\n@article{injected", "title": "T"},  # type: ignore[arg-type]
        [],
    )

    assert record["citekey"] == "evil2020articleinjected"


def test_serializing_a_structurally_broken_citekey_is_refused() -> None:
    from pzi.bib_serialize import serialize_bibtex

    with pytest.raises(PziError, match="cannot appear in a BibTeX entry key"):
        serialize_bibtex(
            [{"entry_type": "article", "citekey": "a,b", "fields": {"title": "T"}}]
        )


# ---------------------------------------------------------------------------
# Field names are case-insensitive
# ---------------------------------------------------------------------------


JABREF = """@Article{jabref2020,
  Author = {Smith, John},
  Title = {Hello World},
  Doi = {10.1/x},
  File = {papers/jabref2020.pdf},
  Year = {2020},
}
"""


def test_capitalized_field_names_reach_the_record_model(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    path = _write(bib, JABREF)

    record = read_bib_file(path)["records"][0]

    assert record["title"] == "Hello World"
    assert record["doi"] == "10.1/x"
    assert record["authors"] == ["Smith, John"]
    assert record["year"] == 2020
    assert str(record["local_pdf_path"]).endswith("papers/jabref2020.pdf")


def test_a_write_does_not_add_lowercase_twins_of_capitalized_fields(
    tmp_path: Path,
) -> None:
    """The real shape of the bug: `tag`/`update` re-project the record onto the
    entry, and the projection's lowercase keys used to land *beside* the
    capitalized originals — an entry bibtexparser itself then refuses."""
    bib = tmp_path / "main.bib"
    path = _write(bib, JABREF)

    def _tag(entry: BibtexEntry, record) -> BibtexEntry:
        tagged = dict(record)
        tagged["tags"] = ["readme"]
        return apply_record_to_entry(entry, tagged)  # type: ignore[arg-type]

    update_bib_entry(path, "jabref2020", _tag)  # type: ignore[arg-type]

    text = bib.read_text(encoding="utf-8")
    assert text.count("itle = ") == 1
    assert text.count("uthor = ") == 1
    assert "Title = {Hello World}" in text  # the user's capitalization is kept
    assert "keywords = {readme}" in text  # a genuinely new field is lowercase
    # And the result is still readable: a duplicate field key would make
    # bibtexparser drop the whole entry.
    assert [entry["citekey"] for entry in read_bib_file(path)["entries"]] == [
        "jabref2020"
    ]


# ---------------------------------------------------------------------------
# Field order
# ---------------------------------------------------------------------------


def test_a_touched_entry_keeps_its_field_order(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    path = _write(
        bib,
        "@article{a1,\n"
        "  title = {T},\n"
        "  author = {Smith, John},\n"
        "  year = {2020},\n"
        "  doi = {10.1/x},\n"
        "}\n",
    )

    update_bib_entry(path, "a1", _add_keyword)  # type: ignore[arg-type]

    text = bib.read_text(encoding="utf-8")
    positions = [text.index(f"{key} = ") for key in ("title", "author", "year", "doi")]
    assert positions == sorted(positions)
    assert text.index("keywords = ") > positions[-1]


# ---------------------------------------------------------------------------
# Escaped braces
# ---------------------------------------------------------------------------


def test_latex_escaped_braces_survive_a_write(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    path = _write(
        bib,
        "@article{a1,\n"
        "  note = {a \\} b \\{ c \\\\ d},\n"
        "  title = {T},\n"
        "}\n",
    )

    update_bib_entry(path, "a1", _add_keyword)  # type: ignore[arg-type]

    assert "note = {a \\} b \\{ c \\\\ d}" in bib.read_text(encoding="utf-8")


def test_unmatched_braces_are_still_dropped() -> None:
    from pzi.bib_serialize import _balance_braces

    assert _balance_braces("a } b") == "a  b"
    assert _balance_braces("a { b") == "a  b"
    assert _balance_braces("{DNA} sequencing") == "{DNA} sequencing"
    assert _balance_braces("a \\} b") == "a \\} b"


# ---------------------------------------------------------------------------
# A `%` comment inside an entry
# ---------------------------------------------------------------------------


COMMENTED = """@article{a1,
  title = {T},
  % private note
  doi = {10.1/x},
  year = {2020},
}
"""


def test_an_inline_comment_is_reported_and_the_entry_is_not_rewritten(
    tmp_path: Path,
) -> None:
    from pzi.bib_serialize import _parse_bib_library, describe_failed_blocks

    bib = tmp_path / "main.bib"
    path = _write(bib, COMMENTED)

    warnings = describe_failed_blocks(_parse_bib_library(bib.read_text(encoding="utf-8")))
    assert any("'%' comment inside the entry" in message for message in warnings)

    with pytest.raises(PziError, match="refusing to rewrite"):
        update_bib_entry(path, "a1", _add_keyword)  # type: ignore[arg-type]

    assert bib.read_text(encoding="utf-8") == COMMENTED
