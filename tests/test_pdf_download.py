import http.client
from pathlib import Path

import pytest

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
    read_limited reconciles and raises ValueError, which surfaces as an error
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


# ---------------------------------------------------------------------------
# Storing a PDF on a filesystem, and when storing fails
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch) -> None:
    """`temp_path` was bound *after* the write, so a write that raised skipped
    the `finally` that removes the temp file — one leaked `.pdf-*.tmp` per
    failure, in the user's papers directory."""
    from pzi import pdf_download

    def _boom(_fd, _data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pdf_download, "_write_all", _boom)

    with pytest.raises(OSError):
        pdf_download.write_pdf_bytes(
            data=b"%PDF-1.4\nx\n", papers_dir=str(tmp_path), citekey="k1"
        )

    assert list(tmp_path.glob(".pdf-*.tmp")) == []


def test_a_filesystem_without_hardlinks_still_stores_the_pdf(tmp_path, monkeypatch) -> None:
    """exFAT, many CIFS mounts and some FUSE filesystems reject `os.link`.

    Only `FileExistsError` was handled, so `EPERM`/`EOPNOTSUPP` aborted the
    whole store — `pzi add` could not attach a PDF at all on such a papers_dir.
    """
    import errno
    import os as _os

    from pzi import pdf_download

    real_replace = _os.replace

    def _no_hardlinks(_src, _dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(pdf_download.os, "link", _no_hardlinks)
    monkeypatch.setattr(pdf_download.os, "replace", real_replace)

    stored = pdf_download.write_pdf_bytes(
        data=b"%PDF-1.4\nx\n", papers_dir=str(tmp_path), citekey="k1"
    )

    assert Path(stored).read_bytes() == b"%PDF-1.4\nx\n"
    assert list(tmp_path.glob(".pdf-*.tmp")) == []
