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

from pzi.add_service import add_record_with_bib
from pzi.bib_repository import merge_bib_entries
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
                    "doi": "10.1/foo", "year": 2024, "authors": ["Smith, J"],
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
                    "authors": ["New, N"], "year": 2025, "doi": "10.1/new"},
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
            "year = {2024}, doi = {10.1/smith}}\n"
            "@article{jones2023, title = {Vision}, author = {Jones, K}, year = {2023}}\n"
        )
        bib_cfg = {"name": "main", "path": bib, "papers_dir": papers, "default": True}

        result = add_record_with_bib(
            bib=bib_cfg,  # type: ignore[arg-type]
            record={"title": "Deep Learning", "authors": ["Smith, John"],
                    "year": 2024, "doi": "10.1/smith", "pdf_url": "https://x.test/p.pdf"},
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
