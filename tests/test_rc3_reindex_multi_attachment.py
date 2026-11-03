"""Reindex must leave a multi-attachment `file` field intact."""
from __future__ import annotations

import pytest

from pzi import reindex_service


def _library(tmp_path, citekey="oldkey"):
    papers = tmp_path / "papers"
    papers.mkdir()
    x, y = papers / "x textbraceleft.pdf", papers / "y.pdf"
    x.write_bytes(b"%PDF-1.4\n")
    y.write_bytes(b"%PDF-1.4\n")
    field = f"{x};{y}"
    bib = tmp_path / "t.bib"
    bib.write_text(
        f"@article{{{citekey},\n  title = {{A Real Title}},\n  author = {{Doe, John}},\n"
        f"  year = {{2025}},\n  file = {{{field}}},\n}}\n"
    )
    return bib, papers, x, y, field


@pytest.mark.parametrize("dry_run", [True, False])
def test_citekey_pass_keeps_every_attachment(tmp_path, dry_run) -> None:
    bib, papers, x, y, field = _library(tmp_path)
    result = reindex_service.reindex_library(
        bib_path=str(bib), papers_dir=str(papers), dry_run=dry_run
    )
    assert result["changed"], "the citekey itself should still change"
    assert any("several attachments" in e for e in result["errors"]), result["errors"]
    assert x.exists() and y.exists()
    if not dry_run:
        text = bib.read_text()
        assert f"{x};{y}" in text and "oldkey" not in text.split("@article{")[1].split(",")[0]


@pytest.mark.parametrize("dry_run", [True, False])
def test_filename_pass_keeps_every_attachment(tmp_path, dry_run) -> None:
    bib, papers, x, y, field = _library(tmp_path)
    before = bib.read_text()
    result = reindex_service.rename_files_to_policy(
        bib_path=str(bib), papers_dir=str(papers),
        pdf_filename_format="{{ title }}", dry_run=dry_run,
    )
    assert result["changed"] == []
    assert any("several attachments" in e for e in result["errors"]), result["errors"]
    assert x.exists() and y.exists()
    assert bib.read_text() == before
