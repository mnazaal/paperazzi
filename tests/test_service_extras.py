import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from pzi.add_service import add_input_to_bib, add_record_to_bib
from pzi.bib_service import list_bibs, set_default_bib
from pzi.doctor_service import doctor_check
from pzi.pdf_service import attach_pdf, attach_pdf_bytes, retry_pdf
from pzi.tag_service import add_tags, list_tags, remove_tags
from pzi.update_service import update_bib


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _write_config(tmp_path: Path, bib_path: Path, *, extra: str = "") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
{extra}
""".strip()
    )
    return config_path


def _seed_bib_with_record(config_path: Path, tmp_path: Path, bib_path: Path) -> None:
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
            "authors": ["Smith, Jane"],
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )
    assert bib_path.exists()


def test_list_bibs_returns_configured_bibs(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)

    result = list_bibs(config_path=str(config_path), home_dir=str(tmp_path))
    assert result["status"] == "ok"
    assert len(result["bibs"]) == 1
    assert result["bibs"][0]["name"] == "ml"
    assert result["bibs"][0]["default"] is True


def test_list_tags_across_entries(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
            "tags": ["ml", "graphs"],
        },
        bib_selector=None,
        dry_run=False,
    )
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "doe2024vision",
            "title": "Vision",
            "doi": "10.1/bar",
            "tags": ["cv"],
        },
        bib_selector=None,
        dry_run=False,
    )

    result = list_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert result["status"] == "ok"
    assert result["tags"] == ["cv", "graphs", "ml"]


def test_add_remove_tags_round_trip(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    add_result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["ml", "graphs"],
    )
    assert add_result["status"] == "ok"
    assert add_result["tags"] == ["graphs", "ml"]
    assert "keywords = {graphs, ml}" in bib_path.read_text()

    remove_result = remove_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["ml"],
    )
    assert remove_result["status"] == "ok"
    assert remove_result["tags"] == ["graphs"]


def test_add_tags_dry_run_does_not_mutate(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)
    before = bib_path.read_text()

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["ml"],
        dry_run=True,
    )
    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert bib_path.read_text() == before


def test_add_tags_unknown_citekey_returns_error(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="missing",
        tags=["ml"],
    )
    assert result["status"] == "error"
    assert "missing" in result["errors"][0]


def test_retry_pdf_uses_note_pdf_url(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "a2024",
            "title": "A",
            "doi": "10.1/a",
            "pdf_url": "https://example.com/a.pdf",
        },
        bib_selector=None,
        dry_run=False,
    )

    calls: list[str] = []

    def fake_fetch(url: str) -> tuple[bytes, str | None]:
        calls.append(url)
        return b"%PDF-1.4 ok", "application/pdf"

    result = retry_pdf(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="a2024",
        fetch_binary=fake_fetch,
    )
    assert result["status"] == "ok"
    assert calls == ["https://example.com/a.pdf"]
    assert result["local_pdf_path"] is not None
    assert Path(result["local_pdf_path"]).exists()


def test_retry_pdf_no_url_errors(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    result = retry_pdf(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"", None),
    )
    assert result["status"] == "error"
    assert "no PDF URL" in result["message"]


def test_attach_pdf_from_local_path_updates_entry(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 local")

    result = attach_pdf(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        source=str(pdf_path),
    )

    assert result["status"] == "ok"
    assert result["local_pdf_path"] is not None
    assert Path(result["local_pdf_path"]).exists()
    text = bib_path.read_text()
    assert "file = {" in text
    assert "note = " not in text or "PDF:" not in text


def test_attach_pdf_from_url_updates_pdf_url_note(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    result = attach_pdf(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        source="https://example.com/paper.pdf",
        fetch_binary=lambda url: (b"%PDF-1.4 remote", "application/pdf"),
    )

    assert result["status"] == "ok"
    text = bib_path.read_text()
    assert "file = {" in text
    assert "PDF: https://example.com/paper.pdf" in text


def test_attach_pdf_bytes_updates_entry(tmp_path: Path) -> None:
    import base64

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    result = attach_pdf_bytes(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        pdf_base64=base64.b64encode(b"%PDF-1.4 bytes").decode("ascii"),
        source_url="https://example.com/in-browser.pdf",
    )

    assert result["status"] == "ok"
    text = bib_path.read_text()
    assert "file = {" in text
    assert "PDF: https://example.com/in-browser.pdf" in text


def test_doctor_check_reports_config_and_paths(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        translation_probe=lambda url: True,
    )
    assert result["status"] == "ok"
    assert result["config_ok"] is True
    assert result["bibs"][0]["path_exists"] is True
    assert result["translation_server_reachable"] is True


def test_doctor_check_default_probe_marks_local_server_reachable(tmp_path: Path) -> None:
    def do_get(request: BaseHTTPRequestHandler) -> None:
        request.send_response(404)
        request.end_headers()

    Handler = type(
        "Handler",
        (BaseHTTPRequestHandler,),
        {"do_GET": do_get, "log_message": lambda request, format, *args: None},
    )

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bib_path = tmp_path / "ml.bib"
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f"""
translation_server_url = "http://127.0.0.1:{port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
        )
        _seed_bib_with_record(config_path, tmp_path, bib_path)

        result = doctor_check(config_path=str(config_path), home_dir=str(tmp_path))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["status"] == "ok"
    assert result["translation_server_reachable"] is True
    assert result["translation_probe_error"] is None


