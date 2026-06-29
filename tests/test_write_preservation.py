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
from pzi.bib_service import delete_entry
from pzi.tag_service import add_tags, remove_tags

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
