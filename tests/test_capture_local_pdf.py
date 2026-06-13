from pathlib import Path

from pzi.capture_local_pdf import attach_pdf_if_available


def test_attach_pdf_if_available_copies_local_pdf_candidate(tmp_path: Path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
    papers_dir = tmp_path / "papers"

    record, warnings = attach_pdf_if_available(
        record={"citekey": "smith2024paper", "pdf_url": str(source_pdf)},
        papers_dir=str(papers_dir),
        dry_run=False,
        fetch_binary=None,
    )

    assert warnings == []
    local_pdf_path = record["local_pdf_path"]
    assert isinstance(local_pdf_path, str)
    assert Path(local_pdf_path).exists()
    assert Path(local_pdf_path).read_bytes() == source_pdf.read_bytes()
