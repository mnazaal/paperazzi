"""Pure deduplication helpers: exact-identity matching + fuzzy similarity hints."""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, Literal, NamedTuple, TypeAlias

from pzi.bibtex import NormalizedRecord, normalize_authors
from pzi.identifiers import normalize_arxiv_id, normalize_doi, normalize_url

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
    """Extract exact-match identities from one normalized record.

    The DOI is canonicalized first. Comparing stored DOIs verbatim meant a
    library holding ``10.1145/abc`` did not match an incoming ``10.1145/ABC`` or
    ``10.1145/abc/``, so re-capturing a paper wrote a second entry for it.
    """
    candidates: list[tuple[IdentityKind, str | None]] = [
        ("doi", canonical_doi(record.get("doi"))),
        ("arxiv", _canonical_arxiv_id(record.get("arxiv_id"))),
        ("url", _canonical_url(record.get("canonical_url"))),
    ]

    identities: list[Identity] = [
        {"kind": kind, "value": value}
        for kind, value in candidates
        if isinstance(value, str) and value.strip()
    ]
    return _deduplicate_identities(identities)


def _canonical_url(value: object) -> str | None:
    """Canonical form of a stored URL, or ``None`` when the value is not one.

    Taken verbatim before, so ``https://x.org/p``, ``https://X.org/p`` and
    ``https://x.org/p/`` were three identities and re-capturing one page wrote a
    second entry. Normalizing is only safe alongside the corroboration in
    :func:`find_exact_match`, since it makes strictly *more* records collide.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = normalize_url(value)
    if normalized is None:
        return None
    # Only for identity. `normalize_url` keeps the trailing slash because the
    # stored value should stay the URL the user or provider gave; `/p` and `/p/`
    # are still the same page to compare against.
    trimmed = normalized.rstrip("/")
    return trimmed or normalized


def _titles_corroborate(a: MatchableRecord, b: MatchableRecord) -> bool:
    """Do two records' titles agree well enough to call them the same paper?

    Requires positive agreement rather than absence of disagreement: a record
    with no title cannot corroborate anything, and treating "cannot tell" as
    "yes" is what the URL identity did wrong in the first place.
    """
    left = title_tokens(a.get("title") if isinstance(a.get("title"), str) else None)
    right = title_tokens(b.get("title") if isinstance(b.get("title"), str) else None)
    if not left or not right:
        return False
    overlap = len(left & right)
    return overlap / max(len(left), len(right)) >= _URL_TITLE_AGREEMENT


#: Share of the longer title's tokens two records must have in common before a
#: shared URL is accepted as identity. Deliberately loose — it is separating
#: "the same paper, maybe a subtitle differs" from "two unrelated papers on one
#: publisher landing page", not scoring a match.
_URL_TITLE_AGREEMENT = 0.6


def canonical_doi(value: object) -> str | None:
    """Canonical form of a stored DOI, or ``None`` when the value is not one.

    A value the DOI parser rejects used to fall back to a case-folded strip, so
    that a malformed field would at least match itself. But the values that
    reach this are overwhelmingly *placeholders* — `n/a`, `-`, `TODO` — and a
    placeholder is the absence of a DOI, not a shared one: every entry carrying
    the same filler collapsed into a single identity and `library dedupe` offered to
    merge unrelated papers.

    Dropping the fallback costs a malformed-but-unique DOI its identity, so
    re-capturing that one paper can insert a second entry. Matching by title and
    URL still applies, and a spurious duplicate is recoverable in a way a
    wrongly merged pair is not.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return normalize_doi(value)


