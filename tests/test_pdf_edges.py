"""Edge tests for pdf.py uncovered lines (104: content type checks, 157->166: fetch_unpaywall)."""

from pathlib import Path

from pzi.pdf import (
    _is_pdf_content_type,
    copy_pdf_to_papers_dir,
    fetch_and_store_pdf,
    fetch_unpaywall_pdf_url,
    is_pdf_bytes,
    plan_pdf_path,
    write_pdf_bytes,
)

# ── _is_pdf_content_type ─────────────────────────────────────────

def test_is_pdf_content_type_pdf() -> None:
    assert _is_pdf_content_type("application/pdf") is True
    assert _is_pdf_content_type("Application/PDF") is True


def test_is_pdf_content_type_html() -> None:
    assert _is_pdf_content_type("text/html") is False
    assert _is_pdf_content_type("text/html; charset=utf-8") is False


def test_is_pdf_content_type_json() -> None:
    assert _is_pdf_content_type("application/json") is False


def test_is_pdf_content_type_plain() -> None:
    assert _is_pdf_content_type("text/plain") is False


def test_is_pdf_content_type_none() -> None:
    assert _is_pdf_content_type(None) is None


def test_is_pdf_content_type_ambiguous() -> None:
    """Octet-stream is ambiguous — return None."""
    assert _is_pdf_content_type("application/octet-stream") is None
    assert _is_pdf_content_type("binary") is None
    assert _is_pdf_content_type("") is None


# ── is_pdf_bytes ─────────────────────────────────────────────────

def test_is_pdf_bytes_valid() -> None:
    assert is_pdf_bytes(b"%PDF-1.4 content") is True


def test_is_pdf_bytes_invalid() -> None:
    assert is_pdf_bytes(b"Not a PDF") is False
    assert is_pdf_bytes(b"") is False


# ── plan_pdf_path / write_pdf_bytes ──────────────────────────────

def test_plan_pdf_path() -> None:
    result = plan_pdf_path(papers_dir="/papers", citekey="smith2024")
    assert result == "/papers/smith2024.pdf"


def test_write_pdf_bytes(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    path = write_pdf_bytes(data=b"%PDF-test", papers_dir=str(papers_dir), citekey="test2024")
    assert Path(path).exists()
    assert Path(path).read_bytes() == b"%PDF-test"
    assert "test2024.pdf" in path


# ── copy_pdf_to_papers_dir ───────────────────────────────────────

def test_copy_pdf_source_not_found(tmp_path: Path) -> None:
    path, err = copy_pdf_to_papers_dir(
        source_path=str(tmp_path / "nonexistent.pdf"),
        papers_dir=str(tmp_path / "papers"),
        citekey="x",
    )
    assert path is None
    assert "not found" in err


def test_copy_pdf_not_valid_pdf(tmp_path: Path) -> None:
    src = tmp_path / "notpdf"
    src.write_bytes(b"hello world")
    path, err = copy_pdf_to_papers_dir(
        source_path=str(src),
        papers_dir=str(tmp_path / "papers"),
        citekey="x",
    )
    assert path is None
    assert "not a valid PDF" in err


def test_copy_pdf_success(tmp_path: Path) -> None:
    src = tmp_path / "real.pdf"
    src.write_bytes(b"%PDF-1.4 real")
    papers = tmp_path / "papers"
    path, err = copy_pdf_to_papers_dir(
        source_path=str(src),
        papers_dir=str(papers),
        citekey="smith2024",
    )
    assert err is None
    assert path is not None
    assert "smith2024.pdf" in path
    assert Path(path).read_bytes() == b"%PDF-1.4 real"


# ── fetch_and_store_pdf ──────────────────────────────────────────

def test_fetch_and_store_pdf_success(tmp_path: Path) -> None:
    def fake_fetch(url: str) -> tuple[bytes, str | None]:
        return b"%PDF-remote", "application/pdf"

    path, err = fetch_and_store_pdf(
        url="https://example.com/a.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="remote2024",
        fetch_binary=fake_fetch,
    )
    assert err is None
    assert path is not None
    assert "remote2024.pdf" in path


def test_fetch_and_store_pdf_not_pdf(tmp_path: Path) -> None:
    def fake_fetch(url: str) -> tuple[bytes, str | None]:
        return b"<html>...</html>", "text/html"

    path, err = fetch_and_store_pdf(
        url="https://example.com/notpdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="nope",
        fetch_binary=fake_fetch,
    )
    assert path is None
    assert "not a PDF" in err


def test_fetch_and_store_pdf_exception(tmp_path: Path) -> None:
    def fake_fetch(url: str) -> tuple[bytes, str | None]:
        raise OSError("network down")

    path, err = fetch_and_store_pdf(
        url="https://example.com/a.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="fail2024",
        fetch_binary=fake_fetch,
    )
    assert path is None
    assert "network down" in err


# ── fetch_unpaywall_pdf_url ──────────────────────────────────────

def test_fetch_unpaywall_success(monkeypatch) -> None:
    import json as _json

    def fake_fetch(url: str) -> str:
        return _json.dumps({
            "best_oa_location": {"url_for_pdf": "https://example.com/oa.pdf"},
        })

    result = fetch_unpaywall_pdf_url("10.1/test", email="x@x.com", fetch_text=fake_fetch)
    assert result == "https://example.com/oa.pdf"


def test_fetch_unpaywall_no_pdf(monkeypatch) -> None:
    def fake_fetch(url: str) -> str:
        return "{}"

    result = fetch_unpaywall_pdf_url("10.1/test", email="x@x.com", fetch_text=fake_fetch)
    assert result is None


def test_fetch_unpaywall_exception(monkeypatch) -> None:
    def fake_fetch(url: str) -> str:
        raise RuntimeError("api down")

    result = fetch_unpaywall_pdf_url("10.1/test", email="x@x.com", fetch_text=fake_fetch)
    assert result is None


def test_fetch_unpaywall_non_string_pdf(monkeypatch) -> None:
    import json as _json

    def fake_fetch(url: str) -> str:
        return _json.dumps({"best_oa_location": {"url_for_pdf": 123}})

    result = fetch_unpaywall_pdf_url("10.1/test", email="x@x.com", fetch_text=fake_fetch)
    # url_for_pdf is not a string → returns None
    assert result is None
