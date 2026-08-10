from pathlib import Path
from typing import cast

import pytest

from pzi.pdf_discovery import (
    DEFAULT_DISCOVERY_STEPS,
    PdfDiscoveryContext,
    apply_pdf_discovery,
    apply_pdf_discovery_parallel,
    arxiv_step,
    browser_pdf_step,
    discovery_phase,
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


def test_an_excluded_url_does_not_end_the_chain() -> None:
    """The download stage failed on the first URL, so discovery must go on.

    Without this the chain is deterministic: a second run picks the same
    winner, so the caller has no way to reach a later source.
    """
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "exclude_pdf_urls": ["https://blocked.example.com/paper.pdf"]
    }

    def blocked_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://blocked.example.com/paper.pdf"
        updated["pdf_source"] = "publisher"
        return updated

    def oa_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://oa.example.org/mirror.pdf"
        updated["pdf_source"] = "unpaywall"
        return updated

    result = apply_pdf_discovery(record, [blocked_step, oa_step], context)
    assert result["pdf_url"] == "https://oa.example.org/mirror.pdf"
    assert result["pdf_source"] == "unpaywall"


def test_an_excluded_url_already_on_the_record_is_dropped_before_any_step() -> None:
    """A re-run receives the record as it stands, failed URL and all.

    `apply_pdf_discovery` returns early when the record already has a
    `pdf_url`, so without validating the *incoming* record the second round
    would hand back the dead URL without running one step.
    """
    record = {
        "title": "Paper",
        "pdf_url": "https://blocked.example.com/paper.pdf",
        "pdf_source": "publisher",
    }
    context: PdfDiscoveryContext = {
        "exclude_pdf_urls": ["https://blocked.example.com/paper.pdf"]
    }

    def oa_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://oa.example.org/mirror.pdf"
        return updated

    result = apply_pdf_discovery(record, [oa_step], context)
    assert result["pdf_url"] == "https://oa.example.org/mirror.pdf"


def test_exclusion_applies_to_the_parallel_scheduler_too() -> None:
    """Both entry points, or the fix reaches one call site of two."""
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "exclude_pdf_urls": ["https://blocked.example.com/paper.pdf"]
    }

    @discovery_phase("http")
    def blocked_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://blocked.example.com/paper.pdf"
        return updated

    @discovery_phase("http")
    def oa_step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://oa.example.org/mirror.pdf"
        return updated

    result = apply_pdf_discovery_parallel(record, [blocked_step, oa_step], context)
    assert result["pdf_url"] == "https://oa.example.org/mirror.pdf"


