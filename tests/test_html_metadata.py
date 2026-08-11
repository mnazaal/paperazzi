"""Tests for html_metadata extraction."""

from pzi.html_metadata import extract_metadata_from_html

CITATION_META_HTML = """
<html>
<head>
<meta name="citation_title" content="Attention Is All You Need">
<meta name="citation_author" content="Vaswani, Ashish">
<meta name="citation_author" content="Shazeer, Noam">
<meta name="citation_publication_date" content="2017">
<meta name="citation_conference_title" content="NeurIPS">
<meta name="citation_doi" content="10.48550/arXiv.1706.03762">
<meta name="citation_pdf_url" content="https://arxiv.org/pdf/1706.03762">
</head>
<body></body>
</html>
"""

JSON_LD_HTML = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "ScholarlyArticle",
  "name": "BERT: Pre-training of Deep Bidirectional Transformers",
  "author": [
    {"@type": "Person", "name": "Devlin, Jacob"},
    {"@type": "Person", "name": "Chang, Ming-Wei"}
  ],
  "datePublished": "2019",
  "identifier": "10.18653/v1/N19-1423"
}
</script>
</head>
<body></body>
</html>
"""

OG_ONLY_HTML = """
<html>
<head>
<meta property="og:title" content="Some Article Title">
</head>
<body></body>
</html>
"""

EMPTY_HTML = "<html><head></head><body></body></html>"


def test_citation_meta_tags():
    result = extract_metadata_from_html(CITATION_META_HTML)
    assert result is not None
    assert result["title"] == "Attention Is All You Need"
    assert result["authors"] == ["Vaswani, Ashish", "Shazeer, Noam"]
    assert result["year"] == 2017
    assert result["venue"] == "NeurIPS"
    assert result["doi"] == "10.48550/arxiv.1706.03762"
    assert result.get("pdf_url") == "https://arxiv.org/pdf/1706.03762"


def test_json_ld_fallback():
    result = extract_metadata_from_html(JSON_LD_HTML)
    assert result is not None
    assert result["title"] == "BERT: Pre-training of Deep Bidirectional Transformers"
    assert "Devlin, Jacob" in result["authors"]
    assert result["year"] == 2019
    assert result["doi"] is not None


def test_og_title_only():
    result = extract_metadata_from_html(OG_ONLY_HTML)
    assert result is not None
    assert result["title"] == "Some Article Title"


def test_empty_returns_none():
    result = extract_metadata_from_html(EMPTY_HTML)
    assert result is None


def test_citation_meta_preferred_over_json_ld():
    html = CITATION_META_HTML + JSON_LD_HTML
    result = extract_metadata_from_html(html)
    assert result is not None
    assert result["title"] == "Attention Is All You Need"


def test_malformed_json_ld_silently_skipped():
    """Malformed JSON-LD should be skipped, not crash metadata extraction."""
    html = (
        '<html><head>\n'
        '<script type="application/ld+json">{broken</script>\n'
        "</head><body></body></html>"
    )
    result = extract_metadata_from_html(html)
    assert result is None  # no usable metadata extracted


SINGLE_AUTHOR_JSON_LD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@type": "ScholarlyArticle",
  "name": "A Single-Author Paper",
  "author": {"@type": "Person", "name": "Ada Lovelace"},
  "datePublished": "2024-03-01"
}
</script>
</head><body></body></html>
"""


def test_json_ld_single_author_object_is_not_iterated_as_a_dict():
    """schema.org allows one author object instead of a list.

    A bare dict is iterable — over its keys — so the old loop harvested
    "@type" and "name" and wrote those into the library as the author names.
    """
    record = extract_metadata_from_html(SINGLE_AUTHOR_JSON_LD_HTML)

    assert record["authors"] == ["Ada Lovelace"]
    assert "@type" not in record["authors"]


def test_json_ld_bare_string_author_is_accepted():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "ScholarlyArticle", "name": "P", "author": "Ada Lovelace"}
    </script>
    </head><body></body></html>
    """

    assert extract_metadata_from_html(html)["authors"] == ["Ada Lovelace"]


def test_json_ld_author_list_of_objects_still_works():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "ScholarlyArticle", "name": "P",
     "author": [{"@type": "Person", "name": "Ada Lovelace"},
                {"@type": "Person", "name": "Grace Hopper"}]}
    </script>
    </head><body></body></html>
    """

    assert extract_metadata_from_html(html)["authors"] == [
        "Ada Lovelace", "Grace Hopper",
    ]


def test_a_cloudflare_interstitial_is_not_captured_as_the_paper() -> None:
    """The challenge page has a title, so it satisfied every check.

    It arrives on exactly the captures that most need to fail loudly — the ones
    where FlareSolverr did not solve the challenge — and was written as
    `title = {Just a moment...}`.
    """
    for title in (
        "Just a moment...",
        "Attention Required! | Cloudflare",
        "Checking your browser before accessing",
        "Access denied",
    ):
        html = f"<html><head><title>{title}</title>" \
               f'<meta property="og:title" content="{title}"></head><body></body></html>'
        assert extract_metadata_from_html(html) is None, title


def test_an_interstitial_title_with_a_doi_is_still_kept() -> None:
    """A page carrying a real DOI is not a challenge page, whatever its title —
    refusing on the title alone would discard a real capture."""
    html = (
        '<html><head><meta name="citation_doi" content="10.1234/real">'
        '<meta property="og:title" content="Just a moment..."></head></html>'
    )
    record = extract_metadata_from_html(html)
    assert record is not None
    assert record["doi"] == "10.1234/real"


def test_a_news_article_is_not_a_scholarly_article() -> None:
    """`"NewsArticle"` contains `"Article"`, and the type test was a substring
    match — so a news page's JSON-LD was read as a paper's."""
    html = """
    <html><head><script type="application/ld+json">
    {"@type": "NewsArticle", "headline": "Scientists Astonished By Thing",
     "author": {"name": "A Journalist"}, "datePublished": "2024-01-01"}
    </script></head></html>
    """
    assert extract_metadata_from_html(html) is None


def test_a_scholarly_article_is_still_read() -> None:
    """The tightening must not stop the type it exists to accept."""
    html = """
    <html><head><script type="application/ld+json">
    {"@type": "ScholarlyArticle", "name": "Attention Is All You Need",
     "author": {"name": "Vaswani, Ashish"}, "datePublished": "2017-06-12"}
    </script></head></html>
    """
    record = extract_metadata_from_html(html)
    assert record is not None
    assert record["title"] == "Attention Is All You Need"


def test_a_json_ld_type_list_still_matches() -> None:
    """JSON-LD allows `@type` to be a list; exact matching must handle it."""
    html = """
    <html><head><script type="application/ld+json">
    {"@type": ["CreativeWork", "ScholarlyArticle"], "name": "A Real Paper"}
    </script></head></html>
    """
    record = extract_metadata_from_html(html)
    assert record is not None
    assert record["title"] == "A Real Paper"
