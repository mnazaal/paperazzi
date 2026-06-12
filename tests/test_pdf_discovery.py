import pytest

from pzi.pdf_discovery import (
    DEFAULT_DISCOVERY_STEPS,
    PdfDiscoveryContext,
    apply_pdf_discovery,
    apply_pdf_discovery_parallel,
    arxiv_step,
    browser_pdf_step,
    doi_pdf_step,
    pdf_url_candidates_step,
    translation_attachment_step,
    unpaywall_step,
    web_attachment_step,
)


def test_apply_pdf_discovery_stops_when_pdf_url_found() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    def first_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://example.com/first.pdf"
        return updated

    def second_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://example.com/second.pdf"
        return updated

    result = apply_pdf_discovery(record, [first_step, second_step], context)
    assert result["pdf_url"] == "https://example.com/first.pdf"


def test_apply_pdf_discovery_runs_all_steps_when_no_match() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    def add_tag(r, c):
        updated = dict(r)
        updated["tag"] = "found"
        return updated

    result = apply_pdf_discovery(record, [add_tag], context)
    assert result.get("tag") == "found"
    assert "pdf_url" not in result




@pytest.mark.parametrize(
    "step,context,expected_key,expected_value",
    [
        (
            translation_attachment_step,
            {"translation_attachments": [{"url": "https://example.com/paper.pdf", "title": "PDF"}]},
            "pdf_url",
            "https://example.com/paper.pdf",
        ),
        (
            pdf_url_candidates_step,
            {"pdf_url_candidates": ["", "  ", "https://example.com/candidate.pdf"]},
            "pdf_url",
            "https://example.com/candidate.pdf",
        ),
        (
            arxiv_step,
            {},
            "pdf_url",
            "https://arxiv.org/pdf/2401.12345",
        ),
        (
            arxiv_step,
            {},
            "pdf_url",
            "https://arxiv.org/pdf/2401.12345",
        ),
    ],
    ids=[
        "translation_attachment_extracts_first",
        "pdf_url_candidates_uses_first_valid",
        "arxiv_builds_url",
        "arxiv_strips_prefix",
    ],
)
def test_discovery_step_extracts_value(step, context, expected_key, expected_value) -> None:
    record = (
        {"title": "Paper", "arxiv_id": "2401.12345"}
        if step is arxiv_step
        else {"title": "Paper"}
    )
    if step is arxiv_step and "arXiv:" in str(context):
        record = {"title": "Paper", "arxiv_id": "arXiv:2401.12345"}
    result = step(record, context)
    assert result[expected_key] == expected_value


@pytest.mark.parametrize(
    "step,context",
    [
        (translation_attachment_step, {"translation_attachments": [{"title": "PDF"}]}),
        (translation_attachment_step, {"translation_attachments": []}),
        (pdf_url_candidates_step, {"pdf_url_candidates": []}),
        (unpaywall_step, {"unpaywall_email": "test@example.com"}),
        (unpaywall_step, {}),
        (arxiv_step, {}),
        (doi_pdf_step, {}),
        (browser_pdf_step, {"browser_pdf_cmd": None, "raw_value": ""}),
    ],
    ids=[
        "translation_skips_missing_url",
        "translation_skips_empty_attachments",
        "pdf_candidates_no_candidates",
        "unpaywall_no_doi",
        "unpaywall_no_email",
        "arxiv_no_id",
        "doi_pdf_no_doi",
        "browser_pdf_no_cmd",
    ],
)
def test_discovery_step_returns_unchanged(step, context) -> None:
    record = {"title": "Paper", "doi": "10.1/foo"} if step is unpaywall_step else {"title": "Paper"}
    result = step(record, context)
    assert result == record


def test_pdf_url_candidates_step_uses_first_valid() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "pdf_url_candidates": ["", "  ", "https://example.com/candidate.pdf"]
    }
    result = pdf_url_candidates_step(record, context)
    assert result["pdf_url"] == "https://example.com/candidate.pdf"


def test_pdf_url_candidates_step_no_candidates() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {"pdf_url_candidates": []}
    result = pdf_url_candidates_step(record, context)
    assert result == record


