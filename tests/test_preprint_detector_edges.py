"""Edge tests for preprint_detector.py uncovered lines (27, 29, 67-68)."""

from pzi.promote_service import detect_preprint_source, is_preprint

# ── is_preprint ──────────────────────────────────────────────────

def test_is_preprint_no_venue() -> None:
    """No venue → preprint (line 27)."""
    assert is_preprint({"title": "Some Paper"}) is True
    assert is_preprint({}) is True


def test_is_preprint_whitespace_venue() -> None:
    """Whitespace-only venue → preprint (line 27)."""
    assert is_preprint({"venue": "  "}) is True
    assert is_preprint({"venue": ""}) is True


def test_is_preprint_venue_not_string() -> None:
    """Non-string venue → preprint (line 27)."""
    assert is_preprint({"venue": 42}) is True


def test_is_preprint_has_arxiv_id() -> None:
    """Record with arxiv_id is preprint regardless of venue (line 29)."""
    assert is_preprint({"venue": "Nature", "arxiv_id": "2401.12345"}) is True


def test_is_preprint_source_url_arxiv(tmp_path) -> None:
    """source_url on arxiv.org → preprint."""
    assert is_preprint({"venue": "Nature", "source_url": "https://arxiv.org/abs/2401.12345"}) is True


def test_is_preprint_canonical_url_biorxiv(tmp_path) -> None:
    """canonical_url on biorxiv.org → preprint."""
    assert is_preprint({"venue": "Cell", "canonical_url": "https://biorxiv.org/content/123"}) is True


def test_is_preprint_not_preprint() -> None:
    """Published paper with venue, no preprint indicators."""
    assert is_preprint({"venue": "Nature", "doi": "10.1/test"}) is False


# ── detect_preprint_source ───────────────────────────────────────

def test_detect_preprint_source_arxiv_id() -> None:
    """arxiv_id present → 'arXiv'."""
    assert detect_preprint_source({"arxiv_id": "2401.12345"}) == "arXiv"


def test_detect_preprint_source_arxiv_url() -> None:
    assert detect_preprint_source({"source_url": "https://arxiv.org/abs/2401.12345"}) == "arXiv"


def test_detect_preprint_source_biorxiv_url() -> None:
    assert detect_preprint_source({"source_url": "https://biorxiv.org/content/123"}) == "bioRxiv"


def test_detect_preprint_source_medrxiv_url() -> None:
    assert detect_preprint_source({"canonical_url": "https://medrxiv.org/content/123"}) == "medRxiv"


def test_detect_preprint_source_not_found() -> None:
    """Not a preprint → None."""
    assert detect_preprint_source({"title": "Regular Paper", "venue": "Nature"}) is None


def test_detect_preprint_source_invalid_url() -> None:
    """Invalid URL does not crash and returns None."""
    record = {"source_url": "not_a_url!!!"}
    assert detect_preprint_source(record) is None


def test_detect_preprint_source_empty_arxiv_id() -> None:
    """Whitespace-only arxiv_id not treated as arXiv."""
    record = {"arxiv_id": "  "}
    assert detect_preprint_source(record) is None


def test_detect_preprint_source_zenodo() -> None:
    assert detect_preprint_source({"canonical_url": "https://zenodo.org/records/123"}) == "Zenodo"