def _canonical_arxiv_id(value: object) -> str | None:
    """Canonical form of a stored arXiv ID, for identity comparison.

    An unrecognized value keeps its stripped self rather than dropping out: a
    hand-written ID this parser does not know is still the user's identifier for
    that paper, and unlike a DOI field there is no established placeholder
    convention here for it to collide with.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return normalize_arxiv_id(value) or value.strip()


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
        if not matches:
            continue
        if identity["kind"] == "url":
            # A DOI and an arXiv ID name a work; a URL names a *location*, and
            # publisher landing pages, repository indexes and shared hosts are
            # routinely one URL across many papers. Accepting it alone turned an
            # insert into an update, and the existing entry took the incoming
            # paper's title, DOI and abstract. Same reasoning `canonical_doi`
            # applies to placeholder DOIs, and the same trade-off: a missed
            # match costs a duplicate, which `library dedupe` can undo, while a
            # wrong merge cannot be undone.
            matches = [
                position
                for position in matches
                if position < len(existing_records)
                and _titles_corroborate(record, existing_records[position])
            ]
            if not matches:
                continue
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


#: German/Nordic vowels have a conventional two-letter spelling that authors and
#: publishers use interchangeably. Stripping the diacritic alone maps `Müller`
#: to `muller`, which then does not match the `Mueller` the same person is
#: published under — scored 50, reported `problematic`.
_TRANSLITERATIONS = {
    ord("ä"): "ae", ord("ö"): "oe", ord("ü"): "ue", ord("ß"): "ss",
    ord("Ä"): "Ae", ord("Ö"): "Oe", ord("Ü"): "Ue",
    ord("æ"): "ae", ord("Æ"): "Ae", ord("ø"): "oe", ord("Ø"): "Oe",
    ord("å"): "aa", ord("Å"): "Aa",
    # Latin letters with a stroke or bar. NFKD has no combining form to
    # decompose these into, so `encode("ascii", "ignore")` *deletes* them:
    # `Łukasz` became `ukasz` and `Đorđe` became `ore` — a name silently
    # missing its first letter, in a citekey and in every author comparison.
    ord("ł"): "l", ord("Ł"): "L",
    ord("đ"): "d", ord("Đ"): "D", ord("ð"): "d", ord("Ð"): "D",
    ord("þ"): "th", ord("Þ"): "Th",
    ord("ħ"): "h", ord("Ħ"): "H",
    ord("ı"): "i", ord("İ"): "I",
    ord("œ"): "oe", ord("Œ"): "Oe",
}


def _to_ascii(text: str) -> str:
    """Decode HTML entities (DBLP emits ``&apos;``/``&amp;``) then strip diacritics."""
    decoded = html.unescape(text).translate(_TRANSLITERATIONS)
    return unicodedata.normalize("NFKD", decoded).encode("ascii", "ignore").decode("ascii")


#: Lowercase particles that belong to the family name, not the given name.
#: Only absorbed when lowercase: "Van" starting a name is usually the family
#: name itself (as in "Van Rossum").
_NAME_PARTICLES = frozenset({
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du",
    "la", "le", "ten", "ter", "bin", "ibn", "al", "st", "mac", "mc",
})


def split_family_given(name: str) -> tuple[str, str]:
    """Split a personal name into (family, given), unchanged otherwise.

    Handles both ``"Family, Given"`` and ``"Given Family"`` orderings.

    Splitting only: no folding and no lowercasing, because the two callers fold
    differently on purpose. Matching wants ``ü``→``ue``; a citekey has to
    reproduce Better BibTeX, which writes ``u``. Doing it here would force one
    of them to be wrong.
    """
    stripped = name.strip()
    if "," in stripped:
        family, _, given = stripped.partition(",")
        return family.strip(), given.strip()
    parts = stripped.split()
    if not parts:
        return "", ""
    # Absorb nobiliary particles into the family name. Taking `parts[-1]` alone
    # made "Jan van der Berg" a `berg` while "van der Berg, Jan" — the same
    # person, written the other way round — was a `van der berg`, so the two
    # spellings of one author never matched and the entry scored 66 with a
    # `chimeric` flag. Citekey generation had the same split and the same bug,
    # which is why it now calls this instead of keeping its own copy.
    start = len(parts) - 1
    while start > 0 and parts[start - 1].lower() in _NAME_PARTICLES:
        start -= 1
    return " ".join(parts[start:]), " ".join(parts[:start])


def split_family_given_folded(name: str) -> tuple[str, str]:
    """:func:`split_family_given`, folded and lowercased for matching."""
    family, given = split_family_given(name)
    return _to_ascii(family).lower(), _to_ascii(given).lower()


def _normalize_author(name: str) -> str:
    family, _ = split_family_given_folded(name)
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


def _as_int_year(value: object) -> int | None:
    """Defensively coerce a year-ish value to int; callers should already have
    normalized ints, but this guards against a stray string reaching the
    ``abs(record_year - existing_year)`` comparison below."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


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
    record_year = _as_int_year(record.get("year"))

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

        existing_year = _as_int_year(existing.get("year"))
        if (
            record_year is not None
            and existing_year is not None
            and abs(record_year - existing_year) > year_window
        ):
            continue

        overlap = author_overlap(record_authors, list(existing.get("authors") or []))
        if overlap == 0 and similarity < 0.85:
            continue

        score = similarity + 0.1 * overlap
        if score > best_score:  # pragma: no branch — covered by integration/browser tests
            best_score = score
            best_key = citekey

    return best_key


