from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from pzi.add_service import (
    add_input_to_bib,
    add_record_to_bib,
    add_record_with_bib,
    ensure_citekey_for_write,
    existing_citekeys,
    reuse_existing_pdf_fields_for_exact_match,
    reuse_orphan_pdf_for_planned_path,
)
from pzi.bib_repository import StalePlanError, plan_bib_write
from pzi.bibtex import record_to_bibtex_entry
from pzi.capture_local_pdf import build_add_record_result, plan_with_applied_record
from pzi.errors import REASON_USAGE, exit_code_for_error
from pzi.exit_codes import USAGE as _USAGE


def test_add_record_to_bib_inserts_new_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1000/foo",
        },
        bib_selector=None,
        dry_run=False,
    )

    result.pop("diff", None)

    assert result == {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": str(bib_path),
        "action": "insert",
        "citekey": "smith2024graph",
        "pdf_path": None,
        "changed_fields": ["citekey", "doi", "title"],
        "dry_run": False,
        "message": "insert entry",
        "warnings": [],
        "errors": [],
    }
    assert (
        bib_path.read_text()
        == "@article{smith2024graph,\n  doi = {10.1000/foo},\n  title = {Graph Parsers}\n}\n"
    )


def test_add_record_to_bib_supports_dry_run(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1000/foo",
        },
        bib_selector=None,
        dry_run=True,
    )

    result.pop("diff", None)

    assert result == {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": str(bib_path),
        "action": "insert",
        "citekey": "smith2024graph",
        "pdf_path": None,
        "changed_fields": ["citekey", "doi", "title"],
        "dry_run": True,
        "message": "would insert entry",
        "warnings": [],
        "errors": [],
    }
    assert not bib_path.exists()


def test_add_record_to_bib_updates_existing_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    bib_path.write_text(
        """
@article{smith2024graph,
  doi = {10.1000/foo},
  title = {Graph Parsers},
}
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers for Structured Search",
            "doi": "10.1000/foo",
            "tags": ["graphs"],
        },
        bib_selector=None,
        dry_run=False,
    )

    assert result == {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": str(bib_path),
        "action": "update",
        "citekey": "smith2024graph",
        "pdf_path": None,
        "changed_fields": ["tags", "title"],
        "dry_run": False,
        "message": "update entry",
        "warnings": [],
        "errors": [],
    }
    assert "keywords = {graphs}" in bib_path.read_text()
    assert "title = {Graph Parsers for Structured Search}" in bib_path.read_text()


def test_add_record_to_bib_updates_existing_entry_with_missing_local_pdf_path(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    bib_path.write_text(
        """
@article{smith2024graph,
  doi = {10.1000/foo},
  title = {Graph Parsers},
}
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "ignored-new-key",
            "doi": "10.1000/foo",
            "local_pdf_path": "papers/smith2024graph.pdf",
        },
        bib_selector=None,
        dry_run=False,
    )

    assert result == {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": str(bib_path),
        "action": "update",
        "citekey": "smith2024graph",
        "pdf_path": "papers/smith2024graph.pdf",
        "changed_fields": ["local_pdf_path"],
        "dry_run": False,
        "message": "update entry",
        "warnings": [],
        "errors": [],
    }
    assert "file = {papers/smith2024graph.pdf}" in bib_path.read_text()


def test_add_record_to_bib_reports_config_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024graph"},
        bib_selector=None,
        dry_run=False,
    )

    assert result == {
        "status": "error",
        "bib_name": None,
        "bib_path": None,
        "action": None,
        "citekey": None,
        "pdf_path": None,
        "changed_fields": [],
        "dry_run": False,
        "message": "failed to load config",
        "warnings": [],
        "errors": ["bibs must be a non-empty list"],
        # Classified rather than left to default: the config did not name what
        # was asked for. Read by `exit_code_for_error` and `pzi.http_status`.
        "reason": "config",
    }


def test_add_record_to_bib_reports_ambiguous_selection(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[bibs]]
name = "ml"
path = "/tmp/ml.bib"

[[bibs]]
name = "systems"
path = "/tmp/systems.bib"
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024graph"},
        bib_selector=None,
        dry_run=False,
    )

    assert result == {
        "status": "error",
        "bib_name": None,
        "bib_path": None,
        "action": None,
        "citekey": None,
        "pdf_path": None,
        "changed_fields": [],
        "dry_run": False,
        "message": "could not resolve target bib",
        "warnings": [],
        "errors": ["no matching bib found or selection is ambiguous"],
        "reason": "config",
    }


def test_add_record_to_bib_requires_citekey(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"title": "Graph Parsers"},
        bib_selector=None,
        dry_run=False,
    )

    assert result == {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": str(bib_path),
        "action": "insert",
        "citekey": "unknownxxxxgraph",
        "pdf_path": None,
        "changed_fields": ["citekey", "title"],
        "dry_run": False,
        "message": "insert entry",
        "warnings": [],
        "errors": [],
    }


