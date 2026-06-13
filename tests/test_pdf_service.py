"""Edge tests for pzi.pdf_service covering previously uncovered branches.

Covers missing lines in retry_pdf, attach_pdf, attach_pdf_bytes,
_attach_pdf_data, _store_pdf_source, and _entry_with_pdf_fields.
"""

from pathlib import Path

import pytest

from pzi import pdf_service

# ---------------------------------------------------------------------------
# retry_pdf: successful bib resolution + citekey found + PDF URL present
# ---------------------------------------------------------------------------


def test_retry_pdf_bib_resolved_error(monkeypatch) -> None:
    """resolved is a list → error path."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ["bib load error", "another error"],
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "error"
    assert result["message"] == "could not resolve target bib"
    assert result["errors"] == ["bib load error", "another error"]


def test_retry_pdf_citekey_not_found(monkeypatch) -> None:
    """citekey not in entries → error path."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {"entries": [{"citekey": "other2024", "fields": {}}], "records": []},
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "error"
    assert result["message"] == "citekey not found"
    assert result["bib_name"] == "ml"


def test_retry_pdf_no_pdf_url(monkeypatch) -> None:
    """citekey found but no PDF URL in note → error path."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {"note": "some note"}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "extract_note_field",
        lambda note, label: None,
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "error"
    assert result["message"] == "no PDF URL on entry"
    assert result["bib_name"] == "ml"


def test_retry_pdf_fetch_failed(monkeypatch) -> None:
    """PDF URL found but fetch_and_store_pdf returns None → error path."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {"note": "PDF: http://x.com/p.pdf"}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "extract_note_field",
        lambda note, label: "http://x.com/p.pdf",
    )
    monkeypatch.setattr(
        pdf_service, "fetch_and_store_pdf",
        lambda **kw: (None, "network error"),
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "error"
    assert result["message"] == "failed to fetch PDF"
    assert result["errors"] == [
        "network error",
        "hint: open the actual PDF tab in your browser and click pzi again",
    ]


def test_retry_pdf_update_not_found(monkeypatch) -> None:
    """fetch succeeded but update_bib_entry returns found=False → error path."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {"note": "PDF: http://x.com/p.pdf"}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "extract_note_field",
        lambda note, label: "http://x.com/p.pdf",
    )
    monkeypatch.setattr(
        pdf_service, "fetch_and_store_pdf",
        lambda **kw: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": False,
            "entries": [],
            "entry": None,
            "record": None,
        },
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "error"
    assert result["message"] == "citekey disappeared"


def test_retry_pdf_removes_new_pdf_when_update_disappears(monkeypatch, tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    new_pdf = papers_dir / "smith2024.pdf"
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": str(papers_dir)}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {"note": "PDF: http://x.com/p.pdf"}}],
            "records": [{"citekey": "smith2024"}],
        },
    )
    monkeypatch.setattr(pdf_service, "extract_note_field", lambda note, label: "http://x.com/p.pdf")

    def fake_fetch(**kwargs):
        papers_dir.mkdir(parents=True, exist_ok=True)
        new_pdf.write_bytes(b"%PDF-new")
        return str(new_pdf), None

    monkeypatch.setattr(pdf_service, "fetch_and_store_pdf", fake_fetch)
    monkeypatch.setattr(
        pdf_service,
        "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": False,
            "entries": [],
            "entry": None,
            "record": None,
        },
    )

    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )

    assert result["status"] == "error"
    assert not new_pdf.exists()


def test_retry_pdf_success(monkeypatch) -> None:
    """Full happy path: resolved → fetch → update → success."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {"note": "PDF: http://x.com/p.pdf"}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "extract_note_field",
        lambda note, label: "http://x.com/p.pdf",
    )
    monkeypatch.setattr(
        pdf_service, "fetch_and_store_pdf",
        lambda **kw: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": True,
            "entries": [],
            "entry": {"entry_type": "article", "citekey": "smith2024", "fields": {}},
            "record": {"citekey": "smith2024"},
        },
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "ok"
    assert result["bib_name"] == "ml"
    assert result["local_pdf_path"] == "/p/smith2024.pdf"


# ---------------------------------------------------------------------------
# attach_pdf: successful resolution, citekey, store, update
# ---------------------------------------------------------------------------


def test_attach_pdf_bib_resolved_error(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ["bib load error"],
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="http://x.com/p.pdf",
    )
    assert result["status"] == "error"
    assert result["message"] == "could not resolve target bib"