class _Prepared(NamedTuple):
    """One record's comparison inputs, derived once instead of once per pair."""

    citekey: str | None
    tokens: frozenset[str]
    authors: list[str]
    year: int | None


def _prepare(records: Sequence[SimilarityCandidate]) -> list[_Prepared]:
    return [
        _Prepared(
            citekey=(
                record.get("citekey")
                if isinstance(record.get("citekey"), str) and str(record.get("citekey")).strip()
                else None
            ),
            tokens=frozenset(title_tokens(record.get("title"))),
            authors=normalize_authors(record.get("authors")),
            year=_as_int_year(record.get("year")),
        )
        for record in records
    ]


def best_fuzzy_matches(
    records: Sequence[SimilarityCandidate],
    *,
    positions: Iterable[int],
    title_threshold: float = 0.6,
    year_window: int = 2,
) -> dict[int, str]:
    """The best fuzzy match for each position in *positions*, over every other record.

    Same answers as calling :func:`compute_similarity_hint` once per position
    against the rest of the corpus — same filters, same score, same
    first-highest-wins tie-break in corpus order — but without paying for that
    shape. The naive loop rebuilt an N-element candidate list per record and
    re-tokenized every title N times, so a 22k-entry library took roughly half an
    hour of pure recomputation before printing anything.

    Three exact changes, none of which can drop a pair:

    * tokens, authors and year are derived once per record;
    * an inverted index over title tokens supplies ``|A ∩ B|`` by counting
      shared tokens, so a candidate sharing none is never visited — and Jaccard
      at or above any positive threshold *requires* a shared token;
    * a candidate whose title length cannot reach the threshold is skipped:
      ``|A ∩ B| ≤ min(|A|,|B|)`` and ``|A ∪ B| ≥ max(|A|,|B|)``, so
      ``min(|A|,|B|) ≥ threshold · max(|A|,|B|)`` is necessary.
    """
    prepared = _prepare(records)
    by_token: dict[str, list[int]] = {}
    for position, item in enumerate(prepared):
        if item.citekey is None:
            continue
        for token in item.tokens:
            by_token.setdefault(token, []).append(position)

    matches: dict[int, str] = {}
    for position in positions:
        query = prepared[position]
        if not query.tokens:
            continue
        shared: dict[int, int] = {}
        for token in query.tokens:
            for candidate in by_token.get(token, ()):
                if candidate != position:
                    shared[candidate] = shared.get(candidate, 0) + 1

        best_key: str | None = None
        best_score = 0.0
        query_size = len(query.tokens)
        # Ascending, so "first to reach the highest score" means the same record
        # the sequential scan would have picked.
        for candidate in sorted(shared):
            other = prepared[candidate]
            overlap_tokens = shared[candidate]
            other_size = len(other.tokens)
            if min(query_size, other_size) < title_threshold * max(query_size, other_size):
                continue
            similarity = overlap_tokens / (query_size + other_size - overlap_tokens)
            if similarity < title_threshold:
                continue
            if (
                query.year is not None
                and other.year is not None
                and abs(query.year - other.year) > year_window
            ):
                continue
            overlap = author_overlap(query.authors, other.authors)
            if overlap == 0 and similarity < 0.85:
                continue
            score = similarity + 0.1 * overlap
            if score > best_score:
                best_score = score
                best_key = other.citekey
        if best_key is not None:
            matches[position] = best_key
    return matches