def test_web_attachment_step_fetches_and_extracts() -> None:
    record = {"canonical_url": "https://example.com/paper"}

    def fake_fetch_web(url, *, server_url):
        assert url == "https://example.com/paper"
        return [
            {
                "record": {"source_url": "https://example.com/paper"},
                "attachments": [
                    {"url": "https://example.com/paper.pdf"}
                ],
            }
        ]

    context: PdfDiscoveryContext = {
        "raw_value": "https://example.com/paper",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["source_url"] == "https://example.com/paper"


def test_web_attachment_step_backfills_missing_fields() -> None:
    record = {"title": "Paper"}

    def fake_fetch_web(url, *, server_url):
        return [
            {
                "record": {
                    "canonical_url": "https://example.com/canonical",
                    "source_url": "https://example.com/source",
                    "abstract_url": "https://example.com/abstract",
                },
                "attachments": [{"url": "https://example.com/paper.pdf"}],
            }
        ]

    context: PdfDiscoveryContext = {
        "raw_value": "https://example.com/paper",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result["pdf_url"] == "https://example.com/paper.pdf"
    assert result["canonical_url"] == "https://example.com/canonical"
    assert result["source_url"] == "https://example.com/source"
    assert result["abstract_url"] == "https://example.com/abstract"


def test_web_attachment_step_does_not_overwrite_existing() -> None:
    record = {
        "canonical_url": "https://example.com/paper",
        "source_url": "https://example.com/existing",
    }

    def fake_fetch_web(url, *, server_url):
        return [
            {
                "record": {"source_url": "https://example.com/new"},
                "attachments": [{"url": "https://example.com/paper.pdf"}],
            }
        ]

    context: PdfDiscoveryContext = {
        "raw_value": "https://example.com/paper",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result["source_url"] == "https://example.com/existing"


def test_web_attachment_step_no_attachments() -> None:
    record = {"canonical_url": "https://example.com/paper"}

    def fake_fetch_web(url, *, server_url):
        return [{"record": {}, "attachments": []}]

    context: PdfDiscoveryContext = {
        "raw_value": "https://example.com/paper",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_fetch_failure() -> None:
    record = {"canonical_url": "https://example.com/paper"}

    def fake_fetch_web(url, *, server_url):
        raise OSError("network error")

    context: PdfDiscoveryContext = {
        "raw_value": "https://example.com/paper",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_browser_pdf_step_no_cmd() -> None:
    record = {"doi": "10.1/foo"}
    context: PdfDiscoveryContext = {"browser_pdf_cmd": None, "raw_value": ""}
    result = browser_pdf_step(record, context)
    assert result == record


def test_unpaywall_step_no_doi() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {"unpaywall_email": "test@example.com"}
    result = unpaywall_step(record, context)
    assert result == record


def test_unpaywall_step_no_email() -> None:
    record = {"doi": "10.1/foo"}
    context: PdfDiscoveryContext = {}
    result = unpaywall_step(record, context)
    assert result == record


def test_arxiv_step_builds_url() -> None:
    record = {"arxiv_id": "2401.12345"}
    context: PdfDiscoveryContext = {}
    result = arxiv_step(record, context)
    assert result["pdf_url"] == "https://arxiv.org/pdf/2401.12345"


def test_arxiv_step_strips_prefix() -> None:
    record = {"arxiv_id": "arXiv:2401.12345"}
    context: PdfDiscoveryContext = {}
    result = arxiv_step(record, context)
    assert result["pdf_url"] == "https://arxiv.org/pdf/2401.12345"


def test_arxiv_step_no_id() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {}
    result = arxiv_step(record, context)
    assert result == record


def test_doi_pdf_step_no_doi() -> None:
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {}
    result = doi_pdf_step(record, context)
    assert result == record


def test_default_steps_list_has_all_steps() -> None:
    names = [step.__name__ for step in DEFAULT_DISCOVERY_STEPS]
    assert names == [
        "arxiv_step",
        "preprint_pdf_step",
        "translation_attachment_step",
        "web_attachment_step",
        "doi_pdf_step",
        "unpaywall_step",
        "pdf_url_candidates_step",
        "browser_pdf_step",
    ]


# --- Parallel discovery tests ---


def test_parallel_stops_when_pure_step_finds_pdf() -> None:
    """Phase 1 pure step finds a PDF → parallel variant returns immediately."""
    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    def arxiv_like(r, c):
        arxiv_like.__name__ = "arxiv_step"
        updated = dict(r)
        updated["pdf_url"] = "https://arxiv.org/pdf/1234.pdf"
        return updated

    result = apply_pdf_discovery_parallel(record, [arxiv_like], context)
    assert result["pdf_url"] == "https://arxiv.org/pdf/1234.pdf"


def test_parallel_falls_back_to_browser() -> None:
    """HTTP steps find nothing → browser step runs as final fallback."""
    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    def http_step(r, c):
        http_step.__name__ = "web_attachment_step"
        return r  # no-op

    def browser_like(r, c):
        browser_like.__name__ = "browser_pdf_step"
        updated = dict(r)
        updated["pdf_url"] = "https://example.com/browser.pdf"
        return updated

    result = apply_pdf_discovery_parallel(record, [http_step, browser_like], context)
    assert result["pdf_url"] == "https://example.com/browser.pdf"


def test_parallel_returns_first_http_result() -> None:
    """Multiple HTTP steps run in parallel; first success wins."""
    import time

    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    def slow_http(r, c):
        slow_http.__name__ = "web_attachment_step"
        time.sleep(0.2)
        updated = dict(r)
        updated["pdf_url"] = "https://slow.example.com/pdf"
        return updated

    def fast_http(r, c):
        fast_http.__name__ = "doi_pdf_step"
        updated = dict(r)
        updated["pdf_url"] = "https://fast.example.com/pdf"
        return updated

    result = apply_pdf_discovery_parallel(
        record, [fast_http, slow_http], context, max_workers=2,
    )
    assert result["pdf_url"] == "https://fast.example.com/pdf"


def test_parallel_handles_http_step_exceptions() -> None:
    """An HTTP step that raises does not crash the pipeline."""
    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    def failing_http(r, c):
        failing_http.__name__ = "web_attachment_step"
        raise RuntimeError("network error")

    def working_browser(r, c):
        working_browser.__name__ = "browser_pdf_step"
        updated = dict(r)
        updated["pdf_url"] = "https://example.com/browser.pdf"
        return updated

    result = apply_pdf_discovery_parallel(
        record, [failing_http, working_browser], context,
    )
    assert result["pdf_url"] == "https://example.com/browser.pdf"
