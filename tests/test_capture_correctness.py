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

    **Assertion changed on purpose (audit C6).** This pinned `kind == "url"`
    resolving to the abs page, which was the very divergence C6 reports: the
    same paper pasted as a URL classified as `doi` with the DataCite DOI, so
    the two spellings took different pipelines and the bare form got neither a
    DOI nor the provider cascade. The property this test exists for — a bare ID
    is accepted at all, and prose still is not — is unchanged. The agreement
    between the spellings is pinned in
    `test_every_spelling_of_one_arxiv_paper_classifies_alike`.
    """
    from pzi.identifiers import classify_input

    for value in ("2301.07041", "arXiv:2301.07041v2", "arxiv:2301.07041", "math.GT/0309136"):
        classified = classify_input(value)
        assert classified["kind"] == "doi", value
        assert (classified["normalized"] or "").startswith("10.48550/arxiv."), value

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


def _add_config(tmp_path: Path) -> str:
    config_path = tmp_path / "config.toml"
    (tmp_path / "papers").mkdir()
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1"

[[bibs]]
name = "ml"
path = "{tmp_path / "library.bib"}"
papers_dir = "{tmp_path / "papers"}"
default = true
""".strip(),
        encoding="utf-8",
    )
    return str(config_path)


@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict-metadata"])
def test_a_doi_no_provider_knows_is_not_a_dead_service(tmp_path: Path, strict: bool) -> None:
    """"Retry later" was the answer to a DOI that will never resolve.

    Every source was tried and every one answered "I do not have this" —
    `MetadataExhausted`. It subclasses `ValueError`, so it landed in the
    transport-failure handler and came back classified `unavailable`: exit 5 on
    the CLI and HTTP 503 on the API, telling the caller to retry a lookup whose
    verdict will not change. The same verdict reached from the other direction
    ("no metadata found for this input") carries no reason at all.
    """
    from pzi.add_service import add_input_to_bib
    from pzi.errors import REASON_UNAVAILABLE
    from pzi.http_status import status_for_service_result

    result = add_input_to_bib(
        config_path=_add_config(tmp_path),
        home_dir=str(tmp_path),
        value="10.1234/nobody.knows.this",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=lambda *_a, **_k: [],
        fetch_search=lambda *_a, **_k: [],
        fetch_crossref=lambda *_a, **_k: None,
        fetch_openalex=lambda *_a, **_k: None,
        fetch_s2=lambda *_a, **_k: None,
        metadata_strict=strict,
    )

    assert result["status"] == "error"
    assert result.get("reason") != REASON_UNAVAILABLE, result
    assert status_for_service_result(result) == 400


def test_a_translation_server_that_is_actually_down_is_still_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contrast case the classification exists for: retrying *is* reasonable.

    The cascade absorbs transport failures at every seam it injects, so the only
    way to put a bare `URLError` in front of this handler is to raise it where
    the cascade itself is called.
    """
    import urllib.error

    from pzi import add_service
    from pzi.errors import REASON_UNAVAILABLE
    from pzi.http_status import status_for_service_result

    def _refused(**_kwargs):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(add_service, "fetch_record_for_input", _refused)

    result = add_service.add_input_to_bib(
        config_path=_add_config(tmp_path),
        home_dir=str(tmp_path),
        value="10.1234/somewhere",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
    )

    assert result["status"] == "error"
    assert result["message"] == "translation server error"
    assert result.get("reason") == REASON_UNAVAILABLE, result
    assert status_for_service_result(result) == 503


def _orphan_pdf_record() -> dict[str, object]:
    return {
        "citekey": "orphan2024pdf",
        "title": "A Paper Whose PDF Is Already On Disk",
        "authors": ["Smith, Ada"],
        "year": 2024,
        "doi": "10.1234/orphan",
        "pdf_url": "https://example.com/orphan.pdf",
    }


def _plant_orphan_pdf(papers_dir: Path, record: dict[str, object]) -> Path:
    """Write a real PDF where the planner would put this record's."""
    from pzi.pdf_planning import plan_pdf_path

    planned = Path(
        plan_pdf_path(
            papers_dir=str(papers_dir),
            citekey=str(record["citekey"]),
            record=record,  # type: ignore[arg-type]
            filename_format=None,
        )
    )
    planned.parent.mkdir(parents=True, exist_ok=True)
    planned.write_bytes(b"%PDF-1.4\n%stub\n")
    return planned


@pytest.mark.parametrize("path", ["single", "batch"])
def test_every_write_path_prepares_a_record_the_same_way(tmp_path: Path, path: str) -> None:
    """One record-preparation pipeline, not one per write path.

    Citekey, exact-match PDF reuse and orphan-PDF reuse were written out three
    times, and two of the copies had already drifted apart by a step. This pins
    the last of the three — adopting a PDF already sitting at the planned path,
    rather than re-downloading it — across both public write entry points.
    """
    from pzi.add_service import add_record_with_bib, add_records_to_bib_batch

    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("", encoding="utf-8")
    bib = {"name": "ml", "path": str(bib_path), "papers_dir": str(papers_dir), "default": True}

    record = _orphan_pdf_record()
    planted = _plant_orphan_pdf(papers_dir, record)

    if path == "single":
        result = add_record_with_bib(bib=bib, record=dict(record), dry_run=False)
    else:
        result = add_records_to_bib_batch(
            bib=bib, records=[dict(record)], dry_run=False
        )[0]

    assert result["status"] == "ok", result
    assert result["pdf_path"] == str(planted), result
