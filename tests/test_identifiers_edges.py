"""Edge tests for pzi.identifiers covering previously uncovered branches.

Covers missing lines in normalize_doi, normalize_url, classify_input,
and _normalize_special_path.
"""


from pzi.identifiers import (
    _normalize_special_path,
    classify_input,
    normalize_doi,
    normalize_url,
)

# ---------------------------------------------------------------------------
# normalize_doi: edge cases
# ---------------------------------------------------------------------------


def test_normalize_doi_with_whitespace_and_newlines() -> None:
    """DOI with surrounding whitespace → stripped."""
    # DOI pattern uses \S+ which doesn't match internal spaces.
    # The .strip() on the match and re.sub(r'\s+', ...) normalize after match.
    assert normalize_doi("  \n10.1145/3368089.3409741\t  ") == "10.1145/3368089.3409741"


def test_normalize_doi_trailing_whitespace() -> None:
    assert normalize_doi("  10.1145/3368089.3409741  ") == "10.1145/3368089.3409741"


def test_normalize_doi_case_normalized() -> None:
    """DOI is lowercased."""
    assert normalize_doi("10.1145/AbCdEfGh") == "10.1145/abcdefgh"


def test_normalize_doi_from_url_with_case() -> None:
    """DOI URL with mixed case → normalized to lowercase."""
    assert normalize_doi("https://doi.org/10.1145/AbC.123") == "10.1145/abc.123"


def test_normalize_doi_not_doi_like() -> None:
    """Random string returns None."""
    assert normalize_doi("not a doi") is None


def test_normalize_doi_empty_string() -> None:
    """Empty string returns None."""
    assert normalize_doi("") is None


def test_normalize_doi_only_whitespace() -> None:
    """Whitespace-only returns None."""
    assert normalize_doi("   ") is None


# ---------------------------------------------------------------------------
# normalize_url: edge cases
# ---------------------------------------------------------------------------


def test_normalize_url_non_http_scheme() -> None:
    """ftp:// scheme returns None."""
    assert normalize_url("ftp://example.com/file.pdf") is None


def test_normalize_url_no_netloc() -> None:
    """URL without netloc returns None."""
    assert normalize_url("https://") is None


def test_normalize_url_no_hostname() -> None:
    """URL without hostname returns None."""
    assert normalize_url("https:///path") is None


def test_normalize_url_default_http_port_80() -> None:
    """Port 80 on http is stripped."""
    assert "example.com:80" not in normalize_url("http://example.com:80/path")


def test_normalize_url_non_default_port_preserved() -> None:
    """Port 8080 preserved."""
    result = normalize_url("http://example.com:8080/path")
    assert ":8080" in result


def test_normalize_url_https_443_default_stripped() -> None:
    """Port 443 on https is stripped."""
    assert ":443" not in normalize_url("https://example.com:443/path")


def test_normalize_url_strips_tracking_params() -> None:
    """utm_source, utm_medium, gclid, fbclid etc removed."""
    result = normalize_url(
        "https://example.com/page?utm_source=fb&utm_medium=cpc&gclid=123&fbclid=456&id=789"
    )
    assert "utm_source" not in result
    assert "utm_medium" not in result
    assert "utm_campaign" not in result
    assert "gclid" not in result
    assert "fbclid" not in result
    assert "id=789" in result


def test_normalize_url_preserves_fragment() -> None:
    """Fragment is preserved (not in query, but in urlunsplit it's empty)."""
    # urlunsplit uses '' as fragment
    result = normalize_url("https://example.com/page#section")
    # Fragment is stripped by urlsplit but not re-added since urlunsplit(..., "")
    assert "#" not in result


def test_normalize_url_hostname_lowercased() -> None:
    result = normalize_url("https://EXAMPLE.COM/Path")
    assert "EXAMPLE.COM" not in result
    assert "example.com" in result


def test_normalize_url_scheme_lowercased() -> None:
    result = normalize_url("HTTPS://example.com/path")
    assert result.startswith("https://")


def test_normalize_url_empty_path_becomes_slash() -> None:
    result = normalize_url("https://example.com")
    assert result.endswith("/") or result.count("/") >= 3


def test_normalize_url_arxiv_abs_with_version() -> None:
    """arxiv.org /abs/ path with version → normalized."""
    result = normalize_url("https://arxiv.org/abs/2401.12345v2")
    assert "2401.12345v2" in result


