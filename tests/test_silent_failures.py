"""Failures that happened and were not reported.

Each test here corresponds to a run that did something the user was not told
about: a flag ignored, a write that failed, a stage skipped, a provider broken.
The shared shape is that the command exited as though nothing was wrong.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from pzi import exit_codes
from pzi.cli import run_cli

MINIMAL_CONFIG = """
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
"""


def _library(tmp_path: Path, text: str = "") -> tuple[Path, Path]:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(text, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")
    return config_path, bib_path


def _run(argv: list[str], tmp_path: Path) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(argv, home_dir=str(tmp_path), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_add_json_emits_a_document_on_a_usage_error(tmp_path: Path) -> None:
    """`--json` promises one document *including on failure*; this printed none.

    Every other command routes conditional usage checks through
    `emit_usage_error`; `add` printed prose to stderr and left stdout empty, so
    a script got rc=2 and nothing to parse.
    """
    config_path, _bib = _library(tmp_path)
    code, out, _err = _run(["add", "--config", str(config_path), "--json"], tmp_path)
    assert code == exit_codes.USAGE
    envelope = json.loads(out)
    assert envelope["status"] == "error"
    assert envelope["errors"]


def test_doctor_json_emits_a_document_on_a_usage_error(tmp_path: Path) -> None:
    config_path, _bib = _library(tmp_path)
    code, out, _err = _run(
        ["doctor", "--config", str(config_path), "--json",
         "--config-only", "--reinstall-server"],
        tmp_path,
    )
    assert code == exit_codes.USAGE
    assert json.loads(out)["status"] == "error"


def test_a_dry_run_stream_says_would(tmp_path: Path) -> None:
    """The banner and summary said "would"; the per-item lines said "✓ added".

    Those lines are what scrolls past and what the user reads.
    """
    from pzi.commands.common import print_capture_stream_line

    stderr = StringIO()
    print_capture_stream_line(
        index=0, total=1, value="10.1/x", bucket="added", citekey="a2020",
        reason=None, dry_run=True, stderr=stderr,
    )
    assert "would added" in stderr.getvalue()

    real = StringIO()
    print_capture_stream_line(
        index=0, total=1, value="10.1/x", bucket="added", citekey="a2020",
        reason=None, dry_run=False, stderr=real,
    )
    assert "would" not in real.getvalue()


def test_an_unreadable_inbox_is_not_reported_as_nothing_appended(tmp_path: Path) -> None:
    """`[]` sends the caller on to rewrite a file it just failed to read."""
    from pzi.inbox_service import _appended_since

    assert _appended_since(tmp_path / "does-not-exist.txt", "snapshot") is None


def test_inbox_reports_the_tokens_it_ignored(tmp_path: Path) -> None:
    """`#Deep Learning` silently loses `Learning`, and so does a second URL."""
    from pzi.inbox_service import parse_inbox_line

    line = parse_inbox_line("10.1000/x #ml #Deep Learning @main https://other.example")
    assert line is not None
    assert line.tags == ["ml", "Deep"]
    assert line.unrecognized == ["Learning", "https://other.example"]


def test_a_skipped_pdf_stage_does_not_claim_it_found_nothing() -> None:
    """"desktop browser download: no PDF appeared" for a stage switched off."""
    from pzi.pdf import fetch_pdf_via_desktop_browser_download
    from pzi.pdf_planning import PdfFallbackSettings

    settings = PdfFallbackSettings.from_environment(
        {"PZI_DISABLE_DESKTOP_BROWSER_FALLBACK": "1"}
    )
    path, note = fetch_pdf_via_desktop_browser_download(
        url="https://example.com/p.pdf", papers_dir="/tmp", citekey="a2020",
        settings=settings,
    )
    assert path is None
    assert "skipped" in (note or "")


def test_the_server_browser_stage_says_it_was_unreachable(dead_port: int) -> None:
    """Reported as "no PDF returned", which asserts a server answered.

    `pdf_service` synthesizes an `api_url` from the listen host/port whenever
    config has none, so this stage runs whether or not `pzi server` is up.
    """
    from pzi.server_browser import download_via_server_api

    errors: list[str] = []
    result = download_via_server_api(
        f"http://127.0.0.1:{dead_port}", "https://example.com/p.pdf",
        timeout=2, errors=errors,
    )
    assert result is None
    assert errors and "not reachable" in errors[0]


def test_a_non_http_scheme_is_refused_not_a_traceback() -> None:
    """`opener.open` returns None for an unhandled scheme → `with None as …`.

    A `TypeError` caught nowhere: traceback, no JSON, exit 1 — and 1 is
    FINDINGS, so a script reads the crash as a clean result.
    """
    import urllib.request

    from pzi.safe_http import SsrfBlocked, safe_urlopen

    for url in ("ftp://example.com/p.pdf", "file:///etc/passwd"):
        with pytest.raises(SsrfBlocked):
            safe_urlopen(urllib.request.Request(url), timeout=1)


def test_page_metadata_cmd_config_mistakes_are_messages_not_tracebacks() -> None:
    """Only `TimeoutExpired` was caught; three config mistakes escaped raw."""
    from pzi.errors import PziError
    from pzi.page_metadata_cmd import run_page_metadata_cmd

    common = {"url": "https://example.com", "html": "<html></html>", "current_metadata": {}}

    # A whitespace-only config value: shlex.split(" ") == [] → run([]) → IndexError.
    with pytest.raises(PziError):
        run_page_metadata_cmd("   ", **common)
    # An unclosed quote: shlex.split raises ValueError.
    with pytest.raises(PziError):
        run_page_metadata_cmd('sh -c "echo', **common)
    # A binary that is not there: FileNotFoundError.
    with pytest.raises(PziError):
        run_page_metadata_cmd("/nonexistent/pzi-hook-binary", **common)


def test_import_reports_a_merge_as_an_update_not_a_skipped_duplicate(
    tmp_path: Path, write_app_config
) -> None:
    """"imported 0/1, skipped 1 duplicate" while the entry gained an abstract."""
    from pzi.import_service import import_from_bibtex

    config_path = write_app_config(tmp_path)
    entry = (
        "@article{smith2024,\n"
        "  title = {Deep Learning},\n"
        "  author = {Smith, John},\n"
        "  year = {2024},\n"
        "  doi = {10.1000/test},\n"
    )
    (tmp_path / "ml.bib").write_text(entry + "}\n", encoding="utf-8")
    source = tmp_path / "src.bib"
    source.write_text(
        entry + "  abstract = {Something the library did not have.},\n}\n",
        encoding="utf-8",
    )

    result = import_from_bibtex(
        config_path=config_path, home_dir=str(tmp_path), source_path=str(source)
    )

    assert result["status"] == "ok"
    assert result["updated"] == 1
    assert result["skipped_duplicates"] == 0
    assert result["results"][0]["status"] == "updated"
    assert "abstract" in result["results"][0]["changed_fields"]


def test_promote_counts_only_the_resolved_markers_that_landed(tmp_path: Path) -> None:
    """A failed tag write claimed success, so the next run re-promoted."""
    from pzi import promote_service

    calls: list[str] = []

    def _failing_add_tags(*, citekey: str, **_kw):
        calls.append(citekey)
        return {"status": "error", "message": "bib is read-only"}

    original = promote_service.add_tags
    promote_service.add_tags = _failing_add_tags
    try:
        tagged, failures = promote_service._tag_resolved(
            config_path="c", home_dir="h", bib_selector=None, citekeys=["a2020", "b2021"],
        )
    finally:
        promote_service.add_tags = original

    assert calls == ["a2020", "b2021"]
    assert tagged == 0
    assert len(failures) == 2
    assert "could not mark resolved" in failures[0]


def test_metadata_exhaustion_carries_the_provider_errors(tmp_path: Path) -> None:
    """Returned only on the success path, so a total failure had no evidence."""
    from pzi.add_planning import MetadataExhausted

    exc = MetadataExhausted("no metadata found for DOI: 10.1/x", ["HTTP 429", "HTTP 503"])
    assert isinstance(exc, ValueError)
    assert exc.provider_errors == ["HTTP 429", "HTTP 503"]


def test_discovery_records_which_step_failed() -> None:
    """A permanently broken provider looked like a paper with no OA copy."""
    from pzi.pdf_discovery import (
        apply_pdf_discovery,
        discovery_diagnostics,
    )

    def unpaywall_step(_record, _context):
        raise RuntimeError("unpaywall: 422 invalid email")

    context: dict = {}
    record = apply_pdf_discovery({"title": "T"}, [unpaywall_step], context)  # type: ignore[arg-type]

    assert not record.get("pdf_url")
    failures = discovery_diagnostics(context)
    assert len(failures) == 1
    assert "unpaywall_step" in failures[0]
    assert "422 invalid email" in failures[0]
