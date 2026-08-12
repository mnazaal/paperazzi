"""Source-preservation regressions: every entry mutation must keep comments,
``@string``/``@preamble`` macros, untouched entries, and honor file_path_style.

Covers insert and update (via ``add_record_with_bib``) plus the tag add/remove
and delete paths that previously went through the lossy ``write_bib_file``;
merge and reindex preservation are covered in ``test_dedupe_service`` and
``test_reindex_service``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from pzi.add_service import add_record_with_bib
from pzi.bib_repository import merge_bib_entries, read_bib_file
from pzi.bib_service import delete_entry
from pzi.pdf_service import attach_pdf
from pzi.tag_service import add_tags, remove_tags
from pzi.update_service import update_bib

_PRESERVE_BIB = (
    "% library header comment\n"
    "@string{acm = {ACM}}\n"
    '@preamble{ "\\newcommand{\\noop}[1]{}" }\n'
    "\n"
    "@article{smith2024, title = {Deep Learning}, author = {Smith, John}, year = {2024}}\n"
    "@article{jones2023, title = {Vision}, author = {Jones, K}, year = {2023}}\n"
)


def _config(td: str, *, pdf_file_path_style: str | None = None) -> tuple[str, str, str]:
    bib = os.path.join(td, "lib.bib")
    papers = os.path.join(td, "papers")
    os.makedirs(papers, exist_ok=True)
    config_path = os.path.join(td, "config.toml")
    style = f'pdf_file_path_style = "{pdf_file_path_style}"\n' if pdf_file_path_style else ""
    Path(config_path).write_text(
        f'{style}[[bibs]]\nname = "main"\npath = "{bib}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n'
    )
    return config_path, bib, papers


def test_tag_add_preserves_comments_macros_and_other_entries() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_PRESERVE_BIB)

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2024", tags=["ml"],
        )

        assert result["status"] == "ok" and result["changed"]
        text = Path(bib).read_text()
        assert "% library header comment" in text
        assert "@string{acm" in text
        assert "@preamble{" in text
        assert "@article{jones2023," in text  # untouched entry survives
        assert "keywords = {ml}" in text


def test_tag_add_keeps_the_edited_entry_macro_reference() -> None:
    """The service layer plans from resolved records; the write must not expand.

    `tag_service` sources its record from `read_bib_file`, whose parse stack
    resolves `@string`, then applies it to an entry parsed by a stack that does
    not. Tagging an entry therefore rewrote its unrelated `journal = acm` as the
    macro's full definition — the entry's own reference, silently expanded.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(
            "@string{acm = {Association for Computing Machinery}}\n\n"
            "@article{smith2024,\n"
            "  title = {Deep Learning},\n"
            "  author = {Smith, John},\n"
            "  journal = acm,\n"
            "  year = {2024},\n"
            "}\n"
        )

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2024", tags=["ml"],
        )

        assert result["status"] == "ok" and result["changed"]
        text = Path(bib).read_text()
        assert "keywords = {ml}" in text
        assert "journal = acm," in text
        assert "Association for Computing Machinery}" not in text.split("@article", 1)[1]


def test_tag_remove_preserves_comments_and_macros() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(
            _PRESERVE_BIB.replace(
                "year = {2024}}", "year = {2024}, keywords = {ml, graphs}}"
            )
        )

        result = remove_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2024", tags=["graphs"],
        )

        assert result["status"] == "ok" and result["changed"]
        text = Path(bib).read_text()
        assert "% library header comment" in text
        assert "@string{acm" in text
        assert "keywords = {ml}" in text