def test_add_record_to_bib_generates_collision_free_citekey(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    bib_path.write_text(
        """
@article{smith2024graph,
  title = {Graph Parsers},
}
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "title": "Graph Systems",
            "authors": ["Smith, Jane"],
            "year": 2024,
        },
        bib_selector=None,
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["citekey"] == "smith2024graph-2"
    assert result["changed_fields"] == ["authors", "citekey", "title", "year"]


def test_add_record_to_bib_writes_relative_file_field_when_configured(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    pdf_path = tmp_path / "papers" / "smith2024.pdf"
    pdf_path.parent.mkdir()
    pdf_path.write_bytes(b"%PDF-1.4\n")
    config_path.write_text(
        f"""
pdf_file_path_style = "relative"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    result = add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "title": "Graph Parsers",
            "authors": ["Smith, Jane"],
            "year": 2024,
            "local_pdf_path": str(pdf_path),
        },
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert "file = {papers/smith2024.pdf}" in bib_path.read_text()
    assert str(pdf_path) not in bib_path.read_text()  # absolute path must NOT appear


def test_add_input_to_bib_uses_translation_server_metadata(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        assert url == "https://example.com/paper"
        assert server_url == f"http://127.0.0.1:{dead_port}"
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Fetched Title",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "doi": "10.1234/foo",
                    "canonical_url": "https://example.com/paper",
                },
                "attachments": [
                    {
                        "title": "PDF",
                        "url": "https://example.com/paper.pdf",
                        "mime_type": "application/pdf",
                    }
                ],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=fake_fetch_web,
    )

    assert result["status"] == "ok"
    assert result["citekey"] == "smith2024fetched"
    contents = bib_path.read_text()
    assert "doi = {10.1234/foo}" in contents
    assert "title = {Fetched Title}" in contents
    assert "pzi-pdf-url = {https://example.com/paper.pdf}" in contents
    assert "pzi-abstract-url = {https://example.com/paper}" in contents


def test_add_input_to_bib_prefers_cli_overrides_to_fetched_metadata(
    tmp_path: Path,
    dead_port,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        assert query == "10.1234/foo"
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Fetched Title",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "doi": "10.1234/foo",
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/foo",
        record_overrides={"title": "Manual Title", "citekey": "manual2024title"},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
    )

    result.pop("diff", None)

    assert result == {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": str(bib_path),
        "action": "insert",
        "citekey": "manual2024title",
        "pdf_path": None,
        "changed_fields": ["authors", "citekey", "doi", "title", "year"],
        "dry_run": True,
        "message": "would insert entry",
        # A capture the translation server answered now says so, as a Crossref
        # capture always did. The asymmetry is what made the live smoke job
        # unable to tell a real translation-server capture from a fallback.
        "metadata_diagnostics": ["metadata from translation_server"],
        "warnings": [],
        "errors": [],
    }


def test_add_input_to_bib_errors_when_translation_server_fails(
    tmp_path: Path,
    dead_port,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        # ValueError, not an arbitrary exception type: add_input_to_bib's
        # except clause is scoped to what a real fetcher can raise (network
        # errors, malformed-response ValueErrors), not to any Exception.
        raise ValueError("server unavailable")

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={"title": "Manual Title"},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
    )

    assert result["status"] == "error"
    assert result["errors"] == ["server unavailable"]


def test_add_input_to_bib_does_not_swallow_non_network_bugs(
    tmp_path: Path,
    dead_port,
) -> None:
    """Regression: add_input_to_bib's except clause used to catch bare
    Exception, so a real bug (KeyError, AttributeError, ...) anywhere in the
    fetch/discovery path was silently misreported as a translation-server
    error instead of surfacing. It's now scoped to actual network/parsing
    failure types."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        raise KeyError("unexpected bug, not a network failure")

    with pytest.raises(KeyError):
        add_input_to_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            value="https://example.com/paper",
            record_overrides={"title": "Manual Title"},
            bib_selector=None,
            dry_run=True,
            fetch_web=fake_fetch_web,
        )


def test_add_input_to_bib_rejects_unrecognized_input_without_fetching(
    tmp_path: Path,
) -> None:
    """`pzi add l` must error out, not insert an empty placeholder entry."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def boom_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        raise AssertionError("fetcher must not run for unrecognized input")

    def boom_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise AssertionError("fetcher must not run for unrecognized input")

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="l",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=boom_fetch_web,
        fetch_search=boom_fetch_search,
    )

    assert result["status"] == "error"
    assert result["message"] == "invalid input"
    assert result["errors"] == ["'l' is not a DOI, URL, or local PDF path"]
    assert not bib_path.exists()  # nothing was written
    # A malformed argument is a usage error. `error_result` set no `reason` at
    # all, so every capture failure defaulted to ENVIRONMENT and `pzi.add("l")`
    # was a 5 where the CLI — which validates the same input in the parser —
    # was a 2. The one writing command had the least usable classification.
    assert result["reason"] == REASON_USAGE
    assert exit_code_for_error(result) == _USAGE


def test_describe_invalid_add_input_classifies_inputs(tmp_path: Path) -> None:
    from pzi.add_service import describe_invalid_add_input

    assert describe_invalid_add_input("l") == "'l' is not a DOI, URL, or local PDF path"
    assert describe_invalid_add_input("10.1145/1327452.1327492") is None
    assert describe_invalid_add_input("https://example.com/paper") is None

    missing = tmp_path / "missing.pdf"
    assert describe_invalid_add_input(str(missing)) == f"PDF file not found: {missing}"
    present = tmp_path / "present.pdf"
    present.write_bytes(b"%PDF-1.4\n")
    assert describe_invalid_add_input(str(present)) is None