def test_attach_pdf_citekey_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {"entries": [{"citekey": "other2024", "fields": {}}], "records": []},
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="http://x.com/p.pdf",
    )
    assert result["status"] == "error"
    assert result["message"] == "citekey not found"


def test_attach_pdf_store_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "_store_pdf_source",
        lambda **kw: (None, "source missing"),
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="/does/not/exist.pdf",
    )
    assert result["status"] == "error"
    assert result["message"] == "failed to attach PDF"
    assert result["errors"] == [
        "source missing",
        "hint: open the actual PDF tab in your browser and click pzi again",
    ]


def test_attach_pdf_update_disappeared(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "_store_pdf_source",
        lambda **kw: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": False,
            "entries": [],
            "entry": None,
            "record": None,
        },
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="/tmp/real.pdf",
    )
    assert result["status"] == "error"
    assert result["message"] == "citekey disappeared"


def test_attach_pdf_removes_new_pdf_when_update_disappears(monkeypatch, tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    new_pdf = papers_dir / "smith2024.pdf"
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": str(papers_dir)}),
    )
    monkeypatch.setattr(
        pdf_service,
        "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}}],
            "records": [{"citekey": "smith2024"}],
        },
    )

    def fake_store(**kwargs):
        papers_dir.mkdir(parents=True, exist_ok=True)
        new_pdf.write_bytes(b"%PDF-new")
        return str(new_pdf), None

    monkeypatch.setattr(pdf_service, "_store_pdf_source", fake_store)
    monkeypatch.setattr(
        pdf_service,
        "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": False,
            "entries": [],
            "entry": None,
            "record": None,
        },
    )

    result = pdf_service.attach_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
        source="/tmp/real.pdf",
    )

    assert result["status"] == "error"
    assert not new_pdf.exists()


def test_attach_pdf_success_http_source(monkeypatch) -> None:
    """HTTP source → url passed as pdf_url to _entry_with_pdf_fields."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "_store_pdf_source",
        lambda **kw: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": True,
            "entries": [],
            "entry": {"entry_type": "article", "citekey": "smith2024", "fields": {}},
            "record": {"citekey": "smith2024"},
        },
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="https://example.com/paper.pdf",
    )
    assert result["status"] == "ok"
    assert result["local_pdf_path"] == "/p/smith2024.pdf"
    assert result["source"] == "https://example.com/paper.pdf"


def test_attach_pdf_success_local_source(monkeypatch) -> None:
    """Local source → no pdf_url in note (not an http URL)."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}}],
            "records": [],
        },
    )
    monkeypatch.setattr(
        pdf_service, "_store_pdf_source",
        lambda **kw: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": True,
            "entries": [],
            "entry": {"entry_type": "article", "citekey": "smith2024", "fields": {}},
            "record": {"citekey": "smith2024"},
        },
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="/tmp/paper.pdf",
    )
    assert result["status"] == "ok"
    assert result["local_pdf_path"] == "/p/smith2024.pdf"


# ---------------------------------------------------------------------------
# attach_pdf_bytes: b64decode paths, PDF validation
# ---------------------------------------------------------------------------


def test_attach_pdf_bytes_bib_resolved_error(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ["error"],
    )
    result = pdf_service.attach_pdf_bytes(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", pdf_base64="bogus", source_url=None,
    )
    assert result["status"] == "error"
    assert result["message"] == "could not resolve target bib"


def test_attach_pdf_bytes_invalid_base64(monkeypatch) -> None:
    """base64 decode raises ValueError → invalid PDF payload."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    result = pdf_service.attach_pdf_bytes(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", pdf_base64="!!!not-valid-base64!!!", source_url=None,
    )
    assert result["status"] == "error"
    assert result["message"] == "invalid PDF payload"
    assert result["bib_name"] == "ml"


def test_attach_pdf_bytes_not_a_pdf(monkeypatch) -> None:
    """Valid base64 but decoded data doesn't start with %PDF-."""
    import base64
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    result = pdf_service.attach_pdf_bytes(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024",
        pdf_base64=base64.b64encode(b"not a pdf file").decode(),
        source_url=None,
    )
    assert result["status"] == "error"
    assert result["message"] == "invalid PDF payload"
    assert "not a PDF" in result["errors"][0]


