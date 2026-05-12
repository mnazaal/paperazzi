"""Pure preprint classification helpers."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

_PREPRINT_DOMAINS = frozenset({
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "hal.archives-ouvertes.fr",
    "osf.io",
    "zenodo.org",
    "peerj.com",
})


def is_preprint(record: Mapping[str, object]) -> bool:
    """Return True when the record looks like a preprint."""
    venue = record.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return True
    if record.get("arxiv_id"):
        return True
    if _url_domain_on_preprint(record.get("source_url")):
        return True
    if _url_domain_on_preprint(record.get("canonical_url")):
        return True
    return False


_DOMAIN_TO_SOURCE: dict[str, str] = {
    "arxiv.org": "arXiv",
    "biorxiv.org": "bioRxiv",
    "medrxiv.org": "medRxiv",
    "hal.archives-ouvertes.fr": "HAL",
    "osf.io": "OSF",
    "zenodo.org": "Zenodo",
    "peerj.com": "PeerJ",
}


def detect_preprint_source(record: Mapping[str, object]) -> str | None:
    """Identify the preprint server, if any."""
    arxiv_id = record.get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id.strip():
        return "arXiv"

    for url_field in ("source_url", "canonical_url"):
        domain = _url_domain(record.get(url_field))
        if domain is not None and domain in _DOMAIN_TO_SOURCE:
            return _DOMAIN_TO_SOURCE[domain]
    return None


def _url_domain_on_preprint(value: object) -> bool:
    domain = _url_domain(value)
    return domain in _PREPRINT_DOMAINS if domain is not None else False


def _url_domain(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    host = parts.hostname
    return host.lower() if host is not None else None