def test_update_preview_and_write_both_honor_relative_file_path_style() -> None:
    """`pzi update` ignored `pdf_file_path_style` in both its preview and write.

    `preview_write_plan` took no style at all, and `update_service` never read
    the setting — so a library configured for relative paths got a diff full of
    absolute ones, and then had absolute ones written into it.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bib, papers = _config(td, pdf_file_path_style="relative")
        pdf_abs = os.path.join(papers, "smith2024.pdf")
        Path(pdf_abs).write_bytes(b"%PDF-1.4\n")
        Path(bib).write_text(
            f"@article{{smith2024, title = {{Graph Parsers}}, author = {{Smith, J}}, "
            f"file = {{{pdf_abs}}}}}\n"
        )

        def _search(query: str, *, server_url: str) -> list[dict[str, Any]]:
            return [{
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers", "venue": "CVPR",
                    "doi": "10.1000/foo", "year": 2024, "authors": ["Smith, J"],
                },
                "attachments": [],
            }]

        preview = update_bib(
            config_path=cp, home_dir=td, bib_selector=None,
            dry_run=True, fetch_search=_search,
        )
        diff = preview["items"][0]["diff"]
        added = [line for line in diff.splitlines() if line.startswith("+")]
        assert any("file = {papers/smith2024.pdf}" in line for line in added), diff

        update_bib(
            config_path=cp, home_dir=td, bib_selector=None,
            dry_run=False, fetch_search=_search,
        )
        assert "file = {papers/smith2024.pdf}" in Path(bib).read_text()


def test_tag_add_honors_relative_file_path_style() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bib, papers = _config(td, pdf_file_path_style="relative")
        pdf_abs = os.path.join(papers, "smith2024.pdf")
        Path(pdf_abs).write_bytes(b"%PDF-1.4\n")
        Path(bib).write_text(
            "% header\n"
            f"@article{{smith2024, title = {{X}}, author = {{S}}, "
            f"year = {{2024}}, file = {{{pdf_abs}}}}}\n"
        )

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2024", tags=["ml"],
        )

        assert result["status"] == "ok"
        text = Path(bib).read_text()
        assert "% header" in text
        assert "file = {papers/smith2024.pdf}" in text  # relativized
        assert pdf_abs not in text


def test_delete_preserves_comments_macros_and_other_entries() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_PRESERVE_BIB)

        result = delete_entry(bib_path=bib, citekey="smith2024", dry_run=False)

        assert result["status"] == "ok"
        text = Path(bib).read_text()
        assert "@article{smith2024," not in text  # deleted
        assert "@article{jones2023," in text  # kept
        assert "% library header comment" in text  # comment preserved
        assert "@string{acm" in text  # macro preserved
        assert "@preamble{" in text  # preamble preserved


def _bib(td: str) -> dict[str, Any]:
    bib = os.path.join(td, "lib.bib")
    papers = os.path.join(td, "papers")
    os.makedirs(papers, exist_ok=True)
    Path(bib).write_text(_PRESERVE_BIB)
    return {"name": "main", "path": bib, "papers_dir": papers, "default": True}


def test_insert_new_entry_preserves_comments_macros_and_preamble() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = _bib(td)

        result = add_record_with_bib(
            bib=bib,  # type: ignore[arg-type]
            record={"citekey": "new2025", "title": "Fresh Work",
                    "authors": ["New, N"], "year": 2025, "doi": "10.1000/new"},
            dry_run=False,
        )

        assert result["status"] == "ok" and result["action"] == "insert"
        text = Path(bib["path"]).read_text()
        assert "@article{new2025," in text  # new entry written
        assert "@article{smith2024," in text and "@article{jones2023," in text
        assert "% library header comment" in text
        assert "@string{acm" in text
        assert "@preamble{" in text


def test_update_existing_entry_preserves_comments_macros_and_preamble() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "lib.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        # Entry carries a DOI so the incoming record exact-matches and updates
        # in place rather than inserting a duplicate.
        Path(bib).write_text(
            "% library header comment\n"
            "@string{acm = {ACM}}\n"
            '@preamble{ "\\newcommand{\\noop}[1]{}" }\n'
            "\n"
            "@article{smith2024, title = {Deep Learning}, author = {Smith, John}, "
            "year = {2024}, doi = {10.1000/smith}}\n"
            "@article{jones2023, title = {Vision}, author = {Jones, K}, year = {2023}}\n"
        )
        bib_cfg = {"name": "main", "path": bib, "papers_dir": papers, "default": True}

        result = add_record_with_bib(
            bib=bib_cfg,  # type: ignore[arg-type]
            record={"title": "Deep Learning", "authors": ["Smith, John"],
                    "year": 2024, "doi": "10.1000/smith", "pdf_url": "https://x.test/p.pdf"},
            dry_run=False,
        )

        assert result["status"] == "ok" and result["action"] == "update"
        text = Path(bib).read_text()
        assert "@article{jones2023," in text  # untouched entry survives
        assert "% library header comment" in text
        assert "@string{acm" in text
        assert "@preamble{" in text


_CONFERENCE_BIB = (
    "@inproceedings{smith2020graph,\n"
    "  title = {Graph Networks},\n"
    "  author = {Smith, Jane and Doe, John},\n"
    "  booktitle = {Proceedings of NeurIPS},\n"
    "  year = {2020},\n"
    "  volume = {33},\n"
    "  pages = {1--12},\n"
    "  publisher = {Curran Associates},\n"
    "  editor = {Editor, Ed},\n"
    "  isbn = {978-1-234-56789-0}\n"
    "}\n"
)


def test_tag_add_preserves_fields_the_record_model_does_not_carry() -> None:
    """The mutated entry itself must survive, not just its neighbours.

    Regression for the 2026-07 audit's top finding: mutations regenerated the
    entry from NormalizedRecord, silently deleting volume/pages/publisher/
    editor/isbn and rewriting booktitle as journal, while reporting success.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_CONFERENCE_BIB)

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2020graph", tags=["ml"],
        )

        assert result["status"] == "ok" and result["changed"]
        text = Path(bib).read_text()
        assert "volume = {33}" in text
        assert "pages = {1--12}" in text
        assert "publisher = {Curran Associates}" in text
        assert "editor = {Editor, Ed}" in text
        assert "isbn = {978-1-234-56789-0}" in text
        assert "keywords = {ml}" in text