def test_attach_pdf_bytes_valid_pdf_calls_attach_data(monkeypatch) -> None:
    """Valid PDF → delegates to _attach_pdf_data."""
    import base64
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    captured_kwargs = {}

    def fake_attach(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "status": "ok",
            "bib_name": kwargs["bib_name"],
            "citekey": kwargs["citekey"],
            "local_pdf_path": "/p/smith2024.pdf",
            "source_url": kwargs["source_url"],
            "message": "attached PDF bytes",
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(pdf_service, "_attach_pdf_data", fake_attach)

    pdf_data = b"%PDF-1.4 fake pdf content"
    result = pdf_service.attach_pdf_bytes(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024",
        pdf_base64=base64.b64encode(pdf_data).decode(),
        source_url="https://example.com/p.pdf",
    )
    assert result["status"] == "ok"
    assert captured_kwargs["citekey"] == "smith2024"
    assert captured_kwargs["data"] == pdf_data
    assert captured_kwargs["source_url"] == "https://example.com/p.pdf"


def test_attach_pdf_bytes_writes_absolute_file_field_by_default(tmp_path) -> None:
    import base64

    bib_path = tmp_path / "refs.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip(),
        encoding="utf-8",
    )
    bib_path.write_text("@article{smith2024,\n  title = {T}\n}\n", encoding="utf-8")

    result = pdf_service.attach_pdf_bytes(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        pdf_base64=base64.b64encode(b"%PDF-1.4 data").decode("ascii"),
        source_url=None,
    )

    assert result["status"] == "ok"
    assert f"file = {{{result['local_pdf_path']}}}" in bib_path.read_text(encoding="utf-8")


def test_attach_pdf_bytes_writes_relative_file_field_when_configured(tmp_path) -> None:
    import base64

    bib_path = tmp_path / "refs.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
pdf_file_path_style = "relative"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip(),
        encoding="utf-8",
    )
    bib_path.write_text("@article{smith2024,\n  title = {T}\n}\n", encoding="utf-8")

    result = pdf_service.attach_pdf_bytes(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        pdf_base64=base64.b64encode(b"%PDF-1.4 data").decode("ascii"),
        source_url=None,
    )

    assert result["status"] == "ok"
    assert "file = {papers/smith2024.pdf}" in bib_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _attach_pdf_data: citekey not found, update not found, success
# ---------------------------------------------------------------------------


def test_attach_pdf_data_citekey_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {"entries": [{"citekey": "other", "fields": {}}], "records": []},
    )
    result = pdf_service._attach_pdf_data(
        bib_name="ml",
        bib_path="/b",
        papers_dir="/p",
        citekey="smith2024",
        data=b"%PDF-1.4 content",
        source_url=None,
    )
    assert result["status"] == "error"
    assert result["message"] == "citekey not found"


def test_attach_pdf_data_update_disappeared(monkeypatch, tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}, "entry_type": "article"}],
            "records": [{"citekey": "smith2024"}],
        },
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": False,
            "entries": [],
            "entry": None,
            "record": None,
        },
    )
    result = pdf_service._attach_pdf_data(
        bib_name="ml",
        bib_path="/b",
        papers_dir=str(papers_dir),
        citekey="smith2024",
        data=b"%PDF-1.4 content",
        source_url=None,
    )
    assert result["status"] == "error"
    assert result["message"] == "citekey disappeared"
    assert not (papers_dir / "smith2024.pdf").exists()


def test_attach_pdf_data_success(monkeypatch, tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [{"citekey": "smith2024", "fields": {}, "entry_type": "article"}],
            "records": [{"citekey": "smith2024"}],
        },
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": True,
            "entries": [],
            "entry": {"entry_type": "article", "citekey": "smith2024", "fields": {}},
            "record": {"citekey": "smith2024"},
        },
    )
    result = pdf_service._attach_pdf_data(
        bib_name="ml",
        bib_path="/b",
        papers_dir=str(papers_dir),
        citekey="smith2024",
        data=b"%PDF-1.4 content",
        source_url="https://example.com/p.pdf",
    )
    assert result["status"] == "ok"
    assert result["local_pdf_path"] == str(papers_dir / "smith2024.pdf")
    assert result["source_url"] == "https://example.com/p.pdf"
    # Verify file was written
    assert (papers_dir / "smith2024.pdf").read_bytes() == b"%PDF-1.4 content"


# ---------------------------------------------------------------------------
# _store_pdf_source: compatibility wrapper around pzi.pdf.store_pdf_source
# ---------------------------------------------------------------------------


