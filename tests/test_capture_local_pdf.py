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


def test_local_pdf_base_record_reports_provider_failures(tmp_path: Path) -> None:
    """Provider failures here were swallowed, leaving `--strict-metadata` blind."""
    from pzi.capture_local_pdf import local_pdf_base_record

    def failing_search(_title, *, server_url):
        raise OSError("connection refused")

    errors: list[str] = []
    record = local_pdf_base_record(
        raw_value=str(tmp_path / "paper.pdf"),
        extracted={"title": "Graph Parsers"},
        server_url="http://127.0.0.1:1",
        fetch_search=failing_search,
        errors=errors,
    )

    # Best-effort record is still returned...
    assert record["title"] == "Graph Parsers"
    # ...but the failure is no longer invisible.
    assert any("connection refused" in e for e in errors)


def test_add_local_pdf_honors_strict_metadata_and_writes_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """`--strict-metadata` was silently ignored for local-PDF input.

    The gate must also run before the write: reporting the failure after the
    entry and its copied PDF had landed would leave exactly the unverified
    record the flag exists to prevent.
    """
    import pzi.capture_local_pdf as clp

    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")

    monkeypatch.setattr(
        clp, "extract_pdf_metadata", lambda _p: {"title": "Graph Parsers"}
    )

    def failing_search(_title, *, server_url):
        raise OSError("connection refused")

    def _unused_add_record(**_kw):  # pragma: no cover — must not be reached
        raise AssertionError("strict gate must run before anything is written")

    result = clp.add_local_pdf(
        bib={"name": "main", "path": str(bib_path),
             "papers_dir": str(papers_dir), "default": True},
        raw_value=str(source_pdf),
        record_overrides={},
        dry_run=False,
        server_url="http://127.0.0.1:1",
        fetch_search=failing_search,
        ensure_citekey=lambda record, existing, **_kw: record,
        add_record=_unused_add_record,
        strict_metadata=True,
    )

    assert result["status"] == "error"
    assert "strict-metadata" in result.get("message", "")
    # Nothing written: no entry, and the PDF was not copied into papers_dir.
    assert bib_path.read_text() == ""
    assert list(papers_dir.iterdir()) == []