def test_no_exclusions_leaves_the_winner_alone() -> None:
    """The guard must not become a way to lose a perfectly good URL."""
    record = {"title": "Paper"}

    def step(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://oa.example.org/mirror.pdf"
        return updated

    for context in ({}, {"exclude_pdf_urls": []}, {"exclude_pdf_urls": None}):
        result = apply_pdf_discovery(record, [step], cast(PdfDiscoveryContext, context))
        assert result["pdf_url"] == "https://oa.example.org/mirror.pdf"




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


def test_pdf_url_candidates_step_accepts_existing_local_pdf_path(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

    result = pdf_url_candidates_step({}, {"pdf_url_candidates": [str(pdf_path)]})

    assert result["pdf_url"] == str(pdf_path)
    assert result["pdf_source"] == "pdf_url_candidates"


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


def test_web_attachment_step_passes_cookie_bridge_to_translation_server() -> None:
    calls: list[dict[str, object]] = []

    def fake_fetch_web(
        url: str, *, server_url: str, cookies: str | None = None
    ) -> list[dict[str, object]]:
        calls.append({"url": url, "server_url": server_url, "cookies": cookies})
        if cookies != "sid=abc123":
            return []
        return [
            {
                "attachments": [
                    {
                        "url": "https://example.com/auth-paper.pdf",
                        "mime_type": "application/pdf",
                    }
                ]
            }
        ]

    result = web_attachment_step(
        {"source_url": "https://example.com/paper"},
        {
            "raw_value": "https://example.com/paper",
            "server_url": "http://localhost:1969",
            "fetch_web": fake_fetch_web,
            "cookies": "sid=abc123",
        },
    )

    assert calls[0]["cookies"] == "sid=abc123"
    assert result["pdf_url"] == "https://example.com/auth-paper.pdf"
    assert result["pdf_source"] == "web_attachment"


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

    @discovery_phase("pure")
    def arxiv_like(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://arxiv.org/pdf/1234.pdf"
        return updated

    result = apply_pdf_discovery_parallel(record, [arxiv_like], context)
    assert result["pdf_url"] == "https://arxiv.org/pdf/1234.pdf"


def test_parallel_falls_back_to_browser() -> None:
    """HTTP steps find nothing → browser step runs as final fallback."""
    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    @discovery_phase("http")
    def http_step(r, c):
        return r  # no-op

    @discovery_phase("browser")
    def browser_like(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://example.com/browser.pdf"
        return updated

    result = apply_pdf_discovery_parallel(record, [http_step, browser_like], context)
    assert result["pdf_url"] == "https://example.com/browser.pdf"


def test_parallel_winner_is_by_step_priority_not_completion() -> None:
    """HTTP steps run in parallel but the winner is chosen by fallback-chain
    position (source priority), not by whichever network call returns first.

    The higher-priority step is deliberately the *slower* one: if selection
    regressed to completion order, the faster low-priority step would win.
    """
    import time

    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    @discovery_phase("http")
    def slow_high_priority(r, c):  # earlier in the list
        time.sleep(0.05)
        updated = dict(r)
        updated["pdf_url"] = "https://high.example.com/pdf"
        return updated

    @discovery_phase("http")
    def fast_low_priority(r, c):  # later in the list, returns first
        updated = dict(r)
        updated["pdf_url"] = "https://low.example.com/pdf"
        return updated

    result = apply_pdf_discovery_parallel(
        record, [slow_high_priority, fast_low_priority], context, max_workers=2,
    )
    assert result["pdf_url"] == "https://high.example.com/pdf"


def test_parallel_handles_http_step_exceptions() -> None:
    """An HTTP step that raises does not crash the pipeline."""
    record: dict[str, object] = {"title": "Paper"}
    context: PdfDiscoveryContext = {}

    @discovery_phase("http")
    def failing_http(r, c):
        raise RuntimeError("network error")

    @discovery_phase("browser")
    def working_browser(r, c):
        updated = dict(r)
        updated["pdf_url"] = "https://example.com/browser.pdf"
        return updated

    result = apply_pdf_discovery_parallel(
        record, [failing_http, working_browser], context,
    )
    assert result["pdf_url"] == "https://example.com/browser.pdf"


def test_unpaywall_step_skips_without_email() -> None:
    from pzi.pdf_discovery import unpaywall_step
    record = {"doi": "10.1234/test"}
    context = {}
    result = unpaywall_step(record, context)
    assert result.get("pdf_url") is None


def test_unpaywall_step_uses_injected_fetch() -> None:
    from pzi.pdf_discovery import unpaywall_step
    record = {"doi": "10.1234/test"}
    context = {
        "unpaywall_email": "test@example.com",
        "fetch_unpaywall": lambda doi, email=None: "https://oa.example.com/paper.pdf",
    }
    result = unpaywall_step(record, context)
    assert result.get("pdf_url") == "https://oa.example.com/paper.pdf"
    assert result.get("pdf_source") == "unpaywall"


def test_doi_pdf_step_skips_without_doi() -> None:
    from pzi.pdf_discovery import doi_pdf_step
    record = {"title": "No DOI"}
    context = {}
    result = doi_pdf_step(record, context)
    assert result.get("pdf_url") is None


def test_web_attachment_step_no_attachments2() -> None:
    from pzi.pdf_discovery import web_attachment_step
    record = {"title": "Paper"}
    context = {
        "server_url": "http://127.0.0.1:1969",
        "raw_value": "https://example.com",
        "fetch_web": lambda url, server_url=None, **kw: [],
    }
    result = web_attachment_step(record, context)
    assert result.get("pdf_url") is None


# ---------------------------------------------------------------------------
# A discovered PDF URL is validated wherever it came from
# ---------------------------------------------------------------------------

_PRIVATE_URLS = [
    "http://127.0.0.1:8080/x.pdf",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/x.pdf",
    "file:///etc/passwd",
    "http://10.0.0.5/internal.pdf",
]


@pytest.mark.parametrize("bad_url", _PRIVATE_URLS)
def test_a_step_cannot_hand_back_a_private_pdf_url(bad_url: str) -> None:
    """Only the browser step validated what it found.

    The URL a DOI, Unpaywall or preprint step returns is supplied by a provider
    response or by the captured page, and the server then fetches it — or hands
    it to the extension to fetch with the user's cookies. One chokepoint covers
    every step, including any added later.
    """
    from pzi.pdf_discovery import apply_pdf_discovery

    def _rogue_step(record, _context):
        return {**record, "pdf_url": bad_url, "pdf_source": "doi"}

    result = apply_pdf_discovery({"citekey": "k1"}, [_rogue_step], {})

    assert result.get("pdf_url") is None


@pytest.mark.parametrize("bad_url", _PRIVATE_URLS)
def test_the_parallel_path_validates_the_same_way(bad_url: str) -> None:
    from pzi.pdf_discovery import apply_pdf_discovery_parallel, discovery_phase

    @discovery_phase("http")
    def _rogue_step(record, _context):
        return {**record, "pdf_url": bad_url, "pdf_source": "doi"}

    result = apply_pdf_discovery_parallel({"citekey": "k1"}, [_rogue_step], {})

    assert result.get("pdf_url") is None


def test_a_public_pdf_url_still_gets_through() -> None:
    from pzi.pdf_discovery import apply_pdf_discovery

    def _good_step(record, _context):
        return {**record, "pdf_url": "https://arxiv.org/pdf/2301.12345", "pdf_source": "arxiv"}

    result = apply_pdf_discovery({"citekey": "k1"}, [_good_step], {})

    assert result["pdf_url"] == "https://arxiv.org/pdf/2301.12345"