def test_tag_add_keeps_booktitle_and_entry_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_CONFERENCE_BIB)

        add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2020graph", tags=["ml"],
        )

        text = Path(bib).read_text()
        assert "booktitle = {Proceedings of NeurIPS}" in text
        assert "journal" not in text
        assert "@inproceedings{smith2020graph," in text


def test_readd_of_existing_entry_preserves_unmodelled_fields() -> None:
    """`pzi add` on a DOI already in the library takes the update branch of
    plan_bib_write, which must merge onto the on-disk entry."""
    with tempfile.TemporaryDirectory() as td:
        cp, bib, papers = _config(td)
        Path(bib).write_text(
            "@inproceedings{smith2020graph,\n"
            "  title = {Graph Networks},\n"
            "  author = {Smith, Jane},\n"
            "  doi = {10.1145/1234},\n"
            "  volume = {33},\n"
            "  publisher = {Curran Associates}\n"
            "}\n"
        )
        bib_cfg = {"name": "main", "path": bib, "papers_dir": papers, "default": True}

        result = add_record_with_bib(
            bib=bib_cfg,  # type: ignore[arg-type]
            record={"title": "Graph Networks", "authors": ["Smith, Jane"],
                    "doi": "10.1145/1234", "year": 2020},
            dry_run=False,
        )

        assert result["status"] == "ok" and result["action"] == "update"

        text = Path(bib).read_text()
        assert "volume = {33}" in text
        assert "publisher = {Curran Associates}" in text


def test_pdf_attach_preserves_fields_the_record_model_does_not_carry() -> None:
    """`pzi pdf attach` goes through _entry_with_pdf_fields, which regenerated
    the entry from the record and dropped everything the model omits."""
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_CONFERENCE_BIB)
        pdf = Path(td) / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 stub\n")

        result = attach_pdf(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="smith2020graph", source=str(pdf),
        )

        assert result["status"] == "ok"
        text = Path(bib).read_text()
        assert "volume = {33}" in text
        assert "pages = {1--12}" in text
        assert "publisher = {Curran Associates}" in text
        assert "booktitle = {Proceedings of NeurIPS}" in text
        assert "journal" not in text
        assert "@inproceedings{smith2020graph," in text


def test_merge_preserves_surviving_entrys_unmodelled_fields() -> None:
    """`pzi fix dedupe --merge` rebuilt the surviving entry from the merged
    record, so B's volume/pages/publisher vanished on merge."""
    with tempfile.TemporaryDirectory() as td:
        _cp, bib, _ = _config(td)
        Path(bib).write_text(
            _CONFERENCE_BIB
            + "@article{smith2020graphdup,\n"
            "  title = {Graph Networks},\n"
            "  author = {Smith, Jane and Doe, John},\n"
            "  doi = {10.1145/9999},\n"
            "  year = {2020}\n"
            "}\n"
        )

        result = merge_bib_entries(
            bib, citekey_a="smith2020graphdup", citekey_b="smith2020graph"
        )

        assert result["found"] is True
        text = Path(bib).read_text()
        assert "volume = {33}" in text
        assert "pages = {1--12}" in text
        assert "publisher = {Curran Associates}" in text
        assert "booktitle = {Proceedings of NeurIPS}" in text
        assert "doi = {10.1145/9999}" in text  # merged-in field still applied
        assert "@inproceedings{smith2020graph," in text


