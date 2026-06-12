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
        created_at=100.0,
        expires_at=700.0,
        max_bytes=25_000_000,
        allowed_source_urls=("https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=1",),
        used=False,
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


def test_validate_attach_request_rejects_expired_or_used_session() -> None:
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
    used = AttachSession(**{**expired.__dict__, "expires_at": 700.0, "used": True})

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
    assert validate_attach_request(
        used,
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        pdf_bytes=b"%PDF-1.7 test",
        source_url=None,
        now=200.0,
    ) == "attach session already used"


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