def test_doctor_check_default_probe_marks_unreachable_server_false(tmp_path: Path) -> None:
    port = _free_port()
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    _seed_bib_with_record(config_path, tmp_path, bib_path)

    result = doctor_check(config_path=str(config_path), home_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["translation_server_reachable"] is False


def test_doctor_check_handles_bad_config(tmp_path: Path) -> None:
    bad_config = tmp_path / "config.toml"
    bad_config.write_text("bibs = []")

    result = doctor_check(config_path=str(bad_config), home_dir=str(tmp_path))
    assert result["status"] == "error"
    assert result["config_ok"] is False
    assert result["config_errors"]


def test_set_default_bib_rewrites_toml(tmp_path: Path) -> None:
    ml_path = tmp_path / "ml.bib"
    sys_path = tmp_path / "sys.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{ml_path}"
default = true

[[bibs]]
name = "sys"
path = "{sys_path}"
default = false
""".strip()
    )

    result = set_default_bib(
        config_path=str(config_path), home_dir=str(tmp_path), name="sys"
    )
    assert result["status"] == "ok"

    reloaded = list_bibs(config_path=str(config_path), home_dir=str(tmp_path))
    defaults = {b["name"]: b["default"] for b in reloaded["bibs"]}
    assert defaults == {"ml": False, "sys": True}


def test_set_default_bib_unknown_name(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)

    result = set_default_bib(
        config_path=str(config_path), home_dir=str(tmp_path), name="missing"
    )
    assert result["status"] == "error"


def test_update_bib_enriches_entries_without_venue(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    def fake_search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "venue": "Journal of Parsing",
                    "doi": "10.9/new",
                    "year": 2024,
                },
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"
    assert result["items"][0]["citekey"] == "smith2024graph"
    assert "venue" in result["items"][0]["changed_fields"]
    assert "journal = {Journal of Parsing}" in bib_path.read_text()


def test_update_bib_dry_run_does_not_write(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "arxiv_id": "2401.12345",
        },
        bib_selector=None,
        dry_run=False,
    )
    before = bib_path.read_text()

    def fake_search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {"venue": "X", "year": 2024},
                "attachments": [],
            }
        ]

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"
    assert bib_path.read_text() == before
    assert all(item["applied"] is False for item in result["items"])


def test_similarity_hint_added_on_insert(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "doi": "10.1/first",
            "authors": ["Smith, Jane"],
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2025graphproceedings",
            "title": "Graph Parsers for Structured Search: Extended",
            "doi": "10.1/second",
            "authors": ["Smith, Jane"],
            "year": 2025,
        },
        bib_selector=None,
        dry_run=False,
    )
    text = bib_path.read_text()
    assert "Possibly similar to smith2024graph" in text


def test_add_local_pdf_with_doi_lookup(tmp_path: Path, monkeypatch) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    def fake_extract(path: str):
        return {
            "doi": "10.1145/3368089.3409741",
            "title": "Graph Parsers",
            "text_sample": "sample",
        }

    monkeypatch.setattr("pzi.add_service.extract_pdf_metadata", fake_extract)

    def fake_search(query: str, *, server_url: str) -> list[dict]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "JMLR",
                    "doi": "10.1145/3368089.3409741",
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["status"] == "ok"
    assert result["action"] == "insert"
    text = bib_path.read_text()
    assert "doi = {10.1145/3368089.3409741}" in text
    assert "journal = {JMLR}" in text
    # PDF should be copied to papers_dir
    papers_dir = tmp_path / "papers"
    copied = list(papers_dir.glob("*.pdf"))
    assert len(copied) == 1


def test_add_local_pdf_dry_run_does_not_copy(tmp_path: Path, monkeypatch) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": None, "title": None, "text_sample": None},
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    papers_dir = tmp_path / "papers"
    assert not papers_dir.exists() or not list(papers_dir.glob("*.pdf"))


def test_add_local_pdf_no_metadata_creates_minimal_entry(tmp_path: Path, monkeypatch) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 no doi or title here just random bytes")

    monkeypatch.setattr(
        "pzi.add_service.extract_pdf_metadata",
        lambda path: {"doi": None, "title": None, "text_sample": None},
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value=str(pdf_path),
        record_overrides={},
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert result["citekey"] is not None
    # Should still copy PDF
    papers_dir = tmp_path / "papers"
    copied = list(papers_dir.glob("*.pdf"))
    assert len(copied) == 1