def test_add_input_to_bib_falls_back_to_crossref_when_zotero_returns_501(
    tmp_path: Path,
    dead_port,
) -> None:
    import urllib.error
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise urllib.error.HTTPError(None, 501, "Not Implemented", {}, None)  # type: ignore[arg-type]

    def fake_crossref(doi: str, **_: object) -> dict[str, object] | None:
        assert doi == "10.5555/3327546.3327713"
        return {
            "title": "Fast Neural Networks",
            "authors": ["Smith, Jane"],
            "year": 2019,
            "venue": "NeurIPS",
            "doi": "10.5555/3327546.3327713",
        }

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.5555/3327546.3327713",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
        fetch_crossref=fake_crossref,
    )

    assert result["status"] == "ok"
    assert result["action"] == "insert"
    assert "title" in result["changed_fields"]


def test_add_input_to_bib_downloads_valid_pdf_attachment(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Fetched Title",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "doi": "10.1234/foo",
                },
                "attachments": [
                    {
                        "title": "PDF",
                        "url": "https://example.com/paper.pdf",
                        "mime_type": "application/pdf",
                    }
                ],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=fake_fetch_web,
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
    )

    assert result["status"] == "ok"
    assert result["warnings"] == []
    assert "file = {" in bib_path.read_text()


def test_add_record_with_bib_retries_once_on_stale_plan_without_redownload(
    tmp_path: Path, monkeypatch
) -> None:
    # A plan that went stale (its target moved) aborts the first write before
    # anything is written; the retry re-reads, replans, and commits. The PDF
    # must be downloaded exactly once (the download happens before planning and
    # is preserved across the retry).
    from pzi import add_service
    from pzi.bib_repository import StalePlanError

    real_execute = add_service.execute_write_plan
    papers = tmp_path / "papers"
    papers.mkdir()
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    bib = {"name": "ml", "path": str(bib_path),
           "papers_dir": str(papers), "default": True}

    downloads = {"n": 0}

    def _spy_fetch_binary(url: str):
        downloads["n"] += 1
        return (b"%PDF-1.7\nbody", "application/pdf")

    calls = {"n": 0}

    def _flaky_execute(path, plan, *, file_path_style="absolute"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise StalePlanError("the bib changed while this write was being prepared")
        return real_execute(path, plan, file_path_style=file_path_style)

    monkeypatch.setattr(add_service, "execute_write_plan", _flaky_execute)

    result = add_service.add_record_with_bib(
        bib=bib,  # type: ignore[arg-type]
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1000/foo",
            "pdf_url": "https://example.com/paper.pdf",
        },
        dry_run=False,
        fetch_binary=_spy_fetch_binary,
    )

    assert result["status"] == "ok"
    assert result["action"] == "insert"
    assert calls["n"] == 2          # first attempt raised, retry committed
    assert downloads["n"] == 1      # PDF fetched exactly once
    assert "@article{smith2024graph" in bib_path.read_text()
    # The single downloaded PDF survives the replan and is referenced by the
    # committed entry — not re-downloaded and not left orphaned.
    assert len(list(papers.glob("*.pdf"))) == 1
    assert "file = {" in bib_path.read_text()


def test_add_record_with_bib_reraises_when_the_plan_stays_stale(
    tmp_path: Path, monkeypatch
) -> None:
    # If the plan is stale on the retry too, give up and re-raise so the
    # CLI/HTTP layer can render the friendly message.
    import pytest

    from pzi import add_service
    from pzi.bib_repository import StalePlanError

    papers = tmp_path / "papers"
    papers.mkdir()
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    bib = {"name": "ml", "path": str(bib_path),
           "papers_dir": str(papers), "default": True}

    def _always_raise(path, plan, *, file_path_style="absolute"):
        raise StalePlanError("the bib changed while this write was being prepared")

    monkeypatch.setattr(add_service, "execute_write_plan", _always_raise)

    with pytest.raises(StalePlanError):
        add_service.add_record_with_bib(
            bib=bib,  # type: ignore[arg-type]
            record={"citekey": "smith2024graph", "title": "Graph Parsers",
                    "doi": "10.1000/foo"},
            dry_run=False,
        )


def test_add_record_with_bib_cleans_up_pdf_when_the_plan_stays_stale(
    tmp_path: Path, monkeypatch
) -> None:
    # When the retry also finds the plan stale and we give up, the PDF that
    # was downloaded before planning must be removed — not left orphaned in
    # papers_dir.
    import pytest

    from pzi import add_service
    from pzi.bib_repository import StalePlanError

    papers = tmp_path / "papers"
    papers.mkdir()
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    bib = {"name": "ml", "path": str(bib_path),
           "papers_dir": str(papers), "default": True}

    downloads = {"n": 0}

    def _spy_fetch_binary(url: str):
        downloads["n"] += 1
        return (b"%PDF-1.7\nbody", "application/pdf")

    def _always_raise(path, plan, *, file_path_style="absolute"):
        raise StalePlanError("the bib changed while this write was being prepared")

    monkeypatch.setattr(add_service, "execute_write_plan", _always_raise)

    with pytest.raises(StalePlanError):
        add_service.add_record_with_bib(
            bib=bib,  # type: ignore[arg-type]
            record={
                "citekey": "smith2024graph",
                "title": "Graph Parsers",
                "doi": "10.1000/foo",
                "pdf_url": "https://example.com/paper.pdf",
            },
            dry_run=False,
            fetch_binary=_spy_fetch_binary,
        )

    assert downloads["n"] == 1                  # downloaded once, before planning
    assert list(papers.glob("*.pdf")) == []     # and cleaned up on give-up