@pytest.mark.parametrize("path_style", ["absolute", "relative"])
@pytest.mark.parametrize(
    ("label", "file_value"),
    [
        ("zotero_absolute_triple", "Full Text PDF:{papers}/x.pdf:application/pdf"),
        ("jabref_empty_description", ":papers/x.pdf:PDF"),
        (
            "two_attachments",
            "Full Text PDF:papers/x.pdf:application/pdf;Snapshot:papers/x.html:text/html",
        ),
        ("bbt_default_two_bare_paths", "papers/x.pdf;papers/y.pdf"),
    ],
)
def test_a_composite_file_field_survives_an_unrelated_write(
    path_style: str, label: str, file_value: str
) -> None:
    """Tagging an entry must not rewrite an attachment it did not change.

    Zotero, JabRef and Better BibTeX all write `description:path:mimetype`,
    joined by `;` for several attachments. pzi read the whole value as a path,
    so any command that touched the entry — `tag add` included — prefixed the
    bib directory onto text that already contained one, producing something
    neither tool could read and silently dropping every attachment after the
    first.

    Driven through the real `add_tags` service, not a hand-written updater: the
    corruption happens when the entry is rebuilt *from the record*, so a test
    that mutates the entry directly never sees it.

    Parameterised over both `pdf_file_path_style` values on purpose: `relative`
    used to *cancel* the corruption arithmetically rather than avoid it, so a
    relative-only test would pass on broken code.

    Composites only. A *bare* path is pzi's own form and is deliberately
    rewritten to match the configured style — covered by the surrounding tests.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bib, papers = _config(td, pdf_file_path_style=path_style)
        for name in ("x.pdf", "y.pdf"):
            Path(os.path.join(papers, name)).write_bytes(b"%PDF-1.4\n")
        Path(os.path.join(papers, "x.html")).write_text("<html></html>")

        value = file_value.format(papers=papers)
        Path(bib).write_text(
            f"@article{{a2020,\n  title = {{A}},\n  file = {{{value}}}\n}}\n"
        )

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="a2020", tags=["ml"],
        )
        assert result["status"] == "ok"

        written = Path(bib).read_text()
        assert f"file = {{{value}}}" in written, (
            f"{label} under {path_style}: file field was rewritten\n{written}"
        )
        assert "ml" in written


#: Zotero / Better BibTeX export style: tab indent, trailing comma on every
#: field, one blank line between entries.
_TAB_STYLE_BIB = (
    "@article{key0,\n"
    "\ttitle = {Paper Zero},\n"
    "\tauthor = {Author, A.},\n"
    "\tyear = {2020},\n"
    "}\n"
    "\n"
    "@article{key1,\n"
    "\ttitle = {Paper One},\n"
    "\tauthor = {Other, B.},\n"
    "\tyear = {2021},\n"
    "}\n"
)

#: Two-space indent, no trailing comma, no blank line between entries — the
#: other convention in the wild, and the one this project's own library uses.
_COMPACT_STYLE_BIB = (
    "@article{key0,\n"
    "  title = {Paper Zero},\n"
    "  author = {Author, A.},\n"
    "  year = {2020}\n"
    "}\n"
    "@article{key1,\n"
    "  title = {Paper One},\n"
    "  author = {Other, B.},\n"
    "  year = {2021}\n"
    "}\n"
)

_UNTOUCHED_TAB_ENTRY = (
    "@article{key1,\n"
    "\ttitle = {Paper One},\n"
    "\tauthor = {Other, B.},\n"
    "\tyear = {2021},\n"
    "}\n"
)

_UNTOUCHED_COMPACT_ENTRY = (
    "@article{key1,\n"
    "  title = {Paper One},\n"
    "  author = {Other, B.},\n"
    "  year = {2021}\n"
    "}\n"
)


def _line_delta(before: str, after: str) -> tuple[list[str], list[str]]:
    """Lines removed from *before* and added to reach *after*."""
    import difflib

    diff = list(difflib.ndiff(before.splitlines(), after.splitlines()))
    removed = [line[2:] for line in diff if line.startswith("- ")]
    added = [line[2:] for line in diff if line.startswith("+ ")]
    return removed, added


def test_tag_add_changes_only_the_edited_entry_byte_for_byte() -> None:
    """A one-entry edit must be a one-entry diff, compared as bytes.

    Every other test in this module asserts substrings, and a whole-file
    reformat passes all of them: ``"@article{key1," in text`` stays true after
    the file has been re-indented, its trailing commas stripped and a blank line
    inserted between every entry. That is what happened — one ``tag add``
    against a 200-entry Zotero export changed 1800 lines — and it survived four
    reviews precisely because nothing compared bytes.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_TAB_STYLE_BIB)

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="key0", tags=["ml"],
        )

        assert result["status"] == "ok" and result["changed"]
        after = Path(bib).read_text()
        removed, added = _line_delta(_TAB_STYLE_BIB, after)
        assert removed == [], f"untouched lines were rewritten: {removed}"
        # The new field carries the file's own indent and trailing comma.
        assert added == ["\tkeywords = {ml},"], added
        assert _UNTOUCHED_TAB_ENTRY in after


