import pytest

from pzi.identifiers import classify_input, normalize_doi, normalize_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.1145/3368089.3409741", "10.1145/3368089.3409741"),
        ("https://doi.org/10.1145/3368089.3409741", "10.1145/3368089.3409741"),
        ("not-a-doi", None),
        # Regression: doi.org forwards query strings to the resolved target
        # rather than treating them as part of the DOI, so tracking params
        # and fragments pasted along with the link must not be captured.
        (
            "https://doi.org/10.1145/3368089.3409741?utm_source=twitter",
            "10.1145/3368089.3409741",
        ),
        (
            "https://doi.org/10.1145/3368089.3409741#section",
            "10.1145/3368089.3409741",
        ),
        ("10.1145/3368089.3409741?ref=x", "10.1145/3368089.3409741"),
        # Regression: bare "doi:" prefix (case-insensitive, optional space).
        ("doi:10.1145/3368089.3409741", "10.1145/3368089.3409741"),
        ("DOI: 10.1145/3368089.3409741", "10.1145/3368089.3409741"),
    ],
    ids=[
        "plain_doi",
        "doi_url",
        "rejects_non_doi",
        "doi_url_strips_query",
        "doi_url_strips_fragment",
        "plain_doi_strips_query",
        "doi_prefix",
        "doi_prefix_uppercase_with_space",
    ],
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
        (
            "https://doi.org/10.1145/1327452.1327492?utm_source=twitter",
            "doi",
            "10.1145/1327452.1327492",
        ),
        ("doi:10.1145/1327452.1327492", "doi", "10.1145/1327452.1327492"),
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
        "doi_org_url_strips_query",
        "doi_prefix",
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


def test_normalize_doi_strips_a_trailing_slash() -> None:
    # A trailing slash is not part of the DOI, so the two forms are one paper.
    assert normalize_doi("10.1145/abc/") == normalize_doi("10.1145/abc")
    assert normalize_doi("https://doi.org/10.1145/abc/") == "10.1145/abc"


def test_normalize_doi_is_case_insensitive_across_forms() -> None:
    # DOIs are case-insensitive by spec.
    assert normalize_doi("10.1145/ABC") == normalize_doi("10.1145/abc")
    assert normalize_doi("https://DOI.ORG/10.1145/AbC") == "10.1145/abc"


def test_classify_input_recognizes_old_style_arxiv_ids_with_a_subject_class() -> None:
    """`math.GT/0309136` — the dot in the subject class was excluded.

    `[a-z\\-]+/\\d{7}` matched `cs/0112017` and `hep-th/9901001` but not a dotted
    class, so those URLs fell through to being classified as a plain `url` and
    lost their arXiv DOI mapping and path canonicalization.
    """
    result = classify_input("https://arxiv.org/abs/math.GT/0309136")

    # Same convention as the other arXiv URLs: canonicalized to the arXiv DOI.
    assert result["kind"] == "doi"
    assert result["normalized"] == "10.48550/arxiv.math.gt/0309136"


def test_classify_input_still_treats_a_non_id_arxiv_path_as_a_url() -> None:
    """The widened pattern must not start swallowing listing pages."""
    assert classify_input("https://arxiv.org/list/cs.LG/recent")["kind"] == "url"


def test_classify_input_extracts_dois_from_publisher_display_paths() -> None:
    """ACM's /doi/abs/, Wiley's /doi/full/ and /doi/epdf/ yielded nothing.

    The pattern required `10.` immediately after `/doi/`, so every publisher URL
    with a display segment silently produced no DOI.
    """
    for url in (
        "https://dl.acm.org/doi/abs/10.1145/3372297",
        "https://onlinelibrary.wiley.com/doi/full/10.1002/spe.1234",
        "https://onlinelibrary.wiley.com/doi/epdf/10.1002/spe.1234",
        "https://dl.acm.org/doi/pdf/10.1145/3372297",
    ):
        result = classify_input(url)
        assert result["kind"] == "doi", url
        assert result["normalized"].startswith("10."), url


def test_every_doi_a_publisher_path_yields_is_one_normalize_doi_accepts() -> None:
    """Why `classify_input`'s `embedded_doi is not None` guard cannot fail.

    `DOI_IN_PATH_PATTERN` captures `10.\\d{4,9}/[^\\s?#]+`, which is exactly the
    group `DOI_PATTERN` matches, so `normalize_doi` cannot reject what the path
    pattern found — the guard is there for the type, not for a case that occurs.
    That coupling is what the `# pragma: no branch` on it now claims, and this
    test is what keeps the claim true if either pattern is tightened.
    """
    from pzi.identifiers import DOI_IN_PATH_PATTERN

    for path in (
        "/doi/10.1145/3372297",
        "/doi/abs/10.1145/3372297.3372299",
        "/doi/epdf/10.1002/spe.1234",
        "/doi/10.1145/abc..",       # trailing punctuation
        "/doi/10.1145/x)]",         # bracket cruft a paste picks up
        "/doi/10.1145/UPPER/Case",  # case is folded, not rejected
    ):
        match = DOI_IN_PATH_PATTERN.search(path)
        assert match is not None, path
        assert normalize_doi(match.group(1)) is not None, path


def test_preprint_doi_prefixes_has_one_source() -> None:
    """Guards against C5b's duplicate reappearing.

    `resolution_match._doi_mismatch` used to carry its own copy of the
    preprint DOI prefix list, separate from `identifiers.PREPRINT_DOI_PREFIX`
    (arXiv only). Adding a preprint server to one and not the other is this
    project's dominant defect shape, so `resolution_match` must hold the exact
    same tuple object `identifiers.PREPRINT_DOI_PREFIXES` defines — not an
    equal-valued copy that can drift the next time either is edited.
    """
    import pzi.resolution_match as resolution_match
    from pzi.identifiers import PREPRINT_DOI_PREFIX, PREPRINT_DOI_PREFIXES

    assert resolution_match.PREPRINT_DOI_PREFIXES is PREPRINT_DOI_PREFIXES
    # The arXiv entry in the general table must not drift from the dedicated
    # arXiv constant `is_preprint_doi` uses.
    assert PREPRINT_DOI_PREFIXES[0] == PREPRINT_DOI_PREFIX.rstrip("/")
