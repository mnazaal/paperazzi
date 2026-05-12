"""Edge tests for pzi.pdf_discovery covering previously uncovered branches.

Covers missing lines in web_attachment_step, browser_pdf_step, doi_pdf_step,
unpaywall_step, arxiv_step, and apply_pdf_discovery.
"""


from pzi.pdf_discovery import (
    PdfDiscoveryContext,
    apply_pdf_discovery,
    arxiv_step,
    browser_pdf_step,
    doi_pdf_step,
    pdf_url_candidates_step,
    translation_attachment_step,
    unpaywall_step,
    web_attachment_step,
)

# ---------------------------------------------------------------------------
# apply_pdf_discovery: edge cases
# ---------------------------------------------------------------------------


def test_apply_pdf_discovery_empty_steps() -> None:
    """No steps → record returned unchanged."""
    record = {"title": "Paper"}
    result = apply_pdf_discovery(record, [], {})
    assert result == record


def test_apply_pdf_discovery_already_has_pdf_url() -> None:
    """Record already has pdf_url → no steps run."""
    record = {"title": "Paper", "pdf_url": "https://existing.com/p.pdf"}

    def should_not_run(r, c):
        raise AssertionError("step should not have run")

    result = apply_pdf_discovery(record, [should_not_run], {})
    assert result["pdf_url"] == "https://existing.com/p.pdf"


def test_apply_pdf_discovery_multiple_steps_second_wins() -> None:
    """First step doesn't set URL, second does."""
    record = {"title": "Paper"}

    def step1(r, c):
        return dict(r)

    def step2(r, c):
        r2 = dict(r)
        r2["pdf_url"] = "https://step2.com/p.pdf"
        return r2

    result = apply_pdf_discovery(record, [step1, step2], {})
    assert result["pdf_url"] == "https://step2.com/p.pdf"


# ---------------------------------------------------------------------------
# translation_attachment_step: edge cases
# ---------------------------------------------------------------------------


def test_translation_attachment_step_missing_context_key() -> None:
    """context has no translation_attachments key → return unchanged."""
    record = {"title": "Paper"}
    result = translation_attachment_step(record, {})
    assert result == record


def test_translation_attachment_step_none_attachments() -> None:
    """translation_attachments is None → return unchanged."""
    record = {"title": "Paper"}
    result = translation_attachment_step(record, {"translation_attachments": None})
    assert result == record


def test_translation_attachment_step_attachment_not_mapping() -> None:
    """First attachment is not a Mapping → return unchanged."""
    record = {"title": "Paper"}
    result = translation_attachment_step(
        record,
        {"translation_attachments": ["not a dict"]},
    )
    assert result == record


def test_translation_attachment_step_empty_url() -> None:
    """attachment['url'] is empty string → return unchanged."""
    record = {"title": "Paper"}
    result = translation_attachment_step(
        record,
        {"translation_attachments": [{"url": "", "title": "PDF"}]},
    )
    assert result == record


def test_translation_attachment_step_whitespace_url() -> None:
    """attachment['url'] is whitespace → return unchanged."""
    record = {"title": "Paper"}
    result = translation_attachment_step(
        record,
        {"translation_attachments": [{"url": "   ", "title": "PDF"}]},
    )
    assert result == record


def test_translation_attachment_step_url_not_string() -> None:
    """attachment['url'] is not a string → return unchanged."""
    record = {"title": "Paper"}
    result = translation_attachment_step(
        record,
        {"translation_attachments": [{"url": 42, "title": "PDF"}]},
    )
    assert result == record


def test_translation_attachment_step_strips_url() -> None:
    """URL has leading/trailing whitespace → stripped."""
    record = {"title": "Paper"}
    result = translation_attachment_step(
        record,
        {"translation_attachments": [{"url": "  https://example.com/p.pdf  "}]},
    )
    assert result["pdf_url"] == "https://example.com/p.pdf"


# ---------------------------------------------------------------------------
# pdf_url_candidates_step: edge cases
# ---------------------------------------------------------------------------


def test_pdf_url_candidates_step_missing_key() -> None:
    """No pdf_url_candidates in context → return unchanged."""
    record = {"title": "Paper"}
    result = pdf_url_candidates_step(record, {})
    assert result == record


def test_pdf_url_candidates_step_all_invalid() -> None:
    """All candidates are empty or non-strings → return unchanged."""
    record = {"title": "Paper"}
    result = pdf_url_candidates_step(
        record,
        {"pdf_url_candidates": ["", "  ", None, "\t\n"]},
    )
    assert result == record