def test_add_input_to_bib_uses_web_fallback_for_doi_pdf_discovery(
    tmp_path: Path,
    dead_port,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    search_calls: list[str] = []
    web_calls: list[str] = []

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        search_calls.append(query)
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "title": "Stochastic Parrots",
                    "authors": ["Bender, Emily M."],
                    "year": 2021,
                    "doi": "10.1145/3442188.3445922",
                    "canonical_url": "https://dl.acm.org/doi/10.1145/3442188.3445922",
                    "abstract_url": "https://dl.acm.org/doi/10.1145/3442188.3445922",
                },
                "attachments": [],
            }
        ]

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        web_calls.append(url)
        assert url == "https://dl.acm.org/doi/10.1145/3442188.3445922"
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "canonical_url": url,
                    "abstract_url": url,
                },
                "attachments": [
                    {
                        "title": "PDF",
                        "url": "https://dl.acm.org/doi/pdf/10.1145/3442188.3445922?download=true",
                        "mime_type": "application/pdf",
                    }
                ],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://doi.org/10.1145/3442188.3445922",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        fetch_web=fake_fetch_web,
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
    )

    assert result["status"] == "ok"
    assert search_calls == ["10.1145/3442188.3445922"]
    assert web_calls == ["https://dl.acm.org/doi/10.1145/3442188.3445922"]
    contents = bib_path.read_text()
    assert "file = {" in contents
    assert "https://dl.acm.org/doi/pdf/10.1145/3442188.3445922?download=true" in contents


def test_add_input_to_bib_prefers_browser_supplied_pdf_candidate(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "title": "Browser PDF",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "doi": "10.1234/browser",
                    "canonical_url": "https://example.com/paper",
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/browser",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        pdf_url_candidates=["https://example.com/from-browser.pdf"],
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
    )

    assert result["status"] == "ok"
    contents = bib_path.read_text()
    assert "file = {" in contents
    assert "pzi-pdf-url = {https://example.com/from-browser.pdf}" in contents


def test_add_input_to_bib_uses_browser_pdf_command_when_configured(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    hook_path = tmp_path / "browser_hook.py"
    hook_path.write_text(
        "import json\nprint(json.dumps({'pdf_url': 'https://example.com/from-browser-cmd.pdf'}))\n"
    )
    browser_cmd = f"python {hook_path}"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"
browser_pdf_cmd = '{browser_cmd}'

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "title": "Browser Command PDF",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "doi": "10.1234/browsercmd",
                    "canonical_url": "https://example.com/paper",
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/browsercmd",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
    )

    assert result["status"] == "ok"
    contents = bib_path.read_text()
    assert "file = {" in contents
    assert "pzi-pdf-url = {https://example.com/from-browser-cmd.pdf}" in contents


def test_add_input_to_bib_browser_pdf_command_argument_overrides_config(
    tmp_path: Path,
    dead_port,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_hook = tmp_path / "config_hook.py"
    argument_hook = tmp_path / "argument_hook.py"
    config_hook.write_text(
        "import json\nprint(json.dumps({'pdf_url': 'https://example.com/from-config.pdf'}))\n"
    )
    argument_hook.write_text(
        "import json\nprint(json.dumps({'pdf_url': 'https://example.com/from-argument.pdf'}))\n"
    )
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"
browser_pdf_cmd = 'python {config_hook}'

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "title": "Browser Command Override",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "doi": "10.1234/browserarg",
                    "canonical_url": "https://example.com/paper",
                },
                "attachments": [],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/browserarg",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
        browser_pdf_cmd=f"python {argument_hook}",
    )

    assert result["status"] == "ok"
    contents = bib_path.read_text()
    assert "pzi-pdf-url = {https://example.com/from-argument.pdf}" in contents
    assert "from-config.pdf" not in contents


def test_add_record_with_page_metadata_overrides_still_inserts_when_fetch_minimal(
    tmp_path: Path,
    dead_port,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={
            "title": "Browser Page Title",
            "doi": "10.1234/browser-page",
            "canonical_url": "https://example.com/paper",
            "source_url": "https://example.com/paper",
            "abstract_url": "https://example.com/paper",
        },
        bib_selector=None,
        dry_run=False,
        fetch_web=fake_fetch_web,
    )

    assert result["status"] == "ok"
    text = bib_path.read_text()
    assert "title = {Browser Page Title}" in text
    assert "doi = {10.1234/browser-page}" in text


def test_add_input_to_bib_doi_uses_browser_metadata_when_lookup_fails(
    tmp_path: Path,
    dead_port,
) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.5555/3327546.3327713",
        record_overrides={
            "title": "Fallback Browser Title",
            "doi": "10.5555/3327546.3327713",
            "canonical_url": "https://example.com/landing",
            "source_url": "https://example.com/landing",
            "abstract_url": "https://example.com/landing",
        },
        bib_selector=None,
        dry_run=False,
        fetch_search=lambda query, *, server_url: [],
        fetch_web=lambda url, *, server_url: [],
        fetch_crossref=lambda doi, **_: None,
        fetch_openalex=lambda doi, **_: None,
        fetch_s2=lambda doi: None,
    )

    assert result["status"] == "ok"
    text = bib_path.read_text()
    assert "title = {Fallback Browser Title}" in text
    assert "doi = {10.5555/3327546.3327713}" in text


