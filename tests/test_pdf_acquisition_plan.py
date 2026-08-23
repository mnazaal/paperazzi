import pytest

from pzi.pdf_acquisition_plan import build_pdf_acquisition_plan, classify_pdf_candidate


def test_classify_ieee_article_page_as_article_page() -> None:
    candidate = classify_pdf_candidate(
        "https://ieeexplore.ieee.org/document/9840963",
        page_url="https://ieeexplore.ieee.org/document/9840963",
    )

    assert candidate == {
        "url": "https://ieeexplore.ieee.org/document/9840963",
        "kind": "article_page",
        "method": "discover_from_page",
        "referrer": "https://ieeexplore.ieee.org/document/9840963",
        "requires_navigation": False,
        "timeout_ms": 10000,
    }


def test_classify_ieee_stamp_as_pdf_gateway() -> None:
    candidate = classify_pdf_candidate(
        "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
        page_url="https://ieeexplore.ieee.org/document/9840963",
    )

    assert candidate["kind"] == "pdf_gateway"
    assert candidate["method"] == "navigate_monitor"
    assert candidate["requires_navigation"] is True
    assert candidate["referrer"] == "https://ieeexplore.ieee.org/document/9840963"


def test_build_plan_prefers_ieee_gateway_over_article_page() -> None:
    plan = build_pdf_acquisition_plan(
        citekey="poborchaya2022analysis",
        bib="main",
        page_url="https://ieeexplore.ieee.org/document/9840963",
        pdf_urls=[
            "https://ieeexplore.ieee.org/document/9840963",
            "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
        ],
        attach_base_url="http://127.0.0.1:8765/attach-pdf-raw",
        request_id="req-1",
        attach_token="tok-1",
    )

    assert plan["request_id"] == "req-1"
    assert plan["citekey"] == "poborchaya2022analysis"
    assert plan["bib"] == "main"
    assert plan["attach"] == {
        "url": "http://127.0.0.1:8765/attach-pdf-raw?request_id=req-1&citekey=poborchaya2022analysis&bib=main",
        "token": "tok-1",
    }
    assert [candidate["kind"] for candidate in plan["candidates"]] == [
        "pdf_gateway",
        "article_page",
    ]


def test_build_plan_returns_none_without_candidates() -> None:
    assert build_pdf_acquisition_plan(
        citekey="smith2024",
        bib=None,
        page_url="https://example.com/article",
        pdf_urls=[],
        attach_base_url="http://127.0.0.1:8765/attach-pdf-raw",
        request_id="req-1",
        attach_token="tok-1",
    ) is None


# ── Publisher gateway classifiers ────────────────────────────────────────


def _gateway_assert(candidate: dict, *, url: str, referrer: str) -> None:
    assert candidate["url"] == url
    assert candidate["kind"] == "pdf_gateway"
    assert candidate["method"] == "navigate_monitor"
    assert candidate["requires_navigation"] is True
    assert candidate["referrer"] == referrer


@pytest.mark.parametrize(
    ("url", "page_url", "timeout_ms"),
    [
        pytest.param(
            "https://dl.acm.org/doi/pdf/10.1145/3590000.3590001?download=true",
            "https://dl.acm.org/doi/10.1145/3590000.3590001",
            20000,
            id="acm",
        ),
        pytest.param(
            "https://www.sciencedirect.com/science/article/pii/S0167811623000123/pdfft?download=true",
            "https://www.sciencedirect.com/science/article/pii/S0167811623000123",
            15000,
            id="sciencedirect-pdfft",
        ),
        pytest.param(
            "https://onlinelibrary.wiley.com/doi/epdf/10.1002/adma.202300123",
            "https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
            20000,
            id="wiley-epdf",
        ),
        pytest.param(
            "https://onlinelibrary.wiley.com/doi/pdf/10.1002/adma.202300123",
            "https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
            20000,
            id="wiley-pdf",
        ),
        pytest.param(
            "https://www.tandfonline.com/doi/pdf/10.1080/01621459.2023.1234567",
            "https://www.tandfonline.com/doi/full/10.1080/01621459.2023.1234567",
            15000,
            id="tandfonline",
        ),
        pytest.param(
            "https://journals.sagepub.com/doi/pdf/10.1177/09567976231234567",
            "https://journals.sagepub.com/doi/10.1177/09567976231234567",
            15000,
            id="sagepub",
        ),
        pytest.param(
            "https://academic.oup.com/bioinformatics/article-pdf/39/1/btac700/12345678/btac700.pdf",
            "https://academic.oup.com/bioinformatics/article/39/1/btac700",
            15000,
            id="oxford-article-pdf",
        ),
        pytest.param(
            "https://some-publisher.example/doi/pdf/10.1234/foo.bar",
            "https://some-publisher.example/article/10.1234/foo.bar",
            15000,
            id="generic-doi-pdf",
        ),
        pytest.param(
            "https://some-publisher.example/doi/epdf/10.1234/foo.bar",
            "https://some-publisher.example/article/10.1234/foo.bar",
            15000,
            id="generic-epdf",
        ),
        pytest.param(
            "https://example.com/article/pii/S1234/pdfft",
            "https://example.com/article/pii/S1234",
            15000,
            id="generic-pdfft",
        ),
        pytest.param(
            "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/adma.202300123",
            "https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
            20000,
            id="wiley-pdfdirect",
        ),
    ],
)
def test_classify_publisher_gateway(url: str, page_url: str, timeout_ms: int) -> None:
    """Every known gateway URL classifies as a navigate-and-monitor candidate.

    The generic rows matter as much as the publisher-specific ones: they are
    what a host absent from the table falls back to, and three publisher rows
    were deleted because the generic row already gave them the same timeout.
    """
    candidate = classify_pdf_candidate(url, page_url=page_url)
    _gateway_assert(candidate, url=url, referrer=page_url)
    # Pinned per host, so deleting a publisher row is only safe while the
    # generic row it falls back to names the same timeout.
    assert candidate["timeout_ms"] == timeout_ms