def test_pdf_url_candidates_step_strips_candidate() -> None:
    """Candidate has whitespace → stripped."""
    record = {"title": "Paper"}
    result = pdf_url_candidates_step(
        record,
        {"pdf_url_candidates": ["  https://example.com/p.pdf  "]},
    )
    assert result["pdf_url"] == "https://example.com/p.pdf"


def test_pdf_url_candidates_step_skips_non_string() -> None:
    """Candidate is not a string → skipped, next used."""
    record = {"title": "Paper"}
    result = pdf_url_candidates_step(
        record,
        {"pdf_url_candidates": [42, "https://example.com/p.pdf"]},
    )
    assert result["pdf_url"] == "https://example.com/p.pdf"


# ---------------------------------------------------------------------------
# web_attachment_step: edge cases
# ---------------------------------------------------------------------------


def test_web_attachment_step_no_candidate_urls(monkeypatch) -> None:
    """landing_page_urls returns no candidates → return unchanged."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: [],
    )
    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": lambda url, server_url: [],
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_no_attachments_in_result(monkeypatch) -> None:
    """Result dict has no 'attachments' key → skipped."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        return [{"record": {}}]

    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_empty_attachments_list(monkeypatch) -> None:
    """First result has attachments=[] → skipped."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        return [{"record": {}, "attachments": []}]

    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_attachment_not_mapping(monkeypatch) -> None:
    """First attachment is not a Mapping → skipped."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        return [{"record": {}, "attachments": ["not a dict"]}]

    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_empty_pdf_url_in_attachment(monkeypatch) -> None:
    """Attachment has url="" → skipped."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        return [{"record": {}, "attachments": [{"url": ""}]}]

    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_oserror_on_fetch(monkeypatch) -> None:
    """fetch_web raises OSError → skipped, move to next URL."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        raise OSError("connection refused")

    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result == record


def test_web_attachment_step_value_error_on_fetch(monkeypatch) -> None:
    """fetch_web raises ValueError → skipped."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        raise ValueError("bad response")

    result = web_attachment_step(
        {"title": "Paper"},
        {
            "raw_value": "",
            "server_url": "http://ts:1969",
            "fetch_web": fake_fetch_web,
        },
    )
    assert result == {"title": "Paper"}


def test_web_attachment_step_tries_multiple_urls(monkeypatch) -> None:
    """First URL fails, second succeeds."""
    urls_called = []

    def fake_landing_page_urls(base_record, raw_value):
        return ["https://fail.example.com", "https://ok.example.com"]

    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        fake_landing_page_urls,
    )

    def fake_fetch_web(url, *, server_url):
        urls_called.append(url)
        if "fail" in url:
            raise OSError("fail")
        return [{"record": {}, "attachments": [{"url": "https://ok.example.com/p.pdf"}]}]

    record = {"title": "Paper"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result["pdf_url"] == "https://ok.example.com/p.pdf"
    assert urls_called == ["https://fail.example.com", "https://ok.example.com"]


def test_web_attachment_step_backfills_only_missing_keys(monkeypatch) -> None:
    """When result has record with fields, only missing ones are backfilled."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        return [
            {
                "record": {
                    "canonical_url": "https://example.com/canon",
                    "source_url": "",
                    "abstract_url": "https://example.com/abs",
                },
                "attachments": [{"url": "https://example.com/p.pdf"}],
            }
        ]

    record = {"title": "Paper", "source_url": "https://existing.com/src"}
    context: PdfDiscoveryContext = {
        "raw_value": "",
        "server_url": "http://ts:1969",
        "fetch_web": fake_fetch_web,
    }
    result = web_attachment_step(record, context)
    assert result["canonical_url"] == "https://example.com/canon"
    assert result["source_url"] == "https://existing.com/src"  # NOT overwritten
    assert result["abstract_url"] == "https://example.com/abs"


def test_web_attachment_step_multiple_results_first_with_attachment_wins(monkeypatch) -> None:
    """First result has no attachments, second does."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/paper"],
    )

    def fake_fetch_web(url, *, server_url):
        return [
            {"record": {}, "attachments": []},
            {"record": {}, "attachments": [{"url": "https://example.com/paper.pdf"}]},
        ]

    result = web_attachment_step(
        {"title": "Paper"},
        {
            "raw_value": "",
            "server_url": "http://ts:1969",
            "fetch_web": fake_fetch_web,
        },
    )
    assert result["pdf_url"] == "https://example.com/paper.pdf"


# ---------------------------------------------------------------------------
# browser_pdf_step: edge cases
# ---------------------------------------------------------------------------


def test_browser_pdf_step_missing_cmd_key() -> None:
    """context has no browser_pdf_cmd key → return unchanged."""
    record = {"title": "Paper", "doi": "10.1/foo"}
    result = browser_pdf_step(record, {"raw_value": ""})
    assert result == record


def test_browser_pdf_step_cmd_is_none() -> None:
    """browser_pdf_cmd is None → return unchanged."""
    record = {"title": "Paper", "doi": "10.1/foo"}
    result = browser_pdf_step(record, {"browser_pdf_cmd": None, "raw_value": ""})
    assert result == record


def test_browser_pdf_step_no_landing_urls(monkeypatch) -> None:
    """landing_page_urls returns empty → return unchanged."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: [],
    )
    record = {"title": "Paper"}
    result = browser_pdf_step(
        record,
        {"browser_pdf_cmd": ["echo"], "raw_value": ""},
    )
    assert result == record