def test_add_input_to_bib_warns_and_skips_html_attachment(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Fetched Title",
                },
                "attachments": [
                    {
                        "title": "PDF",
                        "url": "https://example.com/paper.pdf",
                        "mime_type": "application/pdf",
                    }
                ],
            }
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=fake_fetch_web,
        fetch_binary=lambda url: (b"<html>not pdf</html>", "text/html"),
    )

    assert result["status"] == "ok"
    assert any(
        "all download methods failed for https://example.com/paper.pdf" in w
        for w in result["warnings"]
    )
    assert "file = {" not in bib_path.read_text()


def test_add_input_to_bib_uses_unpaywall_when_no_attachment(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"
unpaywall_email = "test@example.com"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "MapReduce",
                    "doi": "10.1145/1327452.1327492",
                },
                "attachments": [],
            }
        ]

    def fake_unpaywall(doi: str, *, email: str) -> str | None:
        assert doi == "10.1145/1327452.1327492"
        assert email == "test@example.com"
        return "https://dl.acm.org/doi/pdf/10.1145/1327452.1327492"

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1145/1327452.1327492",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        fetch_unpaywall=fake_unpaywall,
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
    )

    assert result["status"] == "ok"
    assert result["warnings"] == []
    assert "file = {" in bib_path.read_text()


