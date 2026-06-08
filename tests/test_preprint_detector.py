import pytest

from pzi.promote_service import detect_preprint_source, is_preprint


@pytest.mark.parametrize(
    "record,expected",
    [
        ({"venue": ""}, True),
        ({"venue": None}, True),
        ({"venue": "  "}, True),
        ({"venue": "NeurIPS"}, False),
        ({"venue": "NeurIPS", "arxiv_id": "2401.12345"}, True),
        ({"source_url": "https://arxiv.org/abs/2401.12345"}, True),
        ({"canonical_url": "https://biorxiv.org/content/10.1101/2024.01.01"}, True),
        ({"source_url": "https://medrxiv.org/content/10.1101/2024.01.01"}, True),
        ({"source_url": "https://hal.archives-ouvertes.fr/hal-01234567"}, True),
        ({"source_url": "https://osf.io/preprints/socarxiv/abc123"}, True),
        ({"source_url": "https://zenodo.org/record/1234567"}, True),
        ({"source_url": "https://peerj.com/articles/12345"}, True),
        ({"source_url": "https://example.com/paper"}, True),  # no venue = preprint
        ({"venue": "Nature", "source_url": "https://example.com/paper"}, False),
    ],
    ids=lambda p: str(p),
)
def test_is_preprint(record, expected):
    assert is_preprint(record) is expected


@pytest.mark.parametrize(
    "record,expected",
    [
        ({"arxiv_id": "2401.12345"}, "arXiv"),
        ({"source_url": "https://arxiv.org/abs/2401.12345"}, "arXiv"),
        ({"canonical_url": "https://biorxiv.org/content/10.1101/2024.01.01"}, "bioRxiv"),
        ({"source_url": "https://medrxiv.org/content/10.1101/2024.01.01"}, "medRxiv"),
        ({"source_url": "https://hal.archives-ouvertes.fr/hal-01234567"}, "HAL"),
        ({"source_url": "https://osf.io/preprints/socarxiv/abc123"}, "OSF"),
        ({"source_url": "https://zenodo.org/record/1234567"}, "Zenodo"),
        ({"source_url": "https://peerj.com/articles/12345"}, "PeerJ"),
        ({"venue": "Nature"}, None),
        ({}, None),
    ],
    ids=lambda p: str(p),
)
def test_detect_preprint_source(record, expected):
    assert detect_preprint_source(record) == expected
