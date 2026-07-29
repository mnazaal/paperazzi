import http.client
from pathlib import Path

import pzi.pdf_download as pdf_download
from pzi.pdf_download import fetch_and_store_pdf


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


def test_fetch_and_store_pdf_ezproxy_rewrites_url_and_trusts_host(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str | None] = {}

    def fake_fetch_binary(url, *, allow_host=None):
        seen["url"] = url
        seen["allow_host"] = allow_host
        return b"%PDF-ezproxy", "application/pdf"

    monkeypatch.setattr(pdf_download, "_fetch_binary", fake_fetch_binary)

    path, error = fetch_and_store_pdf(
        url="https://doi.org/10.1/x",
        papers_dir=str(tmp_path),
        citekey="smith2024",
        ezproxy_host="proxy.lib.university.edu",
    )

    assert error is None
    # Host is rewritten through the proxy, and that exact host is trusted.
    assert seen["url"] == "https://doi-org.proxy.lib.university.edu/10.1/x"
    assert seen["allow_host"] == "doi-org.proxy.lib.university.edu"


def test_store_pdf_source_routes_urls_to_downloader(tmp_path: Path) -> None:
    from pzi.pdf_service import _store_pdf_source as store_pdf_source

    path, error = store_pdf_source(
        source="https://example.test/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"%PDF-from-url", "application/pdf"),
    )

    assert error is None
    assert path == str(tmp_path / "smith2024graph.pdf")
    assert (tmp_path / "smith2024graph.pdf").read_bytes() == b"%PDF-from-url"


def test_fetch_and_store_reports_incomplete_read_instead_of_raising(tmp_path) -> None:
    """A chunked body cut short raises http.client.IncompleteRead, which is not
    an OSError — it used to escape as a traceback out of `pzi add`."""

    def truncated_fetcher(url):
        raise http.client.IncompleteRead(b"%PDF-1.4 partial", 900)

    path, error = fetch_and_store_pdf(
        url="https://example.org/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024",
        fetch_binary=truncated_fetcher,
    )

    assert path is None
    assert error is not None and "failed to download PDF" in error
    assert list(tmp_path.iterdir()) == []


def test_fetch_and_store_reports_content_length_truncation(tmp_path) -> None:
    """The silent case: a body with a known Content-Length that stops early.
    _read_limited reconciles and raises ValueError, which surfaces as an error
    rather than a half-written PDF in the library."""

    def short_fetcher(url):
        raise ValueError("truncated response body: got 40 of 900 bytes")

    path, error = fetch_and_store_pdf(
        url="https://example.org/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024",
        fetch_binary=short_fetcher,
    )

    assert path is None
    assert error is not None and "truncated response body" in error
    assert list(tmp_path.iterdir()) == []
