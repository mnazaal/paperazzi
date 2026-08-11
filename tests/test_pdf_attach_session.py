from pzi.pdf_attach_session import (
    AttachSession,
    build_attach_session,
    validate_attach_request,
)


def test_build_attach_session_is_bound_to_citekey_bib_and_expiry() -> None:
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib="main",
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=25_000_000,
        allowed_source_urls=["https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=1"],
    )

    assert session == AttachSession(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib="main",
        # `created_at` is a constructor argument of `build_attach_session` (it
        # derives `expires_at`), not a field of the session — nothing ever read
        # it back.
        expires_at=700.0,
        max_bytes=25_000_000,
        allowed_source_urls=("https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=1",),
    )


def test_validate_attach_request_accepts_valid_pdf() -> None:
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=20,
        allowed_source_urls=["https://example.com/a.pdf"],
    )

    assert validate_attach_request(
        session,
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url="https://example.com/a.pdf",
        now=200.0,
    ) is None


def test_validate_attach_request_accepts_same_origin_observed_pdf_url() -> None:
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=20,
        allowed_source_urls=["https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=9840963"],
    )

    assert validate_attach_request(
        session,
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url="https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber=9840963",
        now=200.0,
    ) is None


def test_a_pdf_found_at_a_cdn_origin_can_be_attached() -> None:
    """Discovery reaches URLs the plan never named, and usually on another host.

    The publisher's article page is the planned candidate; the PDF it navigates
    to lives on a CDN. `allowed_source_urls` is pinned to the plan, so the
    attach was refused and the capture kept its metadata and silently lost the
    PDF — the case the navigate-monitor and discover-from-page paths exist for.

    Authorised on the planned candidate the fetch started from; the observed
    URL is recorded as provenance rather than as the credential.
    """
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=64,
        allowed_source_urls=["https://ieeexplore.ieee.org/document/9840963"],
    )

    assert validate_attach_request(
        session,
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url="https://cdn.ieee.example/pdfs/9840963.pdf",
        origin_candidate="https://ieeexplore.ieee.org/document/9840963",
        now=200.0,
    ) is None


def test_an_unplanned_origin_candidate_is_still_refused() -> None:
    """The claim has to name something the plan actually contained.

    Otherwise this stops being a check and becomes a field the caller fills in
    to be allowed. It is defence in depth either way — a caller holding the
    attach token knows the planned candidates — but "some planned URL" is the
    line it draws, and it has to draw it.
    """
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=64,
        allowed_source_urls=["https://ieeexplore.ieee.org/document/9840963"],
    )

    assert validate_attach_request(
        session,
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url="https://cdn.evil.example/anything.pdf",
        origin_candidate="https://elsewhere.example/not-in-the-plan",
        now=200.0,
    ) == "source URL not allowed for attach session"


def test_a_non_ascii_attach_token_is_refused_not_crashed_on() -> None:
    """`hmac.compare_digest` raises `TypeError` on a str containing non-ASCII.

    So a token with one accented character was a 500 rather than a 403 — and
    the route had already `claim()`ed the session, so the exception skipped
    the `restore()` and destroyed a session the caller could otherwise retry.
    `http_security.tokens_match` was hardened for exactly this and encodes to
    UTF-8 first; this is the second caller, which never used it.
    """
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=64,
        allowed_source_urls=[],
    )

    assert validate_attach_request(
        session,
        request_id="req-1",
        token="tök-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url=None,
        now=200.0,
    ) == "invalid attach token"


def test_validate_attach_request_rejects_wrong_token() -> None:
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=20,
        allowed_source_urls=[],
    )

    assert validate_attach_request(
        session,
        request_id="req-1",
        token="wrong",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url=None,
        now=200.0,
    ) == "invalid attach token"


def test_validate_attach_request_rejects_an_expired_session() -> None:
    expired = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=10,
        max_bytes=20,
        allowed_source_urls=[],
    )
    assert validate_attach_request(
        expired,
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url=None,
        now=200.0,
    ) == "attach session expired"


def test_validate_attach_request_rejects_identity_size_type_and_source_mismatch() -> None:
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib="main",
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=8,
        allowed_source_urls=["https://example.com/a.pdf"],
    )

    common = {
        "session": session,
        "request_id": "req-1",
        "token": "tok-1",
        "citekey": "smith2024",
        "bib": "main",
        "pdf_bytes": b"%PDF-1.7 test",
        "source_url": "https://example.com/a.pdf",
        "now": 200.0,
    }
    assert validate_attach_request(**{**common, "citekey": "other"}) == "attach citekey mismatch"
    assert validate_attach_request(**{**common, "bib": "other"}) == "attach bib mismatch"
    assert validate_attach_request(**common) == "PDF payload too large"
    assert validate_attach_request(**{**common, "pdf_bytes": b"html"}) == "PDF payload must start with %PDF-"
    assert validate_attach_request(
        **{**common, "pdf_bytes": b"%PDF-1", "source_url": "https://evil.example/a.pdf"}
    ) == "source URL not allowed for attach session"
