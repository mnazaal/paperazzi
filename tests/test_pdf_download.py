from pathlib import Path

from pzi.pdf_download import fetch_and_store_pdf, store_pdf_source


def test_fetch_and_store_pdf_uses_injected_downloader_and_writer(tmp_path: Path) -> None:
    path, error = fetch_and_store_pdf(
        url="https://example.test/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"%PDF-from-network", "application/pdf"),
    )

    assert error is None
    assert path == str(tmp_path / "smith2024graph.pdf")
    assert (tmp_path / "smith2024graph.pdf").read_bytes() == b"%PDF-from-network"


def test_store_pdf_source_routes_urls_to_downloader(tmp_path: Path) -> None:
    path, error = store_pdf_source(
        source="https://example.test/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"%PDF-from-url", "application/pdf"),
    )

    assert error is None
    assert path == str(tmp_path / "smith2024graph.pdf")
    assert (tmp_path / "smith2024graph.pdf").read_bytes() == b"%PDF-from-url"
