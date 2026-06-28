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


def test_normalize_url_preserves_ipv6_brackets() -> None:
    from pzi.identifiers import normalize_url

    assert (
        normalize_url("http://[2606:2800:220:1:248:1893:25c8:1946]/paper.pdf")
        == "http://[2606:2800:220:1:248:1893:25c8:1946]/paper.pdf"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com/x.pdf",      # unsupported scheme
        "https:///just-a-path",         # no netloc
        "http://example.com:notaport/", # port is non-numeric → ValueError
        "not a url at all",             # no scheme/netloc
    ],
)
def test_normalize_url_rejects_unsupported(raw) -> None:
    assert normalize_url(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        # arXiv abstract/PDF URLs canonicalize to the arXiv DOI.
        ("https://arxiv.org/abs/2401.12345", "10.48550/arxiv.2401.12345"),
        ("https://arxiv.org/abs/2401.12345v2", "10.48550/arxiv.2401.12345"),
        ("https://arxiv.org/pdf/2401.12345", "10.48550/arxiv.2401.12345"),
        ("https://arxiv.org/abs/cs/0112017", "10.48550/arxiv.cs/0112017"),
        # bioRxiv/medRxiv content paths yield the embedded DOI (version stripped).
        ("https://www.biorxiv.org/content/10.1101/2020.01.01.123456v1",
         "10.1101/2020.01.01.123456"),
        ("https://www.medrxiv.org/content/10.1101/2021.05.05.654321v2.full",
         "10.1101/2021.05.05.654321"),
        # Zenodo records map to the Zenodo DOI prefix.
        ("https://zenodo.org/records/1234567", "10.5281/zenodo.1234567"),
        ("https://zenodo.org/record/7654321", "10.5281/zenodo.7654321"),
    ],
)
def test_classify_input_extracts_repository_dois(raw, expected) -> None:
    result = classify_input(raw)
    assert result["kind"] == "doi"
    assert result["normalized"] == expected


def test_classify_input_arxiv_non_id_path_is_plain_url() -> None:
    # An arXiv URL that is not an abs/pdf identifier stays a plain url.
    result = classify_input("https://arxiv.org/list/cs.LG/recent")
    assert result["kind"] == "url"
    assert normalize_url("http://[::1]:8080/x") == "http://[::1]:8080/x"