def test_browser_pdf_step_discovers_pdf(monkeypatch) -> None:
    """browser discovers a PDF URL → returned."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/page"],
    )
    monkeypatch.setattr(
        "pzi.browser_pdf.discover_pdf_url_with_browser",
        lambda command, page_url, doi: "https://example.com/p.pdf",
    )
    record = {"title": "Paper", "doi": "10.1/foo"}
    result = browser_pdf_step(
        record,
        {"browser_pdf_cmd": ["echo"], "raw_value": "", "doi": "10.1/foo"},
    )
    assert result["pdf_url"] == "https://example.com/p.pdf"


def test_browser_pdf_step_no_doi(monkeypatch) -> None:
    """Record has no doi → doi=None passed to discover function."""
    monkeypatch.setattr(
        "pzi.pdf_acquisition.landing_page_urls",
        lambda base_record, raw_value: ["https://example.com/page"],
    )
    capture = {}

    def fake_discover(command, page_url, doi):
        capture["doi"] = doi
        capture["page_url"] = page_url
        return "https://example.com/p.pdf"

    monkeypatch.setattr(
        "pzi.browser_pdf.discover_pdf_url_with_browser",
        fake_discover,
    )
    record = {"title": "Paper"}  # no doi
    result = browser_pdf_step(
        record,
        {"browser_pdf_cmd": ["echo"], "raw_value": ""},
    )
    assert result["pdf_url"] == "https://example.com/p.pdf"
    assert capture["doi"] is None


# ---------------------------------------------------------------------------
# doi_pdf_step: edge cases
# ---------------------------------------------------------------------------


def test_doi_pdf_step_no_doi() -> None:
    """No DOI → return unchanged."""
    record = {"title": "Paper"}
    result = doi_pdf_step(record, {})
    assert result == record


def test_doi_pdf_step_empty_doi() -> None:
    """DOI is empty string → return unchanged."""
    record = {"doi": ""}
    result = doi_pdf_step(record, {})
    assert result == record


def test_doi_pdf_step_whitespace_doi() -> None:
    """DOI is whitespace → return unchanged."""
    record = {"doi": "   "}
    result = doi_pdf_step(record, {})
    assert result == record


def test_doi_pdf_step_crossref_returns_url(monkeypatch) -> None:
    """crossref returns a PDF URL → use it."""
    monkeypatch.setattr(
        "pzi.crossref.fetch_crossref_pdf_url",
        lambda doi: "https://crossref.example.com/p.pdf",
    )
    record = {"doi": "10.1/foo"}
    result = doi_pdf_step(record, {})
    assert result["pdf_url"] == "https://crossref.example.com/p.pdf"


def test_doi_pdf_step_europepmc_returns_url(monkeypatch) -> None:
    """crossref returns None, europepmc returns URL."""
    monkeypatch.setattr(
        "pzi.crossref.fetch_crossref_pdf_url",
        lambda doi: None,
    )
    monkeypatch.setattr(
        "pzi.europepmc.fetch_europepmc_pdf_url",
        lambda doi: "https://europepmc.example.com/p.pdf",
    )
    record = {"doi": "10.1/foo"}
    result = doi_pdf_step(record, {})
    assert result["pdf_url"] == "https://europepmc.example.com/p.pdf"


def test_doi_pdf_step_doaj_returns_url(monkeypatch) -> None:
    """crossref and europepmc fail, doaj returns URL."""
    monkeypatch.setattr(
        "pzi.crossref.fetch_crossref_pdf_url",
        lambda doi: None,
    )
    monkeypatch.setattr(
        "pzi.europepmc.fetch_europepmc_pdf_url",
        lambda doi: None,
    )
    monkeypatch.setattr(
        "pzi.doaj.fetch_doaj_pdf_url",
        lambda doi: "https://doaj.example.com/p.pdf",
    )
    record = {"doi": "10.1/foo"}
    result = doi_pdf_step(record, {})
    assert result["pdf_url"] == "https://doaj.example.com/p.pdf"


def test_doi_pdf_step_all_return_none(monkeypatch) -> None:
    """All resolvers return None → record unchanged."""
    monkeypatch.setattr(
        "pzi.crossref.fetch_crossref_pdf_url",
        lambda doi: None,
    )
    monkeypatch.setattr(
        "pzi.europepmc.fetch_europepmc_pdf_url",
        lambda doi: None,
    )
    monkeypatch.setattr(
        "pzi.doaj.fetch_doaj_pdf_url",
        lambda doi: None,
    )
    record = {"doi": "10.1/foo", "title": "Paper"}
    result = doi_pdf_step(record, {})
    assert result == record


# ---------------------------------------------------------------------------
# unpaywall_step: edge cases
# ---------------------------------------------------------------------------


def test_unpaywall_step_no_doi() -> None:
    """No DOI → return unchanged."""
    record = {"title": "Paper"}
    result = unpaywall_step(record, {"unpaywall_email": "test@example.com"})
    assert result == record


def test_unpaywall_step_no_email() -> None:
    """DOI present but no email → return unchanged."""
    record = {"doi": "10.1/foo"}
    result = unpaywall_step(record, {})
    assert result == record


def test_unpaywall_step_fetch_returns_none(monkeypatch) -> None:
    """Unpaywall fetch returns None → record unchanged."""
    monkeypatch.setattr(
        "pzi.pdf.fetch_unpaywall_pdf_url",
        lambda doi, email: None,
    )
    record = {"doi": "10.1/foo"}
    result = unpaywall_step(record, {"unpaywall_email": "test@example.com"})
    assert result == record


def test_unpaywall_step_fetch_returns_url(monkeypatch) -> None:
    """Unpaywall fetch returns URL → set pdf_url."""
    monkeypatch.setattr(
        "pzi.pdf.fetch_unpaywall_pdf_url",
        lambda doi, email: "https://unpaywall.example.com/p.pdf",
    )
    record = {"doi": "10.1/foo"}
    result = unpaywall_step(record, {"unpaywall_email": "test@example.com"})
    assert result["pdf_url"] == "https://unpaywall.example.com/p.pdf"


def test_unpaywall_step_custom_fetch_from_context(monkeypatch) -> None:
    """context provides fetch_unpaywall → used instead of default."""
    def custom_fetch(doi, email):
        return "https://custom.example.com/p.pdf"

    record = {"doi": "10.1/foo"}
    result = unpaywall_step(
        record,
        {"unpaywall_email": "test@example.com", "fetch_unpaywall": custom_fetch},
    )
    assert result["pdf_url"] == "https://custom.example.com/p.pdf"


# ---------------------------------------------------------------------------
# arxiv_step: edge cases
# ---------------------------------------------------------------------------


def test_arxiv_step_no_arxiv_id() -> None:
    """No arxiv_id → return unchanged."""
    record = {"title": "Paper"}
    result = arxiv_step(record, {})
    assert result == record


def test_arxiv_step_empty_arxiv_id() -> None:
    """Empty arxiv_id → return unchanged."""
    record = {"arxiv_id": ""}
    result = arxiv_step(record, {})
    assert result == record


def test_arxiv_step_whitespace_arxiv_id() -> None:
    """Whitespace-only arxiv_id → return unchanged."""
    record = {"arxiv_id": "   "}
    result = arxiv_step(record, {})
    assert result == record


def test_arxiv_step_strips_arxiv_prefix_lowercase() -> None:
    """arxiv: prefix (lowercase) stripped."""
    record = {"arxiv_id": "arxiv:2401.12345"}
    result = arxiv_step(record, {})
    assert result["pdf_url"] == "https://arxiv.org/pdf/2401.12345"


def test_arxiv_step_strips_whitespace_around_prefix() -> None:
    """Prefix with extra whitespace handled."""
    record = {"arxiv_id": "  arXiv:2401.12345  "}
    result = arxiv_step(record, {})
    assert result["pdf_url"] == "https://arxiv.org/pdf/2401.12345"


def test_arxiv_step_bare_is_empty_after_prefix_strip() -> None:
    """After removing prefix, bare is empty → return unchanged."""
    record = {"arxiv_id": "arXiv:"}
    result = arxiv_step(record, {})
    assert result == record


def test_arxiv_step_non_string_arxiv_id() -> None:
    """arxiv_id is not a string → return unchanged."""
    record = {"arxiv_id": 12345}
    result = arxiv_step(record, {})
    assert result == record


def test_arxiv_step_old_style_id() -> None:
    """Old-style arxiv ID like hep-th/9901001."""
    record = {"arxiv_id": "hep-th/9901001"}
    result = arxiv_step(record, {})
    assert result["pdf_url"] == "https://arxiv.org/pdf/hep-th/9901001"
