"""Pure deduplication helpers: exact-identity matching + fuzzy similarity hints."""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Literal, TypeAlias

from pzi.bibtex import NormalizedRecord, normalize_authors

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SimilarityCandidate: TypeAlias = dict[str, Any]

IdentityKind = Literal["doi", "arxiv", "url"]

Identity: TypeAlias = dict[str, Any]

MatchableRecord = NormalizedRecord

# ---------------------------------------------------------------------------
# Exact-identity matching
# ---------------------------------------------------------------------------


def extract_identities(record: MatchableRecord) -> list[Identity]:
    """Extract exact-match identities from one normalized record."""
    candidates: list[tuple[IdentityKind, str | None]] = [
        ("doi", record.get("doi")),
        ("arxiv", record.get("arxiv_id")),
        ("url", record.get("canonical_url")),
    ]

    identities: list[Identity] = [
        {"kind": kind, "value": value}
        for kind, value in candidates
        if isinstance(value, str) and value.strip()
    ]
    return _deduplicate_identities(identities)


def build_identity_index(
    records: Sequence[MatchableRecord],
) -> dict[tuple[IdentityKind, str], list[int]]:
    """Index records by exact identity, preserving input positions."""
    index: dict[tuple[IdentityKind, str], list[int]] = {}
    for position, record in enumerate(records):
        for identity in extract_identities(record):
            key = (identity["kind"], identity["value"])
            index.setdefault(key, []).append(position)
    return index


def find_exact_match(
    record: MatchableRecord,
    existing_records: Sequence[MatchableRecord],
    *,
    index: dict[tuple[IdentityKind, str], list[int]] | None = None,
) -> int | None:
    """Return the first exact-match record position, or None when absent.

    Pass a prebuilt *index* (from :func:`build_identity_index`) to avoid
    rebuilding it on every call when matching repeatedly against the same
    ``existing_records`` — the add/capture write path does several lookups per
    entry.  When omitted, the index is built from *existing_records*.
    """
    identity_index = build_identity_index(existing_records) if index is None else index
    for identity in extract_identities(record):
        matches = identity_index.get((identity["kind"], identity["value"]))
        if matches:
            return matches[0]
    return None


def _deduplicate_identities(identities: list[Identity]) -> list[Identity]:
    seen: set[tuple[IdentityKind, str]] = set()
    deduplicated: list[Identity] = []
    for identity in identities:
        key = (identity["kind"], identity["value"])
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(identity)
    return deduplicated


# ---------------------------------------------------------------------------
# Fuzzy similarity
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str | None) -> str:
    if title is None:
        return ""
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    )
    return _NON_ALNUM.sub(" ", ascii_title.lower()).strip()


def title_tokens(title: str | None) -> set[str]:
    return {token for token in normalize_title(title).split() if len(token) > 2}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _to_ascii(text: str) -> str:
    """Decode HTML entities (DBLP emits ``&apos;``/``&amp;``) then strip diacritics."""
    decoded = html.unescape(text)
    return unicodedata.normalize("NFKD", decoded).encode("ascii", "ignore").decode("ascii")


def _split_family_given(name: str) -> tuple[str, str]:
    """Split a personal name into (family, given) in ASCII, lowercased.

    Handles both ``"Family, Given"`` and ``"Given Family"`` orderings.
    """
    ascii_name = _to_ascii(name).strip()
    if "," in ascii_name:
        family, _, given = ascii_name.partition(",")
        return family.strip().lower(), given.strip().lower()
    parts = ascii_name.split()
    if not parts:
        return "", ""
    return parts[-1].lower(), " ".join(parts[:-1]).lower()


def _normalize_author(name: str) -> str:
    family, _ = _split_family_given(name)
    return _NON_ALNUM.sub("", family)


def author_overlap(a: list[str], b: list[str]) -> int:
    norm_a = {_normalize_author(x) for x in a}
    norm_b = {_normalize_author(x) for x in b}
    norm_a.discard("")
    norm_b.discard("")
    return len(norm_a & norm_b)


def author_surnames(authors: Sequence[str]) -> list[str]:
    """Return normalized family names in input order, dropping empties."""
    return [s for s in (_normalize_author(a) for a in authors) if s]


def is_alphabetized_record(doi: object) -> bool:
    """True for sources that publish authors A–Z rather than as-submitted.

    Crossref proceedings deposits under the ``10.52202`` prefix (NeurIPS / ICML)
    sort contributors alphabetically, so a surname reordering against such a
    record is a deposit artifact, not a genuine author swap.
    """
    return isinstance(doi, str) and doi.strip().lower().startswith("10.52202")


