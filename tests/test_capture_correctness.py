"""Captures that wrote something other than what was asked for.

Distinct from `test_silent_failures.py`: these runs reported success and the
library disagreed with the report.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_a_bare_arxiv_id_is_accepted(tmp_path: Path) -> None:
    """The identifier arXiv papers are actually cited by was "not a DOI, URL…".

    `normalize_arxiv_id` has handled prefixes, versions and old-style subject
    classes all along; `classify_input` never consulted it.
    """
    from pzi.identifiers import classify_input

    for value in ("2301.07041", "arXiv:2301.07041v2", "arxiv:2301.07041", "math.GT/0309136"):
        classified = classify_input(value)
        assert classified["kind"] == "url", value
        assert "arxiv.org/abs/" in (classified["normalized"] or ""), value

    # Still not a paper identifier.
    assert classify_input("just some words")["kind"] == "unknown"


def test_a_text_file_named_pdf_is_refused(tmp_path: Path) -> None:
    """Extension and existence were the whole check.

    A text file named `.pdf` wrote an empty `@article{unknownxxxxuntitled}`
    placeholder and exited 0.
    """
    from pzi.add_service import describe_invalid_add_input

    impostor = tmp_path / "paper.pdf"
    impostor.write_text("This is not a PDF at all.\n", encoding="utf-8")
    assert "not a PDF" in (describe_invalid_add_input(str(impostor)) or "")

    real = tmp_path / "real.pdf"
    real.write_bytes(b"%PDF-1.7\n%\xc7\xec\x8f\xa2\n")
    assert describe_invalid_add_input(str(real)) is None


def test_a_local_pdf_dry_run_names_the_file_the_real_run_writes(tmp_path: Path) -> None:
    """`--dry-run` previewed an entry with no `file =` line; the real run adds one."""
    from pzi.capture_local_pdf import copy_local_pdf_after_citekey

    source = tmp_path / "in.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    papers = tmp_path / "papers"
    papers.mkdir()

    record, warnings, copied = copy_local_pdf_after_citekey(
        record={"citekey": "smith2024", "title": "T"},  # type: ignore[arg-type]
        source_path=str(source),
        papers_dir=str(papers),
        dry_run=True,
    )

    assert warnings == []
    # Nothing was written…
    assert copied is None
    assert list(papers.iterdir()) == []
    # …but the preview names what would be.
    assert "smith2024" in str(record["local_pdf_path"])


def test_a_renamed_citekey_is_reported(tmp_path: Path) -> None:
    """`--citekey mykey` silently became `mykey-2`, with `warnings: []`.

    The user's `\\cite{mykey}` then points at nothing.
    """
    from pzi.add_service import ensure_citekey_for_write

    existing = [{"citekey": "mykey", "title": "Existing", "doi": "10.1/a"}]
    result = ensure_citekey_for_write(
        {"citekey": "mykey", "title": "Different Paper", "doi": "10.1/b"},  # type: ignore[arg-type]
        existing,  # type: ignore[arg-type]
    )
    assert result["citekey"] == "mykey-2"
    assert result["_citekey_renamed_from"] == "mykey"


def test_a_rate_limit_body_is_not_cached_as_a_result() -> None:
    """S2 reports a quota refusal as HTTP *200* with an `error` body.

    Caching every 200 froze a transient rate limit into a permanent one for the
    whole TTL: three lookups, one network call, the same refusal each time.
    """
    from pzi.fetch_helpers import _is_transient_error_body

    assert _is_transient_error_body('{"error": "Rate limit exceeded"}')
    assert _is_transient_error_body('{"message": "Too Many Requests"}')
    # A real record is cacheable, even one whose text mentions rate limiting.
    assert not _is_transient_error_body(
        '{"title": "A Study of Rate Limit Exceeded Errors", "year": 2024}'
    )
    assert not _is_transient_error_body("not json at all")


def test_each_secret_command_failure_names_its_own_config_key() -> None:
    """One failure could have been any of five `*_cmd` keys and named none.

    `resolve_optional_value` has taken a `config_key` all along —
    `build_capture_context` is what called it without one, so the defect is in
    the caller and this drives the caller.
    """
    from pzi.capture_context import build_capture_context
    from pzi.errors import PziError

    config = {
        "contact_email_cmd": None,
        "contact_email": None,
        "unpaywall_email_cmd": "false",
        "unpaywall_email": None,
        "semantic_scholar_api_key_cmd": None,
        "semantic_scholar_api_key": None,
    }
    bib = {"name": "ml", "path": "/tmp/ml.bib", "papers_dir": "/tmp/papers", "default": True}

    with pytest.raises(PziError) as caught:
        build_capture_context(
            config=config,  # type: ignore[arg-type]
            bib=bib,  # type: ignore[arg-type]
            browser_pdf_cmd_override=None,
            browser=None,
        )
    assert "unpaywall_email_cmd" in str(caught.value)


def test_the_missing_title_message_does_not_blame_the_extension() -> None:
    """Printed by `pzi add 10.x/y` on the command line, which never involved it."""
    from pzi.add_planning import minimum_metadata_diagnostics

    lines = minimum_metadata_diagnostics({"doi": "10.1/x"})
    assert lines
    assert "browser extension" not in lines[0]


def test_non_pdf_bytes_are_never_stored_as_a_paper(tmp_path: Path) -> None:
    """The check that stops an HTML paywall page becoming a paper's PDF.

    It carried `# pragma: no cover — covered by integration/browser tests`,
    which was false (this call reaches it), and `pyproject.toml` excludes
    pragmas from the coverage gate — so the one guard against storing a login
    page as a PDF was exempt from the gate that would notice it breaking.
    """
    from pzi.pdf_download import fetch_and_store_pdf

    papers = tmp_path / "papers"
    papers.mkdir()

    path, error = fetch_and_store_pdf(
        url="https://example.com/paper.pdf",
        papers_dir=str(papers),
        citekey="smith2024",
        fetch_binary=lambda _url: (b"<!DOCTYPE html><html><body>Sign in</body></html>", "text/html"),
    )

    assert path is None
    assert "not a PDF" in (error or "")
    assert list(papers.iterdir()) == []

    # And with `application/pdf` claimed, which is the case that reaches the
    # pragma'd line: the content-type guard above it passes, so only the magic
    # bytes stand between a paywall page and the papers directory.
    lying_path, lying_error = fetch_and_store_pdf(
        url="https://example.com/paper.pdf",
        papers_dir=str(papers),
        citekey="smith2024",
        fetch_binary=lambda _url: (
            b"<!DOCTYPE html><html><body>Sign in</body></html>", "application/pdf"
        ),
    )
    assert lying_path is None
    assert "not a PDF" in (lying_error or "")
    assert list(papers.iterdir()) == []
