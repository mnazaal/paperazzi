"""BibTeX record mappings, citekey generation, and helpers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal, TypeAlias, TypedDict


class NormalizedRecord(TypedDict, total=False):
    """Internal canonical representation of a bibliographic record.

    All fields are optional (total=False).  Records may carry additional
    keys beyond the typed set; callers that add ad-hoc keys should use
    ``# type: ignore[typeddict-unknown-key]`` or cast() at the insertion side.
    """

    citekey: str
    title: str | None
    authors: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    canonical_url: str | None
    source_url: str | None
    abstract_url: str | None
    abstract: str | None
    local_pdf_path: str | None
    pdf_url: str | None
    pdf_source: str
    tags: list[str]
    note: str | None
    item_type: str | None

    # --- Fallback keys from browser page metadata or user overrides ---
    fallback_title: str | None
    fallback_canonical_url: str | None
    fallback_source_url: str | None
    fallback_abstract_url: str | None
    fallback_doi: str | None
    fallback_authors: str
    fallback_year: str
    fallback_venue: str | None
    fallback_abstract: str | None
    fallback_volume: str
    fallback_issue: str
    fallback_pages: str
    fallback_issn: str
    fallback_isbn: str
    fallback_pdf_url: str

    # --- Deduplication hint from fuzzy similarity ---
    similarity_hint: str | None


class BibtexEntry(TypedDict):
    """A single BibTeX entry shape as consumed / produced by bibtexparser v2."""

    entry_type: str
    citekey: str
    fields: dict[str, str]


class ClassifiedInput(TypedDict):
    """Result of classify_input() — what kind of input, plus normalized value."""

    kind: Literal["doi", "url", "pdf_url", "local_pdf", "unknown"]
    raw: str
    normalized: str | None



def record_to_bibtex_entry(
    record: NormalizedRecord, *, entry_type: str = "article"
) -> BibtexEntry:
    """Project a normalized record into a BibTeX-like entry shape."""
    citekey = record.get("citekey")
    if not isinstance(citekey, str) or not citekey.strip():
        raise ValueError("record.citekey must be a non-empty string")

    fields: dict[str, str] = {}

    title = _empty_to_none(record.get("title"))
    if title is not None:
        fields["title"] = title

    authors = normalize_authors(record.get("authors"))
    if authors:
        fields["author"] = " and ".join(authors)

    year = record.get("year")
    if year is not None:
        fields["year"] = str(year)

    venue = _empty_to_none(record.get("venue"))
    if venue is not None:
        fields["journal"] = venue

    doi = _empty_to_none(record.get("doi"))
    if doi is not None:
        fields["doi"] = doi

    url = _empty_to_none(record.get("canonical_url") or record.get("source_url"))
    if url is not None:
        fields["url"] = url

    local_pdf = _empty_to_none(record.get("local_pdf_path"))
    if local_pdf is not None:
        fields["file"] = local_pdf

    abstract = _normalize_abstract_text(record.get("abstract"))
    if abstract is not None:
        fields["abstract"] = abstract

    tags = record.get("tags")
    if tags:
        fields["keywords"] = ", ".join(tags)

    note = _empty_to_none(record.get("note"))
    if note is not None:
        fields["note"] = note

    pdf_url = _empty_to_none(record.get("pdf_url"))
    if pdf_url is not None:
        fields["pzi-pdf-url"] = pdf_url

    abstract_url = _empty_to_none(record.get("abstract_url"))
    if abstract_url is not None:
        fields["pzi-abstract-url"] = abstract_url

    arxiv_id = _empty_to_none(record.get("arxiv_id"))
    if arxiv_id is not None:
        fields["eprint"] = arxiv_id
        fields["archiveprefix"] = "arXiv"

    return {
        "entry_type": entry_type,
        "citekey": citekey.strip(),
        "fields": fields,
    }


def bibtex_entry_to_record(entry: BibtexEntry) -> NormalizedRecord:
    """Project a BibTeX-like entry into the normalized internal record shape."""
    fields = entry["fields"]
    arxiv_id = fields.get("eprint")
    archive_prefix = fields.get("archiveprefix")

    return {
        "citekey": entry["citekey"],
        "title": _empty_to_none(fields.get("title")),
        "authors": _parse_authors(fields.get("author")),
        "year": _parse_year(fields.get("year")),
        "venue": _empty_to_none(fields.get("journal") or fields.get("booktitle")),
        "doi": _empty_to_none(fields.get("doi")),
        "arxiv_id": _empty_to_none(arxiv_id)
        if isinstance(archive_prefix, str) and archive_prefix.strip().lower() == "arxiv"
        else None,
        "canonical_url": _empty_to_none(fields.get("url")),
        "source_url": _empty_to_none(fields.get("url")),
        "pdf_url": _empty_to_none(fields.get("pzi-pdf-url")),
        "abstract_url": _empty_to_none(fields.get("pzi-abstract-url")),
        "tags": _parse_keywords(fields.get("keywords")),
        "note": _empty_to_none(fields.get("note")),
        "local_pdf_path": _empty_to_none(fields.get("file")),
        "abstract": _empty_to_none(fields.get("abstract")),
    }


def _parse_keywords(value: str | None) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_abstract_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    abstract = _empty_to_none(value)
    if abstract is None:
        return None
    return re.sub(r"^\s*abstract\s*\n+", "", abstract, count=1, flags=re.IGNORECASE).strip()


def _parse_authors(value: str | None) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in value.split(" and ") if part.strip()]


def _parse_year(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    return int(stripped) if stripped.isdigit() else None


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# ---------------------------------------------------------------------------
# Citekey generation
# ---------------------------------------------------------------------------

CitekeyInput: TypeAlias = dict[str, Any]

_NON_ALNUM_CITEKEY = re.compile(r"[^a-z0-9]+")
# Pattern for bare initials like "N." that Zotero IEEE translator
# sometimes emits as separate author entries instead of full names.
# Requires period to avoid matching single characters from strings
# accidentally fed through list() (e.g. list("N. E. Poborchaya")).
_BARE_INITIAL = re.compile(r"^[A-Z]\.$")


def normalize_authors(value: object) -> list[str]:
    """Return a list of author strings from various input formats.

    ``None``          → ``[]``
    ``list[str]``     → kept as-is (already correct)
    ``str``           → split by ``" and "`` separator
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(a) for a in value if a]
    if isinstance(value, str):
        parts = re.split(r"\s+and\s+", value)
        if len(parts) > 1:
            return [p.strip() for p in parts if p.strip()]
        return [value.strip()] if value.strip() else []
    return []