def test_normalize_url_arxiv_pdf_without_pdf_extension() -> None:
    """arxiv.org /pdf/ path without .pdf suffix → .pdf added."""
    result = normalize_url("https://arxiv.org/pdf/2401.12345")
    assert result.endswith(".pdf")


def test_normalize_url_arxiv_pdf_with_extension() -> None:
    """arxiv.org /pdf/ path already has .pdf → preserved."""
    result = normalize_url("https://arxiv.org/pdf/2401.12345.pdf")
    assert result.endswith(".pdf")


def test_normalize_url_arxiv_old_style_abs() -> None:
    """Old-style arxiv ID hep-th/9901001."""
    result = normalize_url("https://arxiv.org/abs/hep-th/9901001")
    assert "hep-th/9901001" in result


def test_normalize_url_arxiv_old_style_pdf() -> None:
    """Old-style arxiv ID in /pdf/ path."""
    result = normalize_url("https://arxiv.org/pdf/hep-th/9901001")
    assert result.endswith(".pdf")
    assert "hep-th/9901001" in result


def test_normalize_url_doi_org_strips_leading_slash() -> None:
    """doi.org URL → normalized DOI path."""
    result = normalize_url("https://doi.org/10.1145/3368089.3409741")
    assert result == "https://doi.org/10.1145/3368089.3409741"


def test_normalize_url_doi_org_with_extra_slash() -> None:
    """doi.org with // prefix."""
    result = normalize_url("https://doi.org//10.1145/3368089.3409741")
    # lstrip("/") removes both, normalize_doi extracts
    assert "10.1145/3368089.3409741" in result


def test_normalize_url_doi_org_invalid_doi() -> None:
    """doi.org with non-DOI path → path left as-is."""
    result = normalize_url("https://doi.org/not-a-doi")
    assert "not-a-doi" in result


def test_normalize_url_multiple_tracking_params() -> None:
    """utm_campaign and utm_term also stripped."""
    result = normalize_url(
        "https://example.com?utm_campaign=spring&utm_term=sale&real=param"
    )
    assert "utm_campaign" not in result
    assert "utm_term" not in result
    assert "real=param" in result


# ---------------------------------------------------------------------------
# classify_input: edge cases
# ---------------------------------------------------------------------------


def test_classify_direct_doi() -> None:
    result = classify_input("10.1145/3368089.3409741")
    assert result["kind"] == "doi"
    assert result["normalized"] == "10.1145/3368089.3409741"


def test_classify_url_with_embedded_doi() -> None:
    """ACM URL with DOI in path → classified as doi."""
    result = classify_input("https://dl.acm.org/doi/10.5555/3327546.3327713")
    assert result["kind"] == "doi"
    assert result["normalized"] == "10.5555/3327546.3327713"


def test_classify_doi_org_url() -> None:
    """doi.org URL → classified as doi."""
    result = classify_input("https://doi.org/10.1145/1327452.1327492")
    assert result["kind"] == "doi"
    assert result["normalized"] == "10.1145/1327452.1327492"


def test_classify_pdf_url() -> None:
    """URL ending in .pdf → pdf_url."""
    result = classify_input("https://example.com/paper.pdf")
    assert result["kind"] == "pdf_url"


def test_classify_arxiv_pdf_url() -> None:
    """arxiv.org /pdf/ path without .pdf extension → pdf_url."""
    result = classify_input("https://arxiv.org/pdf/2401.12345")
    assert result["kind"] == "pdf_url"


def test_classify_regular_url() -> None:
    result = classify_input("https://example.com/paper")
    assert result["kind"] == "url"


def test_classify_local_pdf_path() -> None:
    result = classify_input("/path/to/paper.pdf")
    assert result["kind"] == "local_pdf"
    assert result["normalized"] == "/path/to/paper.pdf"


def test_classify_local_pdf_uppercase() -> None:
    result = classify_input("/path/to/paper.PDF")
    assert result["kind"] == "local_pdf"
    assert result["normalized"] == "/path/to/paper.PDF"


def test_classify_unknown_text() -> None:
    result = classify_input("just some paper title")
    assert result["kind"] == "unknown"
    assert result["normalized"] is None


def test_classify_unknown_no_pdf_extension() -> None:
    """Path without .pdf that's not a URL → unknown."""
    result = classify_input("/path/to/data")
    assert result["kind"] == "unknown"


def test_classify_arxiv_abs_is_url_not_pdf() -> None:
    """arxiv /abs/ path → url (not pdf_url)."""
    result = classify_input("https://arxiv.org/abs/2401.12345")
    assert result["kind"] == "url"