def test_add_input_to_bib_flaresolverr_url_fallback(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"
flaresolverr_url = "http://127.0.0.1:8191"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    cloudflare_html = """
<html>
<head>
<meta name="citation_title" content="Deep Learning Book">
<meta name="citation_author" content="Goodfellow, Ian">
<meta name="citation_publication_date" content="2016">
</head>
<body></body>
</html>
"""

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_flaresolverr(url: str) -> str | None:
        return cloudflare_html

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://cloudflare-protected.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )

    assert result["status"] == "ok"
    bib_text = bib_path.read_text()
    assert "Deep Learning Book" in bib_text
    assert "Goodfellow" in bib_text


def test_add_input_to_bib_flaresolverr_disabled_when_no_url(tmp_path: Path, dead_port) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    flaresolverr_called = []

    def fake_flaresolverr(url: str) -> str | None:
        flaresolverr_called.append(url)
        return None

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://cloudflare-protected.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )

    assert result["status"] == "error"
    assert flaresolverr_called == []


def test_add_input_to_bib_flaresolverr_doi_embedded_in_url(tmp_path: Path, dead_port) -> None:
    """ACM-style URL with unresolvable pseudo-DOI falls back to FlareSolverr."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"
flaresolverr_url = "http://127.0.0.1:8191"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        assert "dl.acm.org" in url
        return [
            {
                "item_type": "conferencePaper",
                "record": {
                    "title": "Attention Is All You Need",
                    "authors": ["Vaswani, Ashish"],
                    "year": 2017,
                    "venue": "NeurIPS 2017",
                    "doi": "10.5555/3327546.3327713",
                },
                "attachments": [],
            }
        ]

    def fake_crossref(doi: str, **_: object) -> object:
        return None

    def fake_openalex(doi: str, **_: object) -> object:
        return None

    def fake_s2(doi: str) -> object:
        return None

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="https://dl.acm.org/doi/10.5555/3327546.3327713",
        record_overrides={},
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_fetch_search,
        fetch_web=fake_fetch_web,
        fetch_crossref=fake_crossref,
        fetch_openalex=fake_openalex,
        fetch_s2=fake_s2,
    )

    assert result["status"] == "ok"
    bib_text = bib_path.read_text()
    assert "Attention Is All You Need" in bib_text


def test_ensure_citekey_reuses_exact_match_key() -> None:
    record = {"doi": "10.1234/a", "title": "Paper"}
    existing = [{"doi": "10.1234/a", "citekey": "smith2024paper"}]

    result = ensure_citekey_for_write(record, existing)  # type: ignore[arg-type]

    assert result["citekey"] == "smith2024paper"


def test_ensure_citekey_suffixes_collision() -> None:
    record = {"citekey": "smith2024paper", "doi": "10.1234/b"}
    existing = [{"citekey": "smith2024paper", "doi": "10.1234/a"}]

    result = ensure_citekey_for_write(record, existing)  # type: ignore[arg-type]

    assert result["citekey"] == "smith2024paper-2"


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


def test_plan_with_applied_record_rebases_citekey() -> None:
    plan = {"record": {"citekey": "old", "doi": "10.1234/a"}, "action": "insert"}
    updated_entry = record_to_bibtex_entry(
        {"citekey": "new", "doi": "10.1234/a", "title": "Paper"}
    )

    result = plan_with_applied_record(
        plan,
        {"doi": "10.1234/a"},  # type: ignore[arg-type]
        [updated_entry],
    )

    assert result["record"]["citekey"] == "new"
    assert result["entry"] is updated_entry


def test_plan_with_applied_record_keeps_the_force_new_citekey() -> None:
    """A force-new insert must not be rebound onto the entry it duplicates.

    Both entries carry the same DOI, so the identity-based `find_exact_match`
    rebind would return the *pre-existing* one and silently discard the
    force-generated citekey — turning "add this again as a separate entry" back
    into "you already have this". The `force_new` early return is what prevents
    that, and nothing covered it.
    """
    plan = {
        "record": {"citekey": "smith2024a", "doi": "10.1234/a"},
        "action": "insert",
        "force_new": True,
    }
    pre_existing = record_to_bibtex_entry(
        {"citekey": "smith2024", "doi": "10.1234/a", "title": "Paper"}
    )
    forced = record_to_bibtex_entry(
        {"citekey": "smith2024a", "doi": "10.1234/a", "title": "Paper"}
    )

    result = plan_with_applied_record(
        plan,
        {"doi": "10.1234/a"},  # type: ignore[arg-type]
        [pre_existing, forced],
    )

    assert result["record"]["citekey"] == "smith2024a"


def test_plan_with_applied_record_reports_what_the_write_committed() -> None:
    """The reported record must match the entry, even when the citekey held.

    `execute_write_plan` rebases the plan onto the library it reads under the
    lock, but it rebases a *local* copy and hands back only the entries — so a
    concurrent writer's field that the rebase correctly preserved in the `.bib`
    was missing from the record this capture reports (`--json`, the HTTP capture
    payload) and from nowhere else. Reconciling only when the citekey moved left
    exactly that case uncovered, which is the common one: an ordinary capture
    onto an entry someone else just edited.
    """
    plan = {
        "record": {"citekey": "a2020", "doi": "10.1234/a", "venue": None},
        "action": "update",
    }
    # What the rebase actually wrote: the other writer's `journal` survived.
    committed = record_to_bibtex_entry(
        {
            "citekey": "a2020",
            "doi": "10.1234/a",
            "title": "Paper",
            "venue": "NeurIPS",
        }
    )

    result = plan_with_applied_record(
        plan,
        {"doi": "10.1234/a"},  # type: ignore[arg-type]
        [committed],
    )

    assert result["record"]["venue"] == "NeurIPS"


def test_plan_with_applied_record_keeps_keys_the_entry_cannot_carry() -> None:
    """Reconciling the record must not throw away what BibTeX does not model.

    `bibtex_entry_to_record` can only recover what the entry holds, so replacing
    the record wholesale dropped `pdf_source`, `similarity_hint`, `duplicate_of`,
    the `fallback_*` keys — and `_citekey_renamed_from`, which
    `build_add_record_result` reads to warn that the requested citekey was taken.
    A citekey rebind is precisely when that warning is due, so the swap silently
    swallowed it every time it fired.
    """
    plan = {
        "record": {
            "citekey": "old",
            "doi": "10.1234/a",
            "pdf_source": "unpaywall",
            "_citekey_renamed_from": "wanted2020",
        },
        "action": "insert",
    }
    committed = record_to_bibtex_entry(
        {"citekey": "new", "doi": "10.1234/a", "title": "Paper"}
    )

    result = plan_with_applied_record(
        plan,
        {"doi": "10.1234/a"},  # type: ignore[arg-type]
        [committed],
    )

    assert result["record"]["citekey"] == "new"
    assert result["record"]["pdf_source"] == "unpaywall"
    assert result["record"]["_citekey_renamed_from"] == "wanted2020"


def test_build_add_record_result_shapes_dry_run_message() -> None:
    plan = plan_bib_write(
        {"citekey": "smith2024paper", "title": "Paper"},
        [],
    )

    result = build_add_record_result(
        bib={"name": "ml", "path": "/tmp/ml.bib"},
        plan=plan,
        warnings=[],
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["message"] == "would insert entry"
    assert result["citekey"] == "smith2024paper"


# ---------------------------------------------------------------------------
# Single-write-path PDF cleanup on retry-then-fail
# ---------------------------------------------------------------------------

_FAKE_PDF = b"%PDF-1.4 fake"


def _fake_fetch_binary(url: str) -> tuple[bytes, str | None]:
    return _FAKE_PDF, "application/pdf"


def test_single_path_retry_then_fail_removes_new_pdf(tmp_path: Path) -> None:
    """A final StalePlanError on the single-write path removes the PDF.

    The PDF is downloaded before the plan+write loop. On the second
    StalePlanError the cleanup guard must remove it from papers_dir —
    but must NOT remove any PDF that existed before the capture.
    """
    bib_path = tmp_path / "lib.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()

    # A pre-existing PDF must survive.
    pre_existing = papers_dir / "preexisting.pdf"
    pre_existing.write_bytes(_FAKE_PDF)

    bib: dict = {
        "name": "test",
        "path": str(bib_path),
        "papers_dir": str(papers_dir),
    }

    # Both execute_write_plan attempts raise StalePlanError so the retry
    # exhausts and re-raises, triggering _cleanup_new_pdf.
    with patch(
        "pzi.add_service.execute_write_plan",
        side_effect=StalePlanError("the entry it targets no longer exists"),
    ):
        with pytest.raises(StalePlanError):
            add_record_with_bib(
                bib=cast(dict, bib),
                record={
                    "citekey": "smith2024",
                    "title": "New Paper",
                    "doi": "10.1000/new",
                    "pdf_url": "http://example.com/new.pdf",
                },
                dry_run=False,
                fetch_binary=_fake_fetch_binary,
            )

    remaining = list(papers_dir.glob("*.pdf"))
    assert remaining == [pre_existing], (
        "only the pre-existing PDF should remain; newly-downloaded PDF must be removed"
    )


# ---------------------------------------------------------------------------
# Provider error warn / strict-metadata
# ---------------------------------------------------------------------------


def _make_add_input_config(tmp_path: Path, dead_port: int) -> tuple[str, str]:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    return str(config_path), str(bib_path)


def test_add_input_to_bib_provider_error_appears_as_warning(
    tmp_path: Path, dead_port
) -> None:
    """When a provider fails and fallback succeeds, result is ok with a warning."""
    import urllib.error

    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    def fake_crossref(doi: str, **_: object) -> dict[str, object]:
        return {"title": "Good Paper", "doi": doi, "year": 2024}

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1234/good.2024",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
        fetch_crossref=fake_crossref,
    )

    assert result["status"] == "ok"
    warnings = result.get("warnings", [])
    assert any("429" in w for w in warnings), (
        f"expected HTTP 429 warning; got warnings={warnings}"
    )


def test_strict_metadata_accepts_a_capture_a_later_provider_rescued(
    tmp_path: Path, dead_port
) -> None:
    """A recovered provider error is a warning, not a failure.

    `--strict-metadata` used to fail whenever *any* provider errored, even when
    a later one produced a complete record — so a routine 429 from the
    translation server failed an add that Crossref then completed perfectly.
    That is the flag refusing a capture it has no complaint about.
    """
    import urllib.error

    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        raise urllib.error.HTTPError(None, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    def fake_crossref(doi: str, **_: object) -> dict[str, object]:
        return {"title": "Good Paper", "doi": doi, "year": 2024}

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1234/good.2024",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
        fetch_crossref=fake_crossref,
        metadata_strict=True,
    )

    assert result["status"] == "ok", result
    # Reported, not hidden: the 429 still reaches the user as a warning.
    assert any("429" in w for w in result.get("warnings", [])), result.get("warnings")


def test_strict_metadata_refuses_a_record_that_identifies_no_paper(
    tmp_path: Path, dead_port
) -> None:
    """The thing strict mode never checked.

    Nothing tested completeness on the success path, so strict mode accepted
    `@article{unknownxxxxuntitled, doi = {…}}` — no title, no author, no year —
    while rejecting complete records over a transient 429.
    `has_minimum_metadata` already existed and was applied only on the
    translation-server-crash fallback.
    """
    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    def no_results(query: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def bare_crossref(doi: str, **_: object) -> dict[str, object]:
        return {"doi": doi}

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1234/bare.2024",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=no_results,
        fetch_crossref=bare_crossref,
        metadata_strict=True,
    )

    assert result["status"] == "error", result
    assert "strict-metadata" in result.get("message", "")
    assert result["errors"]


def test_without_strict_an_incomplete_record_is_still_captured(
    tmp_path: Path, dead_port
) -> None:
    """The completeness gate belongs to the flag, not to every add."""
    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1234/bare.2024",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=lambda query, *, server_url: [],
        fetch_crossref=lambda doi, **_: {"doi": doi},
        metadata_strict=False,
    )

    assert result["status"] == "ok", result


def test_add_reports_metadata_diagnostics_when_several_candidates_returned(
    tmp_path: Path, dead_port
) -> None:
    """Ambiguous metadata must still be reported on the add result.

    The diagnostics used to be produced inside a wrapper around the injected
    fetcher that assigned to `nonlocal` variables; they are now computed from the
    results `fetch_record_for_input` returns. Nothing covered this path, so this
    pins it.
    """
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def two_candidates(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {"title": "Graph Parsers", "doi": "10.1145/3368089.3409741",
                           "year": 2024},
                "attachments": [],
            },
            {
                # No DOI, so it is not dropped for contradicting the requested
                # one — genuinely ambiguous, which is what "several candidates"
                # is about. Its old DOI contradicted the query, so it is now
                # discarded before selection and there is only one candidate to
                # report.
                "item_type": "journalArticle",
                "record": {"title": "Graph Parsers (extended abstract)", "year": 2023},
                "attachments": [],
            },
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1145/3368089.3409741",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=two_candidates,
        fetch_web=lambda url, *, server_url: [],
        fetch_crossref=lambda doi, **_: None,
        fetch_openalex=lambda doi, **_: None,
        fetch_s2=lambda doi: None,
    )

    assert result["status"] == "ok"
    assert result["metadata_diagnostics"], "several candidates must be reported"


def test_verbose_does_not_name_a_candidate_that_was_thrown_away(
    tmp_path: Path, dead_port
) -> None:
    """`--verbose` reads the recorded candidate list to say where a capture
    came from.

    That list was extended with every raw hit *before* the ones contradicting
    the requested DOI were dropped, so the diagnostic named a candidate that
    played no part — and warned "metadata confidence low" about a capture that
    was correct.
    """
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:{dead_port}"

[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def one_good_one_contradicting(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "doi": "10.1145/3368089.3409741",
                    "year": 2024,
                },
                "attachments": [],
            },
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "An Entirely Different Paper",
                    "doi": "10.1145/9999999.8888888",
                    "year": 1999,
                },
                "attachments": [],
            },
        ]

    result = add_input_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1145/3368089.3409741",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=one_good_one_contradicting,
        fetch_web=lambda url, *, server_url: [],
        fetch_crossref=lambda doi, **_: None,
        fetch_openalex=lambda doi, **_: None,
        fetch_s2=lambda doi: None,
    )

    assert result["status"] == "ok"
    reported = " ".join(result.get("metadata_diagnostics") or []) + " ".join(
        result.get("warnings") or []
    )
    assert "An Entirely Different Paper" not in reported, reported
    assert "10.1145/9999999.8888888" not in reported, reported


def test_near_duplicate_insert_warns_and_notes_the_existing_entry(tmp_path: Path) -> None:
    """A same-paper capture with no shared identifier inserts, but says so.

    No DOI and no arXiv id on either side, and the two canonical URLs differ by
    a trailing slash, so exact-identity matching cannot fire and the write is an
    insert. The near-duplicate was previously recorded only in the new entry's
    ``note`` field, which meant the terminal output was indistinguishable from a
    clean capture.
    """
    bib_path = tmp_path / "library.bib"
    papers = tmp_path / "papers"
    papers.mkdir()
    bib_path.write_text(
        "@article{smith2024graph,\n"
        "  title = {A Study of Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  url = {https://example.org/paper},\n"
        "}\n"
    )
    bib = {"name": "main", "path": str(bib_path), "papers_dir": str(papers), "default": True}

    result = add_record_with_bib(
        bib=cast(dict, bib),
        record={
            "title": "A Study of Graph Parsers",
            "authors": ["Smith, Jane"],
            "year": 2024,
            # A genuinely different location. A trailing-slash variant of the
            # existing entry's URL is the *same* page, and reached this fuzzy
            # path only because identities compared URLs verbatim.
            "canonical_url": "https://mirror.example.net/2024/graph-parsers",
        },
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert result["action"] == "insert", "no shared identifier — cannot exact-match"
    assert result["warnings"] == [
        "possibly a duplicate of smith2024graph — compare them with `pzi library dedupe`"
    ]

    text = bib_path.read_text()
    assert text.count("@article{") == 2, "the duplicate is still written"
    assert "Possibly similar to smith2024graph" in text, "note records it in the bib"
    assert "similarity_hint" not in text, "the structural field must not leak into BibTeX"


def test_strict_metadata_is_not_bypassed_when_the_whole_cascade_fails(
    tmp_path: Path, dead_port
) -> None:
    """Total provider failure must fail under strict, like partial failure does.

    When the cascade raised, the errors it had accumulated were lost and the
    strict gate inside the `try` never ran. Control fell to the fallback, which
    — given enough hand-supplied metadata — added the entry and reported ok.
    That inverted the flag's severity: a partial failure was fatal while a total
    one succeeded silently, which is the exact "falling back silently" the flag
    is documented to prevent.
    """
    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1234/good.2024",
        record_overrides={
            "title": "A Hand-Entered Paper",
            "authors": ["Smith, Ada"],
            "year": 2024,
        },
        bib_selector=None,
        dry_run=True,
        metadata_strict=True,
    )

    assert result["status"] == "error"
    assert "strict-metadata" in result.get("message", "")


def test_without_strict_the_cascade_failure_still_falls_back(
    tmp_path: Path, dead_port
) -> None:
    """The fallback is correct behavior by default — strict is what forbids it."""
    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="10.1234/good.2024",
        record_overrides={
            "title": "A Hand-Entered Paper",
            "authors": ["Smith, Ada"],
            "year": 2024,
        },
        bib_selector=None,
        dry_run=True,
    )

    assert result["status"] == "ok"


def test_a_metadata_lookup_failure_is_not_called_a_translation_server_error(
    tmp_path: Path, dead_port
) -> None:
    """An HTTP status means the server answered; only a connect failure is its fault.

    Reporting every failure as "translation server error" sent users to restart
    a service that was running fine.
    """
    import urllib.error

    config_path, _bib_path = _make_add_input_config(tmp_path, dead_port)

    def failing_web(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://x", 500, "Server Error", {}, None  # type: ignore[arg-type]
        )

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=failing_web,
    )

    assert result["status"] == "error"
    assert result.get("message") == "metadata lookup failed"


def test_a_pdf_failure_does_not_lose_the_metadata(tmp_path, monkeypatch) -> None:
    """Attaching a PDF is enrichment; the entry is the point of `pzi add`.

    An exception out of the PDF stage — a dead `browser_pdf_cmd`, a full disk, a
    provider hanging up mid-download — aborted the whole add, so the metadata
    that had already been resolved was thrown away and the user had to re-fetch
    it. The PDF is retryable with `pzi pdf retry`; the capture is not.
    """
    from pzi import add_service

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("", encoding="utf-8")
    papers = tmp_path / "papers"
    papers.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n',
        encoding="utf-8",
    )

    def _explode(**_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(add_service, "attach_pdf_if_available", _explode)

    result = add_service.add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "year": 2024,
            "pdf_url": "https://example.com/paper.pdf",
        },
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert result["citekey"] == "smith2024graph"
    assert "Graph Parsers" in bib_path.read_text(encoding="utf-8")
    assert any("No space left" in w for w in result["warnings"]), result["warnings"]
