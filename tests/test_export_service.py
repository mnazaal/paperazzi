
"""Tests for pzi.export_service — all four export formats."""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
from pathlib import Path

from pzi.export_service import (
    export_bibtex,
    export_csv,
    export_json,
    export_ris,
)


def _write_bib(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


SIMPLE_BIB = (
    '@article{smith2024,\n'
    '  title = {Deep Learning},\n'
    '  author = {Smith, John and Jones, Alice},\n'
    '  year = {2024},\n'
    '  doi = {10.1000/example},\n'
    '  journal = {Nature},\n'
    '}\n'
)


def test_export_bibtex_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_bibtex(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "bibtex"
        assert result["content_type"] == "application/x-bibtex"


def test_export_bibtex_content() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_bibtex(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        assert "smith2024" in result["content"]
        assert "Deep Learning" in result["content"]


def test_export_json_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_json(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "json"
        assert result["content_type"] == "application/json"
        parsed = json.loads(result["content"])
        assert parsed == []


def test_export_json_records() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_json(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        parsed = json.loads(result["content"])
        assert len(parsed) == 1
        rec = parsed[0]
        assert rec["title"] == "Deep Learning"
        assert rec["doi"] == "10.1000/example"
        assert rec["year"] == 2024
        assert rec["entry_type"] == "article"
        assert "Smith, John" in rec["authors"]
        assert "Jones, Alice" in rec["authors"]


def test_export_csv_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_csv(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "csv"
        assert result["content_type"] == "text/csv"
        # Should have header only
        lines = result["content"].strip().split("\n")
        assert len(lines) == 1  # header only
        assert "citekey" in lines[0]


def test_export_csv_records() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_csv(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        lines = result["content"].strip().split("\n")
        assert len(lines) == 2  # header + 1 row
        assert "smith2024" in lines[1]
        assert "Deep Learning" in lines[1]


def test_export_ris_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_ris(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "ris"
        assert result["content"] == ""


def test_export_ris_records() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_ris(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        assert result["content_type"] == "application/x-research-info-systems"
        ris = result["content"]
        assert "TY  - JOUR" in ris
        assert "TI  - Deep Learning" in ris
        # Title must be emitted exactly once per entry (regression: was duplicated).
        assert ris.count("TI  - ") == 1
        assert "AU  - Smith, John" in ris
        assert "AU  - Jones, Alice" in ris
        assert "DO  - 10.1000/example" in ris
        assert "PY  - 2024" in ris
        assert "ER  - " in ris


def test_export_ris_inproceedings_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(
            bib,
            '@inproceedings{conf2024, title = {Paper}, author = {A}, year = {2024}}',
        )
        result = export_ris(bib)
        assert "TY  - CONF" in result["content"]


def test_normalize_authors_handles_list_string_and_other() -> None:
    from pzi.export_service import _normalize_authors

    assert _normalize_authors(["Smith, J", "Doe, A"]) == "Smith, J; Doe, A"
    assert _normalize_authors("Smith, J and Doe, A") == "Smith, J and Doe, A"
    assert _normalize_authors(None) == ""


def test_normalize_tags_handles_list_string_and_other() -> None:
    from pzi.export_service import _normalize_tags

    assert _normalize_tags(["ml", "graphs"]) == "ml, graphs"
    assert _normalize_tags("ml,graphs") == "ml,graphs"
    assert _normalize_tags(None) == ""


def test_export_reports_entries_the_parser_dropped() -> None:
    """An export that silently omits entries is not a backup.

    bibtexparser v2 collects unreadable blocks instead of raising, so every
    exporter used to return the entries that happened to parse with
    `status: ok` and no errors — losing the rest with exit 0.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(
            bib,
            "@article{good2024, title = {Good}, author = {A}, year = {2024}}\n\n"
            "@article{broken2024,\n  title = {Unclosed\n  year = {2024},\n}\n",
        )

        for export in (export_bibtex, export_json, export_csv, export_ris):
            result = export(bib)
            assert result["status"] == "error", export.__name__
            assert result["errors"], export.__name__
            assert "unparseable" in result["errors"][0].lower(), export.__name__


def test_export_reports_a_duplicate_citekey_as_a_dropped_entry() -> None:
    """Only the first block of a duplicated key parses; the second is lost too."""
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(
            bib,
            "@article{dup2024, title = {A}}\n@article{dup2024, title = {B}}\n",
        )

        result = export_bibtex(bib)

        assert result["status"] == "error"
        assert any("duplicate citekey" in err for err in result["errors"])


def test_export_of_a_clean_library_stays_ok() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, '@article{good2024, title = {Good}, year = {2024}}')

        result = export_bibtex(bib)

        assert result["status"] == "ok"
        assert result["errors"] == []


def test_a_missing_bib_is_an_export_error_in_every_format(tmp_path: Path) -> None:
    """"The library file is not there" and "the library is empty" are different.

    `read_bib_source` returns empty for a nonexistent path, so every exporter
    used to report a clean export of zero entries — and the CLI then wrote that
    emptiness over the destination. `_export_status`' own docstring already says
    an export that silently omits entries is not an `ok` export; a missing file
    omits all of them.
    """
    missing = str(tmp_path / "not-created-yet.bib")

    for export in (export_bibtex, export_json, export_csv, export_ris):
        result = export(missing)

        assert result["status"] == "error", export.__name__
        assert any(missing in err for err in result["errors"]), export.__name__


def test_export_does_not_overwrite_a_destination_when_the_bib_is_missing(
    tmp_path: Path,
) -> None:
    """The data-loss case: `--force` over a backup after the library moved.

    A renamed file, an unmounted share or a typo'd `path =` used to make
    `pzi export --force -o backup.bib` truncate the backup to zero bytes and
    report "exported 0 entries", exit 0.
    """
    from pzi.cli import run_cli

    backup = tmp_path / "backup.bib"
    backup.write_text(SIMPLE_BIB)
    config = tmp_path / "config.toml"
    config.write_text(
        'translation_server_url = "http://127.0.0.1:59999"\n\n'
        '[[bibs]]\nname = "main"\n'
        f'path = "{tmp_path / "vanished.bib"}"\ndefault = true\n'
    )

    out, err = io.StringIO(), io.StringIO()
    code = run_cli(
        ["export", "--force", "-o", str(backup), "--config", str(config)],
        home_dir=str(tmp_path),
        stdout=out,
        stderr=err,
    )

    assert code != 0
    assert backup.read_text() == SIMPLE_BIB


def test_export_ris_emits_one_ur_line_per_unique_url(tmp_path: Path) -> None:
    """canonical_url and source_url both come from the single BibTeX `url`.

    bibtex.py fills both keys from one field, so every entry read back from a
    bib file emitted the same URL twice.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{smith2024,\n"
        "  title = {Graph Parsers},\n"
        "  url = {https://example.test/paper},\n"
        "}\n"
    )

    ris = export_ris(str(bib))["content"]

    assert ris.count("UR  - ") == 1
    assert "UR  - https://example.test/paper" in ris


def test_export_ris_keeps_distinct_urls(tmp_path: Path) -> None:
    """Deduping must not collapse a genuinely different arXiv URL."""
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{smith2024,\n"
        "  title = {Graph Parsers},\n"
        "  url = {https://example.test/paper},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n"
    )

    ris = export_ris(str(bib))["content"]

    assert "UR  - https://example.test/paper" in ris
    assert "UR  - https://arxiv.org/abs/2401.12345" in ris
    assert ris.count("UR  - ") == 2


def test_export_ris_never_emits_an_untagged_line(tmp_path: Path) -> None:
    """RIS has no continuation syntax; a wrapped abstract broke the record.

    Every line must be `XX  - value`. A multiline abstract emitted bare text
    lines that strict readers drop or mis-assign — and a continuation line
    starting with two characters and "  - " is reparsed as a new field.
    """
    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{smith2024,\n"
        "  title = {Graph Parsers},\n"
        "  abstract = {First line of the abstract\n"
        "AB  - which continues here\n"
        "and ends here},\n"
        "}\n"
    )

    ris = export_ris(str(bib))["content"]

    for line in ris.splitlines():
        if not line:
            continue
        assert re.match(r"^[A-Z][A-Z0-9]  - ", line), f"untagged RIS line: {line!r}"
    # The abstract survives as a single folded AB line.
    abstract_lines = [ln for ln in ris.splitlines() if ln.startswith("AB  - ")]
    assert len(abstract_lines) == 1
    assert "which continues here" in abstract_lines[0]


_DETAILED_BIB = (
    "@article{smith2024,\n"
    "  title = {A Detailed Paper},\n"
    "  author = {Smith, Jane},\n"
    "  year = {2024},\n"
    "  journal = {Journal of Things},\n"
    "  volume = {12},\n"
    "  number = {3},\n"
    "  pages = {123--145},\n"
    "  publisher = {Academic Press},\n"
    "  issn = {1234-5678},\n"
    "  isbn = {978-3-16-148410-0},\n"
    "}\n"
)


def test_ris_export_carries_the_fields_a_citation_needs(tmp_path: Path) -> None:
    """RIS exists to hand a citation to another reference manager.

    `_RIS_FIELDS` omitted volume, number, pages, publisher, issn and isbn — the
    six the records gained in 04e3997 — while `export_json` emitted all of them,
    so an export used as a backup lost page numbers on two of the four formats.
    """
    from pzi.export_service import export_ris

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(_DETAILED_BIB)

    content = export_ris(bib_path=str(bib_path))["content"]

    assert "VL  - 12" in content
    assert "IS  - 3" in content
    assert "PB  - Academic Press" in content
    assert "SN  - 1234-5678" in content
    # RIS splits a page range across two tags rather than carrying `123--145`.
    assert "SP  - 123" in content
    assert "EP  - 145" in content


def test_csv_export_carries_the_same_fields(tmp_path: Path) -> None:
    from pzi.export_service import export_csv

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(_DETAILED_BIB)

    content = export_csv(bib_path=str(bib_path))["content"]

    header, row = content.splitlines()[0], content.splitlines()[1]
    for column in ("volume", "number", "pages", "publisher", "issn", "isbn"):
        assert column in header, column
    assert "123--145" in row
    assert "Academic Press" in row
