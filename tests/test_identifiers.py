import pytest

from pzi.identifiers import classify_input, normalize_doi, normalize_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.1145/3368089.3409741", "10.1145/3368089.3409741"),
        ("https://doi.org/10.1145/3368089.3409741", "10.1145/3368089.3409741"),
        ("not-a-doi", None),
    ],
    ids=["plain_doi", "doi_url", "rejects_non_doi"],
)
def test_normalize_doi(raw, expected) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://Example.com/paper?utm_source=x&id=42#section",
            "https://example.com/paper?id=42",
        ),
        ("https://example.com:443/paper", "https://example.com/paper"),
        (
            "https://doi.org/10.1145/3368089.3409741",
            "https://doi.org/10.1145/3368089.3409741",
        ),
        (
            "https://arxiv.org/pdf/2401.12345",
            "https://arxiv.org/pdf/2401.12345.pdf",
        ),
    ],
    ids=[
        "strips_fragment_tracking",
        "drops_default_port",
        "canonicalizes_doi",
        "canonicalizes_arxiv_pdf",
    ],
)
def test_normalize_url(raw, expected) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw,expected_kind,expected_normalized",
    [
        ("10.1145/3368089.3409741", "doi", "10.1145/3368089.3409741"),
        ("https://example.com/paper", "url", "https://example.com/paper"),
        ("https://example.com/paper.pdf", "pdf_url", "https://example.com/paper.pdf"),
        ("paper title only", "unknown", None),
        ("https://dl.acm.org/doi/10.5555/3327546.3327713", "doi", "10.5555/3327546.3327713"),
        ("https://doi.org/10.1145/1327452.1327492", "doi", "10.1145/1327452.1327492"),
        ("/path/to/paper.pdf", "local_pdf", "/path/to/paper.pdf"),
        ("C:\\Users\\foo\\paper.PDF", "local_pdf", "C:\\Users\\foo\\paper.PDF"),
    ],
    ids=[
        "detects_doi",
        "detects_url",
        "detects_pdf_url",
        "rejects_unknown",
        "extracts_doi_from_acm",
        "extracts_doi_from_doi_org",
        "detects_local_pdf",
        "detects_local_pdf_windows",
    ],
)
def test_classify_input(raw, expected_kind, expected_normalized) -> None:
    result = classify_input(raw)
    assert result["kind"] == expected_kind
    assert result["normalized"] == expected_normalized
    assert result["raw"] == raw