def test_store_pdf_source_wrapper_delegates_to_shared_pdf_helper(monkeypatch) -> None:
    """The old private helper stays as a thin compatibility wrapper."""
    seen = {}

    def fake_store_pdf_source(*, source, papers_dir, citekey, fetch_binary, **kwargs):
        seen.update(
            source=source,
            papers_dir=papers_dir,
            citekey=citekey,
            fetch_binary=fetch_binary,
        )
        return "/p/smith2024.pdf", None

    monkeypatch.setattr(pdf_service, "store_pdf_source", fake_store_pdf_source)
    fetch_binary = object()

    result = pdf_service._store_pdf_source(
        source="https://example.com/p.pdf",
        papers_dir="/p",
        citekey="smith2024",
        fetch_binary=fetch_binary,
    )

    assert result == ("/p/smith2024.pdf", None)
    assert seen == {
        "source": "https://example.com/p.pdf",
        "papers_dir": "/p",
        "citekey": "smith2024",
        "fetch_binary": fetch_binary,
    }


def test_store_pdf_source_local_missing() -> None:
    """Local file that does not exist."""
    result = pdf_service._store_pdf_source(
        source="/does/not/exist/file.pdf",
        papers_dir="/p",
        citekey="smith2024",
    )
    assert result[0] is None
    assert "not found" in result[1]


def test_store_pdf_source_local_not_pdf(monkeypatch, tmp_path: Path) -> None:
    """Local file exists but is not a PDF."""
    fpath = tmp_path / "source.txt"
    fpath.write_text("hello world")

    result = pdf_service._store_pdf_source(
        source=str(fpath),
        papers_dir=str(tmp_path / "papers"),
        citekey="smith2024",
    )
    assert result[0] is None
    assert "not a valid PDF" in result[1]


def test_store_pdf_source_local_success(monkeypatch, tmp_path: Path) -> None:
    """Local file is valid PDF → copied to papers_dir."""
    fpath = tmp_path / "source.pdf"
    fpath.write_bytes(b"%PDF-1.4 content goes here")
    papers_dir = tmp_path / "papers"

    result = pdf_service._store_pdf_source(
        source=str(fpath),
        papers_dir=str(papers_dir),
        citekey="smith2024",
    )
    assert result[0] == str(papers_dir / "smith2024.pdf")
    assert result[1] is None
    assert (papers_dir / "smith2024.pdf").read_bytes() == b"%PDF-1.4 content goes here"


# ---------------------------------------------------------------------------
# _entry_with_pdf_fields: pdf_url None vs not None
# ---------------------------------------------------------------------------


def test_entry_with_pdf_fields_no_pdf_url() -> None:
    """pdf_url is None → should not be in updated record."""
    entry: pdf_service.BibtexEntry = {
        "entry_type": "article",
        "citekey": "smith2024",
        "fields": {"title": "Test"},
    }
    record: pdf_service.NormalizedRecord = {
        "citekey": "smith2024",
        "title": "Test",
    }

    # We need the lambda in attach_pdf to test this via the updater callback.
    # The _entry_with_pdf_fields function is called as:
    #   lambda entry, record: _entry_with_pdf_fields(entry, cast(NormalizedRecord, dict(record)),
    #       local_pdf_path=local_pdf_path, pdf_url=source if source.startswith(...) else None)
    #
    # When pdf_url is None, it should NOT add pdf_url to the record.
    entry = {"entry_type": "article", "citekey": "smith2024", "fields": {}}
    record = {"citekey": "smith2024", "title": "Test"}
    result_entry = pdf_service._entry_with_pdf_fields(
        entry,
        record,
        local_pdf_path="/p/smith2024.pdf",
        pdf_url=None,
    )
    assert result_entry["entry_type"] == "article"
    assert result_entry["citekey"] == "smith2024"
    # pdf_url should NOT be in the note field since we passed None
    note = result_entry["fields"].get("note", "")
    assert "PDF: " not in note or note == ""


