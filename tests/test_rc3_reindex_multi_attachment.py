"""Reindex must leave a multi-attachment `file` field intact."""
from __future__ import annotations

import os

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


# --- a PDF shared between a single-attachment entry and a multi-attachment one

_ENTRY = (
    "@article{{{key},\n  title = {{{title}}},\n  author = {{{author}}},\n"
    "  year = {{2021}},\n  file = {{{field}}},\n}}\n"
)


def _file_lines(text: str) -> list[str]:
    # The rewrite may respell an absolute path as `~/...` (HOME is the tmp dir
    # in these tests); that is the writer's style, not a moved attachment.
    return [
        line.strip().replace("~/", f"{os.environ['HOME']}/")
        for line in text.splitlines()
        if "file =" in line
    ]


def _shared(tmp_path, monkeypatch, style, order, shared_is_second, name):
    """Entry A (single) and B (two attachments) both name *name*."""
    papers = tmp_path / "papers"
    papers.mkdir()
    shared, other = papers / name, papers / "y.pdf"
    shared.write_bytes(b"%PDF-1.4 shared")
    other.write_bytes(b"%PDF-1.4 other")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    spell = {
        "absolute": lambda f: f"{papers}/{f}",
        "home": lambda f: f"~/papers/{f}",
        "relative": lambda f: f"papers/{f}",
    }[style]
    # A owns the shared file alone. B lists it first, or after y.pdf.
    b_field = (
        f"{spell('y.pdf')};{spell(name)}"
        if shared_is_second
        else f"{spell(name)};{spell('y.pdf')}"
    )
    a = _ENTRY.format(
        key="oldA", title="Alpha study", author="Doe, John", field=spell(name)
    )
    b = _ENTRY.format(
        key="oldB", title="Beta study", author="Roe, Jane", field=b_field
    )
    bib = tmp_path / "t.bib"
    bib.write_text("\n".join([a, b] if order == "ab" else [b, a]))
    return bib, papers, shared, other


_MATRIX = pytest.mark.parametrize(
    ("style", "order", "shared_is_second"),
    [
        (style, order, second)
        for style in ("absolute", "home", "relative")
        for order in ("ab", "ba")
        for second in (False, True)
    ],
)


@_MATRIX
def test_citekey_pass_never_moves_a_pdf_a_multi_attachment_entry_lists(
    tmp_path, monkeypatch, style, order, shared_is_second
) -> None:
    bib, papers, shared, other = _shared(
        tmp_path, monkeypatch, style, order, shared_is_second, "x.pdf"
    )
    before = _file_lines(bib.read_text())
    result = reindex_service.reindex_library(
        bib_path=str(bib), papers_dir=str(papers), dry_run=False
    )
    assert shared.exists() and other.exists()
    assert sorted(p.name for p in papers.iterdir()) == ["x.pdf", "y.pdf"]
    assert _file_lines(bib.read_text()) == before
    assert not any(c.get("renamed_pdf") for c in result["changed"])
    assert not any("new_pdf" in c for c in result["changed"])
    assert {c["old_citekey"] for c in result["changed"]} == {"oldA", "oldB"}
    # The report names both entries and says why, and never claims a PDF was
    # kept for an entry whose reference it then broke.
    joined = "\n".join(result["errors"])
    assert "oldA" in joined and "oldB" in joined and "several attachments" in joined


@_MATRIX
def test_filename_pass_never_renames_a_pdf_a_multi_attachment_entry_lists(
    tmp_path, monkeypatch, style, order, shared_is_second
) -> None:
    name = "x textbraceleft.pdf"
    bib, papers, shared, other = _shared(
        tmp_path, monkeypatch, style, order, shared_is_second, name
    )
    before = bib.read_text()
    result = reindex_service.rename_files_to_policy(
        bib_path=str(bib), papers_dir=str(papers),
        pdf_filename_format="{{ citekey }}-new", dry_run=False,
    )
    assert result["changed"] == []
    assert sorted(p.name for p in papers.iterdir()) == sorted([name, "y.pdf"])
    assert bib.read_text() == before
    joined = "\n".join(result["errors"])
    assert "several attachments" in joined
    if not shared_is_second:
        # B is examined only when the shared file is its primary attachment.
        assert "oldB" in joined
    # A's own PDF is the shared one, so it is reported too rather than silent.
    assert "oldA" in joined
