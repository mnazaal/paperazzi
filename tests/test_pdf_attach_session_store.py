from pzi.pdf_attach_session import build_attach_session
from pzi.pdf_attach_session_store import AttachSessionStore


def test_attach_session_store_saves_gets_and_consumes_session() -> None:
    store = AttachSessionStore(clock=lambda: 200.0)
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=10,
        allowed_source_urls=[],
    )

    store.put(session)

    assert store.get("req-1") == session
    consumed = store.consume("req-1")
    assert consumed is not None
    assert consumed.used is True
    assert store.get("req-1") is None


def test_attach_session_store_prunes_expired_sessions_on_put_and_get() -> None:
    store = AttachSessionStore(clock=lambda: 1000.0)
    expired = build_attach_session(
        request_id="old",
        token="tok-old",
        citekey="old2024",
        bib=None,
        created_at=0.0,
        ttl_seconds=10,
        max_bytes=10,
        allowed_source_urls=[],
    )
    fresh = build_attach_session(
        request_id="new",
        token="tok-new",
        citekey="new2024",
        bib=None,
        created_at=999.0,
        ttl_seconds=10,
        max_bytes=10,
        allowed_source_urls=[],
    )

    store.put(expired)
    store.put(fresh)

    assert store.get("old") is None
    assert store.get("new") == fresh


def test_attach_session_store_claim_removes_session_immediately() -> None:
    # claim() must pop atomically (not just peek like get()), so a second
    # concurrent claimant for the same request_id sees it gone rather than
    # being able to also pass validation on the still-present session.
    store = AttachSessionStore(clock=lambda: 200.0)
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=10,
        allowed_source_urls=[],
    )
    store.put(session)

    claimed = store.claim("req-1")
    assert claimed == session
    assert store.get("req-1") is None
    assert store.claim("req-1") is None


def test_attach_session_store_restore_allows_retry_after_failed_attempt() -> None:
    # A claim that fails validation (e.g. wrong token) must be able to put the
    # session back, so a legitimate retry with the correct token still works.
    store = AttachSessionStore(clock=lambda: 200.0)
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=600,
        max_bytes=10,
        allowed_source_urls=[],
    )
    store.put(session)

    claimed = store.claim("req-1")
    assert claimed is not None
    store.restore(claimed)

    assert store.get("req-1") == session


def test_attach_session_store_restore_drops_expired_session() -> None:
    store = AttachSessionStore(clock=lambda: 1000.0)
    session = build_attach_session(
        request_id="req-1",
        token="tok-1",
        citekey="smith2024",
        bib=None,
        created_at=100.0,
        ttl_seconds=10,
        max_bytes=10,
        allowed_source_urls=[],
    )

    store.restore(session)

    assert store.get("req-1") is None