def authors_swapped(
    entry: Sequence[str],
    candidate: Sequence[str],
    *,
    candidate_alphabetized: bool = False,
) -> bool:
    """True when both lists hold the same surname multiset but a different order.

    Pass ``candidate_alphabetized=True`` (see :func:`is_alphabetized_record`) to
    suppress the flag for sources that sort authors A–Z, where a reordering is a
    record artifact rather than a real swap.
    """
    e = author_surnames(entry)
    c = author_surnames(candidate)
    if len(e) < 2 or sorted(e) != sorted(c) or e == c:
        return False
    if candidate_alphabetized:
        return False
    return True


GivenPair = Literal["match", "variant", "substitution"]


def classify_given_pair(a: str, b: str) -> GivenPair:
    """Classify two given-name strings as match / variant / substitution.

    ``variant`` covers initials, abbreviations, diacritic/transliteration noise,
    and added/dropped middle names — anything consistent with the same person.
    A genuinely different first name returns ``substitution``.
    """
    na = _NON_ALNUM.sub(" ", _to_ascii(a).lower()).strip()
    nb = _NON_ALNUM.sub(" ", _to_ascii(b).lower()).strip()
    if not na or not nb:
        return "variant"  # missing data: not evidence of substitution
    if na == nb:
        return "match"
    first_a, first_b = na.split(), nb.split()
    head_a, head_b = first_a[0], first_b[0]
    # Initial vs full ("j" / "john"), or one a prefix of the other.
    if head_a[0] != head_b[0]:
        return "substitution"
    if head_a == head_b or head_a.startswith(head_b) or head_b.startswith(head_a):
        return "variant"
    if len(head_a) == 1 or len(head_b) == 1:  # single initial sharing first letter
        return "variant"
    return "substitution"


def levenshtein_within_1(a: str, b: str) -> bool:
    """True iff the edit distance between *a* and *b* is at most 1.

    Bounded check — no full DP matrix.  Catches single-character title typos
    (``"Privacy"`` vs ``"Privacys"``) that whole-token similarity waves through.
    """
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:  # at most one substitution
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    # Lengths differ by exactly one: allow a single insertion / deletion.
    shorter, longer = (a, b) if la < lb else (b, a)
    i = j = 0
    edited = False
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
        elif edited:
            return False
        else:
            edited = True
            j += 1  # consume one extra char from the longer string
    return True


_AUTHOR_SENTINELS = frozenset({"others", "et al", "etal"})


def is_truncation_sentinel(author: str) -> bool:
    """True when an author entry is an ``and others`` / ``et al`` truncation marker."""
    token = _to_ascii(author).strip().lower().rstrip(".")
    return token in _AUTHOR_SENTINELS


def has_truncation_sentinel(authors: Sequence[str]) -> bool:
    """True when any author entry discloses truncation (``and others`` / ``et al``)."""
    return any(is_truncation_sentinel(a) for a in authors)


def compute_similarity_hint(
    record: SimilarityCandidate,
    existing_records: Sequence[SimilarityCandidate],
    *,
    title_threshold: float = 0.6,
    year_window: int = 2,
) -> str | None:
    """Return the citekey of the most similar existing record, if any."""
    record_tokens = title_tokens(record.get("title"))
    if not record_tokens:
        return None

    record_authors = normalize_authors(record.get("authors"))
    record_year = record.get("year")

    best_key: str | None = None
    best_score: float = 0.0
    for existing in existing_records:
        citekey = existing.get("citekey")
        if not isinstance(citekey, str) or not citekey.strip():
            continue
        existing_tokens = title_tokens(existing.get("title"))
        similarity = jaccard_similarity(record_tokens, existing_tokens)
        if similarity < title_threshold:
            continue

        existing_year = existing.get("year")
        if (
            record_year is not None
            and existing_year is not None
            and abs(record_year - existing_year) > year_window
        ):
            continue

        overlap = author_overlap(record_authors, list(existing.get("authors") or []))
        if overlap == 0 and similarity < 0.85:
            continue  # pragma: no cover — covered by integration/browser tests

        score = similarity + 0.1 * overlap
        if score > best_score:  # pragma: no branch — covered by integration/browser tests
            best_score = score
            best_key = citekey

    return best_key
