import json

from pzi.metadata_sources import fetch_europepmc_pdf_url

_EUROPEPMC_OA_RESPONSE = {
    "resultList": {
        "result": [
            {
                "id": "23903748",
                "doi": "10.1038/nature12373",
                "title": "Nanometre-scale thermometry in a living cell",
                "fullTextUrlList": {
                    "fullTextUrl": [
                        {
                            "documentStyle": "pdf",
                            "availability": "OpenAccess",
                            "url": "https://europepmc.org/articles/PMC4221854?pdf=render",
                        },
                        {
                            "documentStyle": "html",
                            "availability": "OpenAccess",
                            "url": "https://europepmc.org/articles/PMC4221854",
                        },
                    ]
                },
            }
        ]
    }
}


def test_fetch_europepmc_pdf_url_extracts_oa_pdf() -> None:
    result = fetch_europepmc_pdf_url(
        "10.1038/nature12373",
        fetch_text=lambda _: json.dumps(_EUROPEPMC_OA_RESPONSE),
    )
    assert result == "https://europepmc.org/articles/PMC4221854?pdf=render"


def test_fetch_europepmc_pdf_url_returns_none_without_results() -> None:
    result = fetch_europepmc_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"resultList": {"result": []}}),
    )
    assert result is None


def test_fetch_europepmc_pdf_url_returns_none_without_pdf() -> None:
    response = {
        "resultList": {
            "result": [
                {
                    "doi": "10.1234/foo",
                    "fullTextUrlList": {
                        "fullTextUrl": [
                            {
                                "documentStyle": "html",
                                "availability": "OpenAccess",
                                "url": "https://example.com/article",
                            }
                        ]
                    },
                }
            ]
        }
    }
    result = fetch_europepmc_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps(response),
    )
    assert result is None


def test_fetch_europepmc_pdf_url_returns_none_on_error() -> None:
    def failing_fetch(url: str) -> str:
        raise OSError("network error")

    assert fetch_europepmc_pdf_url("10.1234/foo", fetch_text=failing_fetch) is None
