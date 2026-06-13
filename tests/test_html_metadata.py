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
