import pytest

from pzi.metadata_sources import (
    fetch_crossref_record,
    fetch_openalex_record,
    fetch_semantic_scholar_record,
)
from pzi.pdf import fetch_unpaywall_pdf_url

DOI = "10.1038/nphys1170"
OA_DOI = "10.1371/journal.pone.0000308"


def test_live_crossref_resolves_known_doi(contact_email: str | None) -> None:
    record = fetch_crossref_record(DOI, contact_email=contact_email)

    assert record is not None
    assert record.get("doi") == DOI
    assert record.get("title")
    assert record.get("authors")


def test_live_openalex_resolves_known_doi(contact_email: str | None) -> None:
    record = fetch_openalex_record(DOI, contact_email=contact_email)

    assert record is not None
    assert record.get("doi") == DOI
    assert record.get("title")
    assert record.get("authors")


def test_live_semantic_scholar_resolves_known_doi_when_key_set(s2_api_key: str | None) -> None:
    if not s2_api_key:
        pytest.skip("PZI_S2_API_KEY unset")

    record = fetch_semantic_scholar_record(DOI, api_key=s2_api_key)

    assert record is not None
    assert record.get("doi") == DOI
    assert record.get("title")


def test_live_unpaywall_finds_open_access_pdf_when_email_set(unpaywall_email: str | None) -> None:
    if not unpaywall_email:
        pytest.skip("PZI_UNPAYWALL_EMAIL/PZI_CONTACT_EMAIL unset")

    pdf_url = fetch_unpaywall_pdf_url(OA_DOI, email=unpaywall_email)

    if pdf_url is None:
        pytest.skip("Unpaywall returned no PDF for the test DOI (third-party data/availability)")
    assert pdf_url.startswith(("http://", "https://"))
