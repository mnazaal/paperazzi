from pathlib import Path

from pzi.add_service import (
    ensure_citekey_for_write,
    existing_citekeys,
    reuse_existing_pdf_fields_for_exact_match,
    reuse_orphan_pdf_for_planned_path,
)


def test_ensure_citekey_reuses_exact_match_key() -> None:
    record = {"doi": "10.1234/a", "title": "Paper"}
    existing = [{"doi": "10.1234/a", "citekey": "smith2024paper"}]

    result = ensure_citekey_for_write(record, existing)  # type: ignore[arg-type]

    assert result["citekey"] == "smith2024paper"


def test_ensure_citekey_suffixes_collision() -> None:
    record = {"citekey": "smith2024paper", "doi": "10.1234/b"}
    existing = [{"citekey": "smith2024paper", "doi": "10.1234/a"}]

    result = ensure_citekey_for_write(record, existing)  # type: ignore[arg-type]

    assert result["citekey"] == "smith2024paper2"


def test_existing_citekeys_ignores_blank_values() -> None:
    assert existing_citekeys(
        [{"citekey": "smith2024paper"}, {"citekey": " "}, {}]  # type: ignore[list-item]
    ) == {"smith2024paper"}


def test_reuse_existing_pdf_fields_for_exact_match() -> None:
    record = {"doi": "10.1234/a", "title": "Paper"}
    existing = [
        {
            "doi": "10.1234/a",
            "local_pdf_path": "/tmp/a.pdf",
            "pdf_url": "https://example.com/a.pdf",
        }
    ]

    result = reuse_existing_pdf_fields_for_exact_match(
        record, existing  # type: ignore[arg-type]
    )

    assert result["local_pdf_path"] == "/tmp/a.pdf"
    assert result["pdf_url"] == "https://example.com/a.pdf"


def test_reuse_orphan_pdf_for_planned_path(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    planned = papers / "smith2024paper.pdf"
    planned.write_bytes(b"%PDF-existing")
    record = {"citekey": "smith2024paper", "pdf_url": "https://example.com/a.pdf"}

    result = reuse_orphan_pdf_for_planned_path(
        record,  # type: ignore[arg-type]
        papers_dir=str(papers),
    )

    assert result["local_pdf_path"] == str(planned)
