import json

from pzi.doaj import fetch_doaj_pdf_url

_DOAJ_RESPONSE = {
    "total": 1,
    "results": [
        {
            "bibjson": {
                "title": "Test Article",
                "link": [
                    {
                        "url": "https://example.com/article.pdf",
                        "content_type": "PDF",
                        "type": "fulltext",
                    },
                    {
                        "url": "https://example.com/article",
                        "content_type": "HTML",
                        "type": "fulltext",
                    },
                ],
            }
        }
    ],
}


def test_fetch_doaj_pdf_url_extracts_pdf() -> None:
    result = fetch_doaj_pdf_url(
        "10.3389/fpsyg.2013.00479",
        fetch_text=lambda _: json.dumps(_DOAJ_RESPONSE),
    )
    assert result == "https://example.com/article.pdf"


def test_fetch_doaj_pdf_url_returns_none_without_results() -> None:
    result = fetch_doaj_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"total": 0, "results": []}),
    )
    assert result is None


def test_fetch_doaj_pdf_url_returns_none_without_pdf_link() -> None:
    response = {
        "total": 1,
        "results": [
            {
                "bibjson": {
                    "title": "Test Article",
                    "link": [
                        {
                            "url": "https://example.com/article",
                            "content_type": "HTML",
                        }
                    ],
                }
            }
        ],
    }
    result = fetch_doaj_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is None


def test_fetch_doaj_pdf_url_returns_none_on_error() -> None:
    def failing_fetch(url: str) -> str:
        raise OSError("network error")

    assert fetch_doaj_pdf_url("10.1234/foo", fetch_text=failing_fetch) is None
