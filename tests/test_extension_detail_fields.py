"""The browser extension's scraped detail fields must reach the .bib."""

from pzi.add_planning import split_record_overrides
from pzi.bibtex import record_to_bibtex_entry
from pzi.http_post_routes import record_overrides_from_capture_body


def test_extension_scraped_detail_fields_reach_the_bib_entry() -> None:
    """`metadata.js` has scraped these since it was written; nothing read them.

    The chain is: extension scrapes `embedded_*` -> POST body ->
    `record_overrides_from_capture_body` -> `fallback_*` -> `split_record_
    overrides` strips the prefix -> record -> writer. It was intact until the
    last two steps, so the data crossed the whole network boundary and was
    dropped on the floor.
    """
    overrides = record_overrides_from_capture_body(
        {
            "embedded_volume": "51",
            "embedded_issue": "1",
            "embedded_pages": "107-113",
            "embedded_issn": "0001-0782",
            "embedded_isbn": "978-1-4503-0000-0",
        }
    )

    _normal, fallback = split_record_overrides(overrides)
    entry = record_to_bibtex_entry({"citekey": "k", "title": "T", **fallback})

    assert entry["fields"]["volume"] == "51"
    assert entry["fields"]["number"] == "1", "BibTeX calls the issue `number`"
    assert entry["fields"]["pages"] == "107--113", "en-dash, whatever the source"
    assert entry["fields"]["issn"] == "0001-0782"
    assert entry["fields"]["isbn"] == "978-1-4503-0000-0"
