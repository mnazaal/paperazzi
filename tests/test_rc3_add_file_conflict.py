"""A download that loses a `file` conflict does not stay behind unreferenced."""
from __future__ import annotations

from pathlib import Path

from pzi import add_service

_PDF = (b"%PDF-1.4 A", "application/pdf")


def test_replan_after_file_conflict_removes_the_unreferenced_download(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    bib_path = tmp_path / "lib.bib"
    bib_path.write_text(
        "@article{k2020,\n  author = {Smith, J},\n  title = {T},\n"
        "  doi = {10.1000/aaa},\n  year = {2020},\n}\n"
    )
    (papers / "other.pdf").write_bytes(b"%PDF-other")

    def fetch(_url: str):
        text = bib_path.read_text()
        bib_path.write_text(
            text.replace("  year", f"  file = {{{papers}/other.pdf}},\n  year")
        )
        return _PDF

    add_service.add_record_with_bib(
        bib={"name": "t", "path": str(bib_path), "papers_dir": str(papers)},
        record={
            "citekey": "k2020", "title": "T", "doi": "10.1000/aaa",
            "authors": ["Smith, J"], "year": 2020, "abstract": "abs",
            "pdf_url": "http://example.com/a.pdf",
        },
        dry_run=False, fetch_binary=fetch, pdf_filename_format="{{ citekey }}",
    )
    assert sorted(p.name for p in papers.iterdir()) == ["other.pdf"]
    assert "k2020.pdf" not in bib_path.read_text()
