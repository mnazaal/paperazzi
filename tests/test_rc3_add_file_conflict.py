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


def _entry(path: Path) -> str:
    return (
        "@article{k2020,\n  author = {Smith, J},\n  title = {T paper},\n"
        f"  doi = {{10.1000/aaa}},\n  year = {{2020}},\n  file = {{{path}}},\n}}\n"
    )


def test_race_never_deletes_a_reused_pdf_outside_papers_dir(
    tmp_path: Path, monkeypatch
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    external = tmp_path / "Docs" / "mine.pdf"
    external.parent.mkdir()
    external.write_bytes(b"%PDF mine")
    other = papers / "other.pdf"
    other.write_bytes(b"%PDF other")
    bib_path = tmp_path / "lib.bib"
    bib_path.write_text(_entry(external))

    real = add_service.execute_write_plan

    def racing(path, plan, **kw):
        # Another writer repoints `file` between plan and lock.
        Path(path).write_text(_entry(other))
        return real(path, plan, **kw)

    monkeypatch.setattr(add_service, "execute_write_plan", racing)
    add_service.add_record_with_bib(
        bib={"name": "t", "path": str(bib_path), "papers_dir": str(papers)},
        record={
            "citekey": "k2020", "title": "T paper", "doi": "10.1000/aaa",
            "authors": ["Smith, J"], "year": 2020, "abstract": "abs",
        },
        dry_run=False, pdf_filename_format="{{ citekey }}",
    )
    assert external.read_bytes() == b"%PDF mine"


def test_cleanup_removes_only_files_inside_papers_dir(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    outside = tmp_path / "mine.pdf"
    outside.write_bytes(b"%PDF mine")
    created = papers / "new.pdf"
    created.write_bytes(b"%PDF new")
    bib = {"name": "t", "path": str(tmp_path / "lib.bib"), "papers_dir": str(papers)}
    (tmp_path / "lib.bib").write_text("")
    add_service._cleanup_new_pdf({"local_pdf_path": str(outside)}, set(), bib)
    add_service._cleanup_new_pdf({"local_pdf_path": str(created)}, set(), bib)
    assert outside.exists()
    assert not created.exists()