def test_tag_add_does_not_insert_blank_lines_between_entries() -> None:
    """``block_separator`` defaults to a blank line, which no file has to want.

    On a compact library this is the whole blast radius: content lines are
    untouched and every entry boundary gains a line, which is why one first
    write against a 22k-entry library produced a 59.5k-line diff with zero
    content changes.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bib, _ = _config(td)
        Path(bib).write_text(_COMPACT_STYLE_BIB)

        result = add_tags(
            config_path=cp, home_dir=td, bib_selector=None,
            citekey="key0", tags=["ml"],
        )

        assert result["status"] == "ok" and result["changed"]
        after = Path(bib).read_text()
        assert "}\n\n@article{key1," not in after, "a blank line was inserted"
        assert _UNTOUCHED_COMPACT_ENTRY in after
        removed, added = _line_delta(_COMPACT_STYLE_BIB, after)
        # `year` was the last field and gains a comma; nothing else may change.
        assert removed == ["  year = {2020}"], removed
        assert added == ["  year = {2020},", "  keywords = {ml}"], added


def test_insert_preserves_the_layout_of_every_untouched_entry() -> None:
    """The insert path is a different writer from the update path.

    Both build their own ``BibtexFormat``; a fix wired into one of them is this
    codebase's dominant defect shape, so the guarantee is asserted through both.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "lib.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        Path(bib).write_text(_TAB_STYLE_BIB)

        result = add_record_with_bib(
            bib={"name": "main", "path": bib, "papers_dir": papers, "default": True},  # type: ignore[arg-type]
            record={"citekey": "new2025", "title": "Fresh Work",
                    "authors": ["New, N"], "year": 2025, "doi": "10.1000/new"},
            dry_run=False,
        )

        assert result["status"] == "ok" and result["action"] == "insert"
        after = Path(bib).read_text()
        removed, _added = _line_delta(_TAB_STYLE_BIB, after)
        assert removed == [], f"an existing entry was rewritten: {removed}"
        assert _UNTOUCHED_TAB_ENTRY in after


@pytest.mark.parametrize("path_style", ["absolute", "relative"])
def test_a_composite_file_field_resolves_to_its_pdf(path_style: str) -> None:
    """The point of parsing: the entry must know it has a PDF."""
    with tempfile.TemporaryDirectory() as td:
        cp, bib, papers = _config(td, pdf_file_path_style=path_style)
        pdf = os.path.join(papers, "x.pdf")
        Path(pdf).write_bytes(b"%PDF-1.4\n")
        Path(bib).write_text(
            "@article{a2020,\n  title = {A},\n"
            f"  file = {{Full Text PDF:{pdf}:application/pdf}}\n}}\n"
        )

        result = read_bib_file(bib)

        assert result["records"][0]["local_pdf_path"] == pdf