def repair_split_initials(
    authors: list[str] | None,
) -> list[str]:
    """Rejoin split-initial author entries from translators like Zotero/IEEE.

    ``["N.", "E.", "Poborchaya", "E.", "O.", "Lobova"]``
    → ``["N. E. Poborchaya", "E. O. Lobova"]``

    Passes through already-correct author lists unchanged.
    """
    if not authors:
        return authors if authors is not None else []

    _bare = re.compile(r"^[A-Z]\.$")
    repaired: list[str] = []
    buffer: list[str] = []

    for author in authors:
        text = str(author).strip()
        if not text:
            continue
        if _bare.match(text):
            buffer.append(text)
        else:
            if buffer:
                repaired.append(" ".join(buffer + [text]))
                buffer = []
            else:
                repaired.append(text)

    if buffer:
        repaired.extend(buffer)

    return repaired
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
    }
)


def generate_citekey_base(data: CitekeyInput) -> str:
    """Generate a deterministic citekey base from author, year, and title."""
    author_part = _author_token(data["authors"])
    year_part = _year_token(data["year"])
    title_part = _title_token(data["title"])
    return f"{author_part}{year_part}{title_part}"


def resolve_citekey_collision(base: str, existing_keys: set[str]) -> str:
    """Return the first available citekey using a numeric suffix when needed.

    Suffixes use a hyphen separator: ``smith2024graph-2``, ``smith2024graph-3``.
    """
    if base not in existing_keys:
        return base

    suffix = 2
    while f"{base}-{suffix}" in existing_keys:
        suffix += 1
    return f"{base}-{suffix}"


def generate_citekey(data: CitekeyInput, existing_keys: set[str]) -> str:
    """Generate a citekey and resolve collisions against existing keys."""
    base = generate_citekey_base(data)
    return resolve_citekey_collision(base, existing_keys)


def _author_token(authors: list[str]) -> str:
    """Extract a citekey-author token from the authors list.

    Skips bare-initial entries (e.g. ``"N."``, ``"E"``) that some web
    translators emit as separate list elements — picks the first
    entry that looks like a real name.
    """
    if not authors:
        return "unknown"

    for author_raw in authors:
        author = author_raw.strip()
        if not author:
            continue
        if _BARE_INITIAL.match(author):
            continue
        if "," in author:
            family_name = author.split(",", 1)[0]
        else:
            parts = author.split()
            family_name = parts[-1] if parts else author
        token = _slug_token(family_name)
        if token:
            return token

    # Fallback: all entries are bare-initials or empty — use first.
    first_author = authors[0].strip()
    if not first_author:
        return "unknown"
    if "," in first_author:
        family_name = first_author.split(",", 1)[0]
    else:
        family_name = first_author.split()[-1]
    token = _slug_token(family_name)
    return token or "unknown"


def _year_token(year: int | None) -> str:
    if year is None:
        return "xxxx"
    return str(year)


def _title_token(title: str | None) -> str:
    if title is None:
        return "untitled"

    words = [_slug_token(part) for part in title.split()]
    meaningful_words = [word for word in words if word and word not in _STOPWORDS]
    if not meaningful_words:
        return "untitled"
    return meaningful_words[0]


def _slug_token(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    lowered = ascii_value.lower().strip()
    collapsed = _NON_ALNUM_CITEKEY.sub("", lowered)
    return collapsed
