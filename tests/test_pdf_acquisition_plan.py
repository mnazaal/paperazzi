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


def test_classify_acm_pdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://dl.acm.org/doi/pdf/10.1145/3590000.3590001?download=true",
            page_url="https://dl.acm.org/doi/10.1145/3590000.3590001",
        ),
        url="https://dl.acm.org/doi/pdf/10.1145/3590000.3590001?download=true",
        referrer="https://dl.acm.org/doi/10.1145/3590000.3590001",
    )


def test_classify_sciencedirect_pdfft_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://www.sciencedirect.com/science/article/pii/S0167811623000123/pdfft?download=true",
            page_url="https://www.sciencedirect.com/science/article/pii/S0167811623000123",
        ),
        url="https://www.sciencedirect.com/science/article/pii/S0167811623000123/pdfft?download=true",
        referrer="https://www.sciencedirect.com/science/article/pii/S0167811623000123",
    )


def test_classify_wiley_epdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://onlinelibrary.wiley.com/doi/epdf/10.1002/adma.202300123",
            page_url="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
        ),
        url="https://onlinelibrary.wiley.com/doi/epdf/10.1002/adma.202300123",
        referrer="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
    )


def test_classify_wiley_pdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://onlinelibrary.wiley.com/doi/pdf/10.1002/adma.202300123",
            page_url="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
        ),
        url="https://onlinelibrary.wiley.com/doi/pdf/10.1002/adma.202300123",
        referrer="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
    )


def test_classify_tandfonline_pdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://www.tandfonline.com/doi/pdf/10.1080/01621459.2023.1234567",
            page_url="https://www.tandfonline.com/doi/full/10.1080/01621459.2023.1234567",
        ),
        url="https://www.tandfonline.com/doi/pdf/10.1080/01621459.2023.1234567",
        referrer="https://www.tandfonline.com/doi/full/10.1080/01621459.2023.1234567",
    )


def test_classify_sagepub_pdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://journals.sagepub.com/doi/pdf/10.1177/09567976231234567",
            page_url="https://journals.sagepub.com/doi/10.1177/09567976231234567",
        ),
        url="https://journals.sagepub.com/doi/pdf/10.1177/09567976231234567",
        referrer="https://journals.sagepub.com/doi/10.1177/09567976231234567",
    )


def test_classify_oxford_article_pdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://academic.oup.com/bioinformatics/article-pdf/39/1/btac700/12345678/btac700.pdf",
            page_url="https://academic.oup.com/bioinformatics/article/39/1/btac700",
        ),
        url="https://academic.oup.com/bioinformatics/article-pdf/39/1/btac700/12345678/btac700.pdf",
        referrer="https://academic.oup.com/bioinformatics/article/39/1/btac700",
    )


def test_classify_generic_doi_pdf_gateway() -> None:
    """Unknown host with /doi/pdf/ path → still classified as pdf_gateway."""
    _gateway_assert(
        classify_pdf_candidate(
            "https://some-publisher.example/doi/pdf/10.1234/foo.bar",
            page_url="https://some-publisher.example/article/10.1234/foo.bar",
        ),
        url="https://some-publisher.example/doi/pdf/10.1234/foo.bar",
        referrer="https://some-publisher.example/article/10.1234/foo.bar",
    )


def test_classify_generic_epdf_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://some-publisher.example/doi/epdf/10.1234/foo.bar",
            page_url="https://some-publisher.example/article/10.1234/foo.bar",
        ),
        url="https://some-publisher.example/doi/epdf/10.1234/foo.bar",
        referrer="https://some-publisher.example/article/10.1234/foo.bar",
    )


def test_classify_generic_pdfft_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://example.com/article/pii/S1234/pdfft",
            page_url="https://example.com/article/pii/S1234",
        ),
        url="https://example.com/article/pii/S1234/pdfft",
        referrer="https://example.com/article/pii/S1234",
    )


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


def test_classify_wiley_pdfdirect_gateway() -> None:
    _gateway_assert(
        classify_pdf_candidate(
            "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/adma.202300123",
            page_url="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
        ),
        url="https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/adma.202300123",
        referrer="https://onlinelibrary.wiley.com/doi/10.1002/adma.202300123",
    )


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