def test_entry_with_pdf_fields_with_pdf_url() -> None:
    """pdf_url is provided → should be included in updated record."""
    entry: pdf_service.BibtexEntry = {
        "entry_type": "article",
        "citekey": "smith2024",
        "fields": {"title": "Test"},
    }
    record: pdf_service.NormalizedRecord = {
        "citekey": "smith2024",
        "title": "Test",
    }

    result_entry = pdf_service._entry_with_pdf_fields(
        entry,
        record,
        local_pdf_path="/p/smith2024.pdf",
        pdf_url="https://example.com/p.pdf",
    )
    assert result_entry["entry_type"] == "article"
    assert result_entry["citekey"] == "smith2024"
    # local_pdf_path should be set as file field
    assert result_entry["fields"]["file"] == "/p/smith2024.pdf"
    # pdf_url should be in the note field
    assert "PDF: https://example.com/p.pdf" in result_entry["fields"].get("note", "")


def test_entry_with_pdf_fields_preserves_existing_note() -> None:
    """When record already has a note, it's preserved alongside PDF fields."""
    entry: pdf_service.BibtexEntry = {
        "entry_type": "article",
        "citekey": "smith2024",
        "fields": {"title": "Test", "note": "original note"},
    }
    record: pdf_service.NormalizedRecord = {
        "citekey": "smith2024",
        "title": "Test",
        "note": "original note",
    }

    result_entry = pdf_service._entry_with_pdf_fields(
        entry,
        record,
        local_pdf_path="/p/smith2024.pdf",
        pdf_url="https://example.com/p.pdf",
    )
    note = result_entry["fields"].get("note", "")
    assert "original note" in note
    assert "PDF: https://example.com/p.pdf" in note
    assert "file" in result_entry["fields"]

# ── from test_pdf_text_metadata.py ──


def test_extract_doi_from_text_normalizes_first_match():
    text = "See DOI: 10.1145/3368089.3409746 for details"

    assert pdf_service.extract_doi_from_text(text) == "10.1145/3368089.3409746"


def test_extract_doi_from_text_stops_before_line_break():
    text = "doi 10.1234/foo.\nbar"

    assert pdf_service.extract_doi_from_text(text) == "10.1234/foo"


def test_extract_title_from_text_skips_metadata_lines():
    text = """
    DOI: 10.1234/example
    Abstract
    Real Paper Title With Enough Words
    Introduction
    """

    assert pdf_service.extract_title_from_text(text) == "Real Paper Title With Enough Words"


def test_pdf_metadata_from_text_returns_empty_fields_for_blank_text():
    assert pdf_service.pdf_metadata_from_text("   ") == {
        "doi": None,
        "title": None,
        "text_sample": None,
    }


def test_pdf_metadata_from_text_limits_text_sample():
    title = "Real Paper Title With Enough Words"
    text = f"{title}\n" + ("x" * 2500)

    result = pdf_service.pdf_metadata_from_text(text)

    assert result["doi"] is None
    assert result["title"] == title
    assert result["text_sample"] == text[:2000]

# ── from test_pdf_metadata.py ──

"""Tests for PDF metadata extraction."""
def _make_pdf_with_text(tmp_path: Path, text: str) -> Path:
    """Create a minimal PDF with embedded text using pypdf."""
    try:
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    # Add text via content stream (minimal approach)
    content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET".encode()
    page["/Contents"] = writer._add_object(
        DictionaryObject({NameObject("/Length"): len(content)})
    )
    # Note: this is a simplified mock; real text extraction may fail
    # We'll use a different approach - create a real PDF with text

    path = tmp_path / "test.pdf"
    with path.open("wb") as f:
        writer.write(f)
    return path


def test_extract_pdf_metadata_missing_file(tmp_path: Path) -> None:
    result = pdf_service.extract_pdf_metadata(str(tmp_path / "nonexistent.pdf"))
    assert result == {"doi": None, "title": None, "text_sample": None}


def test_extract_doi_from_text_finds_first() -> None:
    text = "Some paper text. DOI: 10.1145/3368089.3409741 More text."
    assert pdf_service.extract_doi_from_text(text) == "10.1145/3368089.3409741"


def test_extract_doi_from_text_no_match() -> None:
    assert pdf_service.extract_doi_from_text("No doi here") is None


def test_extract_doi_from_text_normalizes_whitespace() -> None:
    # Preprocessing removes spaces from matched candidate
    text = "DOI: 10.1145/3368089.3409741"
    assert pdf_service.extract_doi_from_text(text) == "10.1145/3368089.3409741"


def test_extract_title_from_text_skips_junk() -> None:
    text = "DOI: 10.1/foo\nJournal of Testing\n\nReal Paper Title Here\nAbstract..."
    assert pdf_service.extract_title_from_text(text) == "Real Paper Title Here"