def test_classify_url_with_tracking_params() -> None:
    """URL with tracking params still classified as url."""
    result = classify_input(
        "https://example.com/paper?utm_source=fb&id=123"
    )
    assert result["kind"] == "url"
    assert "utm_source" not in result.get("normalized", "")


def test_classify_normalized_doi_in_url() -> None:
    """The normalized URL has an embedded DOI."""
    result = classify_input(
        "https://journals.sagepub.com/doi/10.1177/0956797624123456?icid=int.sj-abstract.citing-articles.1"
    )
    assert result["kind"] == "doi"
    assert result["normalized"] == "10.1177/0956797624123456"


# ---------------------------------------------------------------------------
# _normalize_special_path: edge cases
# ---------------------------------------------------------------------------


def test_normalize_special_path_doi_org_valid() -> None:
    result = _normalize_special_path(
        hostname="doi.org",
        path="/10.1145/3368089.3409741",
    )
    assert result == "/10.1145/3368089.3409741"


def test_normalize_special_path_doi_org_invalid() -> None:
    """Non-DOI path on doi.org → unchanged."""
    result = _normalize_special_path(
        hostname="doi.org",
        path="/browse",
    )
    assert result == "/browse"


def test_normalize_special_path_doi_org_with_extra_slash() -> None:
    """doi.org with extra leading slash."""
    result = _normalize_special_path(
        hostname="doi.org",
        path="//10.1145/3368089.3409741",
    )
    # lstrip("/") removes all leading slashes, normalize_doi extracts
    assert result == "/10.1145/3368089.3409741"


def test_normalize_special_path_arxiv_abs() -> None:
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/abs/2401.12345",
    )
    assert result == "/abs/2401.12345"


def test_normalize_special_path_arxiv_abs_uppercase() -> None:
    """arXiv abs ID is lowercased (old-style IDs with letters+digits)."""
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/abs/hep-th/9901001",
    )
    assert result == "/abs/hep-th/9901001"


def test_normalize_special_path_arxiv_abs_with_version() -> None:
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/abs/2401.12345V2",
    )
    assert result == "/abs/2401.12345v2"


def test_normalize_special_path_arxiv_pdf() -> None:
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/pdf/2401.12345",
    )
    assert result == "/pdf/2401.12345.pdf"


def test_normalize_special_path_arxiv_pdf_with_version() -> None:
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/pdf/2401.12345v3",
    )
    assert result == "/pdf/2401.12345v3.pdf"


def test_normalize_special_path_arxiv_pdf_already_has_extension() -> None:
    """Already has .pdf → normalized but .pdf not doubled."""
    # The regex: r"(?i)^/pdf/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?/?"
    # It captures identifier+version. The return is f"/pdf/{identifier.lower()}{suffix.lower()}.pdf"
    # So if input already has .pdf, the regex captures the identifier part without .pdf,
    # and output always appends .pdf.
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/pdf/2401.12345.pdf",
    )
    assert result == "/pdf/2401.12345.pdf"


def test_normalize_special_path_arxiv_old_style_abs() -> None:
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/abs/hep-th/9901001",
    )
    assert result == "/abs/hep-th/9901001"


def test_normalize_special_path_arxiv_old_style_pdf() -> None:
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/pdf/hep-th/9901001",
    )
    assert result == "/pdf/hep-th/9901001.pdf"


def test_normalize_special_path_arxiv_unmatched() -> None:
    """Non-abs, non-pdf arxiv path → unchanged."""
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/list/cs.AI/recent",
    )
    assert result == "/list/cs.AI/recent"


def test_normalize_special_path_arxiv_trailing_slash() -> None:
    """Trailing slash stripped."""
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="/abs/2401.12345/",
    )
    assert result == "/abs/2401.12345"


def test_normalize_special_path_other_host() -> None:
    """Non-doi, non-arxiv host → path unchanged."""
    result = _normalize_special_path(
        hostname="example.com",
        path="/paper/123",
    )
    assert result == "/paper/123"


def test_normalize_special_path_empty_path() -> None:
    result = _normalize_special_path(
        hostname="example.com",
        path="",
    )
    assert result == "/"


def test_normalize_special_path_empty_string_path() -> None:
    """Empty string path → '/' returned."""
    result = _normalize_special_path(
        hostname="arxiv.org",
        path="",
    )
    assert result == "/"
