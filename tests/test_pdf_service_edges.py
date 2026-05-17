"""Edge tests for pzi.pdf_service covering previously uncovered branches.

Covers missing lines in retry_pdf, attach_pdf, attach_pdf_bytes,
_attach_pdf_data, _store_pdf_source, and _entry_with_pdf_fields.
"""

from pathlib import Path

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
        lambda url, papers_dir, citekey, fetch_binary: (None, "network error"),
    )
    result = pdf_service.retry_pdf(
        config_path="/f",
        home_dir="/h",
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "error"
    assert result["message"] == "failed to fetch PDF"
    assert result["errors"] == ["network error"]


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
        lambda url, papers_dir, citekey, fetch_binary: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater: {
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
        lambda url, papers_dir, citekey, fetch_binary: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater: {
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
        lambda source, papers_dir, citekey, fetch_binary: (None, "source missing"),
    )
    result = pdf_service.attach_pdf(
        config_path="/f", home_dir="/h", bib_selector=None,
        citekey="smith2024", source="/does/not/exist.pdf",
    )
    assert result["status"] == "error"
    assert result["message"] == "failed to attach PDF"
    assert result["errors"] == ["source missing"]


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
        lambda source, papers_dir, citekey, fetch_binary: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater: {
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
        lambda source, papers_dir, citekey, fetch_binary: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater: {
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
        lambda source, papers_dir, citekey, fetch_binary: ("/p/smith2024.pdf", None),
    )
    monkeypatch.setattr(
        pdf_service, "update_bib_entry",
        lambda path, citekey, updater: {
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
        lambda path, citekey, updater: {
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
        lambda path, citekey, updater: {
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

    def fake_store_pdf_source(*, source, papers_dir, citekey, fetch_binary):
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