def test_extract_title_from_text_too_short_skipped() -> None:
    text = "DOI: 10.1/foo\nHi\nShort\nActual Title That Is Long Enough"
    assert pdf_service.extract_title_from_text(text) == "Actual Title That Is Long Enough"


def test_extract_title_from_text_none() -> None:
    assert pdf_service.extract_title_from_text("DOI\nhttp\n© 2024") is None


def test_extract_pdf_metadata_real_pdf(tmp_path: Path) -> None:
    """Test with a real PDF created via pypdf if available."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)

    path = tmp_path / "real_test.pdf"
    with path.open("wb") as f:
        writer.write(f)

    result = pdf_service.extract_pdf_metadata(str(path))
    # Blank page has no text; just verify it doesn't crash
    assert result["doi"] is None
    assert result["title"] is None


# ---------------------------------------------------------------------------
# retry_failed_pdfs
# ---------------------------------------------------------------------------


def test_retry_failed_pdfs_bib_resolved_error(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ["bib load error"],
    )
    result = pdf_service.retry_failed_pdfs(
        config_path="/f", home_dir="/h", bib_selector=None,
    )
    assert result["status"] == "error"
    assert result["message"] == "could not resolve target bib"


def test_retry_failed_pdfs_all_skipped(monkeypatch) -> None:
    """All entries already have PDF or no URL → skipped."""
    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: ({"bibs": []}, {"name": "ml", "path": "/b", "papers_dir": "/p"}),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [
                {"citekey": "smith2024", "fields": {"note": "PDF: http://x.com/p.pdf"}},
                {"citekey": "jones2024", "fields": {"note": "some note"}},
            ],
            "records": [
                {"citekey": "smith2024", "local_pdf_path": "/p/smith2024.pdf"},
                {"citekey": "jones2024"},
            ],
        },
    )

    def fake_exists(self):
        return str(self) == "/p/smith2024.pdf"

    monkeypatch.setattr(pdf_service.Path, "exists", fake_exists)

    result = pdf_service.retry_failed_pdfs(
        config_path="/f", home_dir="/h", bib_selector=None,
    )
    assert result["status"] == "ok"
    assert result["total"] == 0
    assert result["skipped_already_has_pdf"] == 1
    assert result["skipped_no_url"] == 1


def test_retry_failed_pdfs_some_retried(
    monkeypatch, tmp_path: Path,
) -> None:
    """Entries need retry → fetch and update some succeed, some fail."""
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        pdf_service, "load_and_resolve_bib",
        lambda **kw: (
            {"bibs": []},
            {"name": "ml", "path": str(tmp_path / "main.bib"), "papers_dir": str(papers_dir)},
        ),
    )
    monkeypatch.setattr(
        pdf_service, "read_bib_file",
        lambda path: {
            "entries": [
                {"citekey": "smith2024", "fields": {"note": "PDF: http://x.com/p.pdf"}},
                {"citekey": "jones2024", "fields": {"note": "PDF: http://y.com/q.pdf"}},
            ],
            "records": [
                {"citekey": "smith2024"},
                {"citekey": "jones2024"},
            ],
        },
    )
    monkeypatch.setattr(pdf_service, "extract_note_field", lambda note, label: "http://x.com/p.pdf")
    monkeypatch.setattr(pdf_service, "_snapshot_pdf_paths", lambda dir: set())

    call_count = {"count": 0}

    def fake_fetch(**kw):
        call_count["count"] += 1
        # First call succeeds, second fails
        if call_count["count"] == 1:
            pdf = papers_dir / "smith2024.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            return str(pdf), None
        return None, "network error"

    monkeypatch.setattr(pdf_service, "fetch_and_store_pdf", fake_fetch)
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater, **kwargs: {
            "found": True,
            "entries": [],
            "entry": {"entry_type": "article", "citekey": citekey, "fields": {}},
            "record": {"citekey": citekey},
        },
    )

    result = pdf_service.retry_failed_pdfs(
        config_path="/f", home_dir="/h", bib_selector=None,
    )
    assert result["status"] == "ok"
    assert result["total"] == 2
    assert result["succeeded"] == 1
    assert result["skipped_already_has_pdf"] == 0
    assert result["skipped_no_url"] == 0
    assert len(result["failures"]) == 1
    assert result["failures"][0]["citekey"] == "jones2024"
