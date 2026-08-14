"""Pure identifier normalization and classification helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pzi.bibtex import ClassifiedInput

_YEAR_PATTERN = re.compile(r"(19|20)\d{2}")

InputKind = Literal["doi", "url", "pdf_url", "local_pdf", "unknown"]

TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
    }
)

DOI_PATTERN = re.compile(
    r"(?i)^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s?#]+)(?:[?#]\S*)?$"
)
# `(?:[a-z]+/)?` absorbs the display segment publishers put between /doi/ and
# the DOI itself -- ACM's /doi/abs/, Wiley's /doi/full/, /doi/pdf/, /doi/epdf/.
# Without it those URLs yielded no DOI at all. Kept to a lowercase word rather
# than `[^/]+/`, which would start swallowing arbitrary junk.
DOI_IN_PATH_PATTERN = re.compile(r"(?i)/doi/(?:[a-z]+/)?(10\.\d{4,9}/[^\s?#]+)")
# `(?:\.[a-z]{2})?` admits the dotted subject class in old-style arXiv IDs
# (math.GT/0309136); `[a-z\-]+` alone excluded the dot, so those fell through to
# being classified as a plain URL and lost their DOI mapping.
_ARXIV_ID = r"[a-z\-]+(?:\.[a-z]{2})?/\d{7}|\d{4}\.\d{4,5}"
ARXIV_ABS_PATTERN = re.compile(rf"(?i)^/abs/({_ARXIV_ID})(v\d+)?/?$")
ARXIV_PDF_PATTERN = re.compile(rf"(?i)^/pdf/({_ARXIV_ID})(v\d+)?(?:\.pdf)?/?$")
# A bare arXiv ID, however it was handed to us: Zotero's `archiveID` carries an
# `arXiv:` prefix, arXiv's own pages carry a version suffix, and a pasted
# abs/pdf URL carries both plus a host.
_ARXIV_BARE = re.compile(
    rf"(?i)^(?:arxiv:\s*)?(?:https?://arxiv\.org/(?:abs|pdf)/)?({_ARXIV_ID})(?:v\d+)?(?:\.pdf)?/?$"
)


def normalize_arxiv_id(value: str) -> str | None:
    """Return a bare, comparable arXiv ID, or ``None`` when *value* is not one.

    Stored verbatim, the same paper carried a different identity depending on
    which route captured it — `2301.12345` from a pasted URL, `arXiv:2301.12345`
    from the translation server, `2301.12345v2` from arXiv itself — so dedupe
    missed it. The prefix is also wrong to keep in the entry: `archiveprefix`
    already supplies it, so `eprint = {arXiv:2301.12345}` renders a citation
    reading "arXiv:arXiv:2301.12345".

    The subject class of an old-style ID (`math.GT/0309136`) is part of the ID
    and is preserved; only the case of the `arXiv:` prefix is normalized, since
    the ID's own case is significant.
    """
    match = _ARXIV_BARE.match(value.strip())
    return match.group(1) if match else None




def normalize_doi(value: str) -> str | None:
    """Return a canonical DOI string, or None if the input is not DOI-like.

    Case is folded and a trailing slash dropped, since neither distinguishes
    two DOIs. The suffix stops at ``?``/``#``: doi.org forwards a query string to the
    resolved target rather than treating it as part of the DOI, so a pasted
    ``doi.org`` link with tracking params (``?utm_source=...``) or an anchor
    must not have that cruft folded into the stored identifier.
    """
    candidate = value.strip()
    match = DOI_PATTERN.match(candidate)
    if match is None and _is_doi_org_url(candidate):
        # A percent-encoded doi.org link — `https://doi.org/10.1145%2F1327452…`
        # — is what a browser address bar hands over for some publishers. It
        # failed the pattern (the `/` is escaped), was classified as a plain
        # URL, and the entry was then written with no identifier at all and
        # never deduped. Decode once and re-test rather than loosening the
        # pattern, which would also start accepting encoded junk.
        match = DOI_PATTERN.match(unquote(candidate))
    if match is None:
        return None

    doi = match.group(1).strip()
    doi = re.sub(r"\s+", "", doi)
    doi = doi.rstrip(".,;:)]}")
    # A trailing slash is not part of the DOI: `10.1145/abc/` and `10.1145/abc`
    # are the same paper, and treating them as different identities made a
    # re-capture create a duplicate entry.
    doi = doi.rstrip("/")
    return doi.lower()


def _is_doi_org_url(value: str) -> bool:
    try:
        return (urlsplit(value).hostname or "").lower() in {"doi.org", "dx.doi.org"}
    except ValueError:  # pragma: no cover — urlsplit rejects very odd input
        return False


def normalize_url(value: str) -> str | None:
    """Return a normalized HTTP(S) URL, or None if the input is not a supported URL."""
    candidate = value.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    if not parts.netloc:
        return None

    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return None  # pragma: no cover — covered by integration/browser tests

    try:
        port = parts.port
    except ValueError:
        return None
    has_default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    # urlsplit strips the brackets from IPv6 literals; restore them so the
    # rebuilt URL stays valid (e.g. http://[2606:...]/paper).
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    netloc = (
        host_for_netloc
        if port is None or has_default_port
        else f"{host_for_netloc}:{port}"
    )

    path = parts.path or "/"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(query_items, doseq=True)

    normalized_path = _normalize_special_path(hostname=hostname, path=path)
    return urlunsplit((scheme, netloc, normalized_path, query, ""))


def classify_input(value: str) -> ClassifiedInput:
    """Classify raw input into doi, url, pdf_url, or unknown."""
    normalized_doi = normalize_doi(value)
    if normalized_doi is not None:
        return {"kind": "doi", "raw": value, "normalized": normalized_doi}

    normalized_url = normalize_url(value)
    if normalized_url is None:
        stripped = value.strip()
        if stripped.lower().endswith(".pdf") and "://" not in stripped:
            return {"kind": "local_pdf", "raw": value, "normalized": stripped}
        # A bare arXiv ID — `2301.07041`, `arXiv:2301.07041v2`, `math.GT/0309136`.
        # `normalize_arxiv_id` has handled prefixes, versions and old-style
        # subject classes all along; `classify_input` simply never consulted it,
        # so the identifier arXiv papers are *actually* cited by was "not a DOI,
        # URL, or local PDF path". Resolved through the abs page, which is what
        # a user pasting the URL would have got.
        arxiv_id = normalize_arxiv_id(stripped)
        if arxiv_id is not None:
            return {
                "kind": "url",
                "raw": value,
                "normalized": f"https://arxiv.org/abs/{arxiv_id}",
            }
        return {"kind": "unknown", "raw": value, "normalized": None}

    url_parts = urlsplit(normalized_url)
    doi_match = DOI_IN_PATH_PATTERN.search(url_parts.path)
    if doi_match is not None:
        embedded_doi = normalize_doi(doi_match.group(1))
        if embedded_doi is not None:  # pragma: no branch — covered by integration/browser tests
            return {"kind": "doi", "raw": value, "normalized": embedded_doi}

    if url_parts.hostname in {"arxiv.org", "www.arxiv.org"}:
        arxiv_id = _extract_arxiv_id_from_url_path(url_parts.path)
        if arxiv_id is not None:
            return {
                "kind": "doi",
                "raw": value,
                "normalized": f"10.48550/arxiv.{arxiv_id}",
            }

    if url_parts.hostname in {"biorxiv.org", "www.biorxiv.org",
                               "medrxiv.org", "www.medrxiv.org"}:
        doi = _extract_doi_from_biorxiv_path(url_parts.path)
        if doi is not None:
            return {"kind": "doi", "raw": value, "normalized": doi}

    if url_parts.hostname in {"zenodo.org", "www.zenodo.org"}:
        zenodo_id = _extract_zenodo_id(url_parts.path)
        if zenodo_id is not None:
            return {
                "kind": "doi",
                "raw": value,
                "normalized": f"10.5281/zenodo.{zenodo_id}",
            }

    is_pdf = (
        url_parts.path.lower().endswith(".pdf")
        or (
            url_parts.hostname in {"arxiv.org", "www.arxiv.org"}
            and ARXIV_PDF_PATTERN.match(url_parts.path)
        )
    )
    kind: InputKind = "pdf_url" if is_pdf else "url"
    return {"kind": kind, "raw": value, "normalized": normalized_url}


def _extract_arxiv_id_from_url_path(path: str) -> str | None:
    """Extract arXiv identifier from an arXiv URL path, or None."""
    for pattern in (ARXIV_ABS_PATTERN, ARXIV_PDF_PATTERN):
        match = pattern.match(path)
        if match is not None:
            return match.group(1).lower()
    return None


_BIORXIV_DOI_RE = re.compile(
    r"(?i)^/content/(10\.\d{4,9}/\S+?)(?:v\d+)?(?:\.[a-z]+)*/?$"
)


def _extract_doi_from_biorxiv_path(path: str) -> str | None:
    """Extract DOI from a bioRxiv/medRxiv URL path, stripping version suffix."""
    match = _BIORXIV_DOI_RE.match(path)
    if match is None:
        return None
    return normalize_doi(match.group(1))


_ZENODO_ID_RE = re.compile(r"(?i)^/(?:records?)/(\d+)/?$")


def _extract_zenodo_id(path: str) -> str | None:
    """Extract Zenodo record ID from path, e.g. /records/1234567 → 1234567."""
    match = _ZENODO_ID_RE.match(path)
    if match is None:
        return None
    return match.group(1)


def _normalize_special_path(*, hostname: str, path: str) -> str:
    if hostname == "doi.org":
        # Percent-decode first: `https://doi.org/10.1145%2F1327452.1327492` is a
        # DOI URL, but `normalize_doi` saw `10.1145%2F...`, rejected it, and the
        # input was classified as a plain URL — so the entry was written with no
        # identifier at all and never deduped.
        stripped = unquote(path.lstrip("/"))
        normalized_doi = normalize_doi(stripped)
        return f"/{normalized_doi}" if normalized_doi is not None else path

    if hostname == "arxiv.org":
        abs_match = ARXIV_ABS_PATTERN.match(path)
        if abs_match is not None:
            identifier, version = abs_match.groups()
            suffix = version or ""
            return f"/abs/{identifier.lower()}{suffix.lower()}"

        pdf_match = ARXIV_PDF_PATTERN.match(path)
        if pdf_match is not None:
            identifier, version = pdf_match.groups()
            suffix = version or ""
            return f"/pdf/{identifier.lower()}{suffix.lower()}.pdf"

    return path or "/"


def extract_year_from_str(value: str) -> int | None:
    """Extract a four-digit year string from a date string, or None."""
    match = _YEAR_PATTERN.search(value)
    return int(match.group(0)) if match else None


# ---------------------------------------------------------------------------
# Preprint classification
# ---------------------------------------------------------------------------
# Pure classifiers, kept here rather than in promote_service: bib_repository,
# bib_service and pdf_discovery all need them, and reaching up into a service
# for a lookup table forced function-level imports to dodge an import cycle.

_PREPRINT_DOMAINS = frozenset({
    # Life sciences / medicine
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    # Chemistry
    "chemrxiv.org",
    # Psychology / social sciences
    "psyarxiv.com",
    "socarxiv.org",
    # Engineering / physical sciences
    "engrxiv.org",
    "techrxiv.org",
    "eartharxiv.org",
    # Multidisciplinary
    "ecoevorxiv.org",
    "researchsquare.com",
    "preprints.org",
    "osf.io",
    "zenodo.org",
    "authorea.com",
    "advance.sagepub.com",
    "papers.ssrn.com",
    # Regional / institutional
    "hal.archives-ouvertes.fr",
    "hal.science",
    "peerj.com",
})


def is_preprint(record: Mapping[str, object]) -> bool:
    """Return True when the record looks like a preprint."""
    venue = record.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return True
    if record.get("arxiv_id"):
        return True
    if is_preprint_url(record.get("source_url")):
        return True
    if is_preprint_url(record.get("canonical_url")):
        return True
    return False


#: DataCite prefix arXiv mints for every submission, e.g. `10.48550/arXiv.2301.07041`.
PREPRINT_DOI_PREFIX = "10.48550/"


def is_preprint_doi(doi: object) -> bool:
    """True when *doi* is arXiv's own DataCite DOI rather than a publisher's."""
    return isinstance(doi, str) and doi.strip().lower().startswith(PREPRINT_DOI_PREFIX)


def has_preprint_identity(record: Mapping[str, object]) -> bool:
    """True when the record itself carries preprint identity.

    Deliberately narrower than :func:`is_preprint`, which also treats a record
    with no ``venue`` as a preprint. That is most of a working library, so it is
    far too broad to gate a *write* on: `update` uses this to decide whether a
    DOI disagreement is the legitimate preprint-vs-published pairing, and
    `promote` needs the same narrow test to choose what to promote at all.

    The venue/publisher test is what lets `promote` recognise a preprint
    *candidate*: DBLP and Semantic Scholar return arXiv records carrying
    `CoRR` or `arXiv (Cornell University)` and frequently no DOI at all, so the
    identifier tests above see nothing to object to.
    """
    if record.get("arxiv_id"):
        return True
    if is_preprint_doi(_canonical_doi_for_identity(record.get("doi"))):
        return True
    if names_a_preprint_server(record.get("venue")) or names_a_preprint_server(
        record.get("publisher")
    ):
        return True
    return is_preprint_url(record.get("source_url")) or is_preprint_url(
        record.get("canonical_url")
    )


def _canonical_doi_for_identity(doi: object) -> str | None:
    """Local canonicalizer, kept here so this module stays leaf-level.

    `similarity.canonical_doi` does the same job, but importing it would point
    an identifier primitive at the matching layer.
    """
    return normalize_doi(doi) if isinstance(doi, str) else None


_DOMAIN_TO_SOURCE: dict[str, str] = {
    # Life sciences / medicine
    "arxiv.org": "arXiv",
    "biorxiv.org": "bioRxiv",
    "medrxiv.org": "medRxiv",
    # Chemistry
    "chemrxiv.org": "ChemRxiv",
    # Psychology / social sciences
    "psyarxiv.com": "PsyArXiv",
    "socarxiv.org": "SocArXiv",
    "papers.ssrn.com": "SSRN",
    # Engineering / physical sciences
    "engrxiv.org": "engrXiv",
    "techrxiv.org": "TechRxiv",
    "eartharxiv.org": "EarthArXiv",
    # Multidisciplinary
    "ecoevorxiv.org": "EcoEvoRxiv",
    "researchsquare.com": "Research Square",
    "preprints.org": "Preprints.org",
    "osf.io": "OSF",
    "zenodo.org": "Zenodo",
    "authorea.com": "Authorea",
    "advance.sagepub.com": "SAGE Advance",
    # Regional / institutional
    "hal.archives-ouvertes.fr": "HAL",
    "hal.science": "HAL",
    "peerj.com": "PeerJ",
}

#: Preprint servers that also publish a version of record, so their *name* in a
#: `venue` says nothing about whether an item is a preprint. A PeerJ article and
#: a PeerJ preprint share a venue string; so do Zenodo, OSF and HAL deposits.
#: Excluded from the venue test below, and only from that one — a URL on these
#: domains is still preprint evidence, which is what `_PREPRINT_DOMAINS` is for.
_ALSO_PUBLISHES = frozenset({"PeerJ", "Zenodo", "OSF", "HAL"})

#: Venue/publisher names that mean "this is a preprint", derived from the server
#: names already in `_DOMAIN_TO_SOURCE` rather than re-listing them. `CoRR` is
#: the exception that has no domain: it is what DBLP and Semantic Scholar call
#: arXiv, and it is how an arXiv record reaches `promote` looking publishable.
_PREPRINT_VENUE_NAMES = frozenset(
    {name for name in _DOMAIN_TO_SOURCE.values() if name not in _ALSO_PUBLISHES}
    | {"CoRR"}
)


def _normalized_venue(value: object) -> str:
    """Lowercase *value*, punctuation flattened, letters split from digits.

    The digit split is what catches DBLP's `CoRR2019`, which is one token to a
    plain punctuation-flattener and so matched neither `corr` nor `corr `. It
    was the last arXiv record to survive the promote filter on a real slice.
    """
    if not isinstance(value, str):
        return ""
    flattened = re.sub(r"[^0-9a-z]+", " ", value.lower())
    return " ".join(re.sub(r"(?<=[a-z])(?=\d)", " ", flattened).split())


#: The preprint venue names, normalized once at import rather than per call.
_NORMALIZED_PREPRINT_VENUES = frozenset(
    _normalized_venue(name) for name in _PREPRINT_VENUE_NAMES
)


def names_a_preprint_server(value: object) -> bool:
    """True when a ``venue`` or ``publisher`` names a preprint server.

    Matched as a *leading name*, not a substring: `arXiv (Cornell University)`
    and `CoRR` are preprint venues, while a journal whose title merely begins
    with the same letters (`Corrigendum...`) is not. Substring matching here
    would reject real publications, which is the expensive direction to be wrong
    in — a missed preprint is one bad promotion, a rejected publication is a
    promotion that never happens at all and is never reported as missing.
    """
    venue = _normalized_venue(value)
    if not venue:
        return False
    return any(
        venue == name or venue.startswith(f"{name} ")
        for name in _NORMALIZED_PREPRINT_VENUES
    )


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


def is_preprint_url(value: object) -> bool:
    """True when *value* is a URL on a known preprint server."""
    domain = _url_domain(value)
    return domain in _PREPRINT_DOMAINS if domain is not None else False


def _url_domain(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:  # pragma: no cover — covered by integration/browser tests
        return None  # pragma: no cover — covered by integration/browser tests
    host = parts.hostname
    if host is None:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host