def test_classify_lookalike_host_is_not_treated_as_publisher_gateway() -> None:
    # Regression: the gateway table used to match hostnames with `re.search`
    # and no leading anchor/domain-boundary check, so an unrelated lookalike
    # host like "evilacademic.oup.com" (not a subdomain of academic.oup.com —
    # no "." boundary) would falsely match the Oxford gateway pattern just by
    # sharing a trailing substring. Uses Oxford's `/article-pdf/` path, which
    # (unlike ACM/ScienceDirect/Wiley's paths) isn't also in the generic
    # any-host catch-all list, so a false host match is the only way this
    # could still classify as a gateway.
    candidate = classify_pdf_candidate(
        "https://evilacademic.oup.com/bioinformatics/article-pdf/39/1/btac700",
        page_url="https://evilacademic.oup.com/bioinformatics/39/1/btac700",
    )

    assert candidate["kind"] == "article_page"


# ── Non-gateway URLs still fall through correctly ─────────────────────────


def test_classify_regular_article_page_unaffected() -> None:
    candidate = classify_pdf_candidate(
        "https://example.com/article/12345",
        page_url="https://example.com/article/12345",
    )
    assert candidate["kind"] == "article_page"
    assert candidate["method"] == "discover_from_page"


def test_classify_direct_pdf_still_works() -> None:
    candidate = classify_pdf_candidate(
        "https://example.com/paper.pdf",
        page_url="https://example.com/article",
    )
    assert candidate["kind"] == "direct_pdf"
    assert candidate["method"] == "direct_fetch"


# ── Plan: gateways sort before article_page ──────────────────────────────


def test_build_plan_sorts_publisher_gateways_first() -> None:
    plan = build_pdf_acquisition_plan(
        citekey="smith2024",
        bib="main",
        page_url="https://dl.acm.org/doi/10.1145/3590000.3590001",
        pdf_urls=[
            "https://dl.acm.org/doi/10.1145/3590000.3590001",
            "https://dl.acm.org/doi/pdf/10.1145/3590000.3590001?download=true",
        ],
        attach_base_url="http://127.0.0.1:8765/attach-pdf-raw",
        request_id="req-acm",
        attach_token="tok-acm",
    )
    assert plan is not None
    kinds = [c["kind"] for c in plan["candidates"]]
    assert kinds == ["pdf_gateway", "article_page"]


# ── Per-publisher timeout values ──────────────────────────────────────────


def test_acm_gateway_has_20s_timeout() -> None:
    c = classify_pdf_candidate(
        "https://dl.acm.org/doi/pdf/10.1145/3590000.3590001",
        page_url="https://dl.acm.org/doi/10.1145/3590000.3590001",
    )
    assert c["timeout_ms"] == 20000


def test_wiley_gateway_has_20s_timeout() -> None:
    c = classify_pdf_candidate(
        "https://onlinelibrary.wiley.com/doi/pdf/10.1002/adma.202300123",
        page_url="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
    )
    assert c["timeout_ms"] == 20000


def test_generic_gateway_has_15s_timeout() -> None:
    c = classify_pdf_candidate(
        "https://some-publisher.example/doi/pdf/10.1234/foo.bar",
        page_url="https://some-publisher.example/article/10.1234/foo.bar",
    )
    assert c["timeout_ms"] == 15000


# ── Edge cases ──────────────────────────────────────────────────────────


def test_malformed_url_skips_gateway_detection() -> None:
    """A URL that raises ValueError during urlsplit should fall through to
    article_page, not crash the classifier."""
    # A URL with invalid brackets triggers ValueError in urlsplit
    c = classify_pdf_candidate(
        "http://[::1]:bad]/path",
        page_url="https://example.com",
    )
    assert c["kind"] == "article_page"
    assert c["method"] == "discover_from_page"
