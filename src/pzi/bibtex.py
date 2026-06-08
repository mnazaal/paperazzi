"""BibTeX record mappings, citekey generation, and helpers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, TypeAlias

NormalizedRecord: TypeAlias = dict[str, Any]



BibtexEntry: TypeAlias = dict[str, Any]



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

    authors = record.get("authors")
    if authors:
        fields["author"] = " and ".join(
            author.strip() for author in authors if author.strip()
        )

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

    note = _build_note(record)
    if note is not None:
        fields["note"] = note

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
    note = fields.get("note")

    return {
        "citekey": entry["citekey"],
        "title": _empty_to_none(fields.get("title")),
        "authors": _parse_authors(fields.get("author")),
        "year": _parse_year(fields.get("year")),
        "venue": _empty_to_none(fields.get("journal") or fields.get("booktitle")),
        "doi": _empty_to_none(fields.get("doi")),
        "arxiv_id": _empty_to_none(arxiv_id)
        if archive_prefix == "arXiv" or arxiv_id
        else None,
        "canonical_url": _empty_to_none(fields.get("url")),
        "source_url": _empty_to_none(fields.get("url")),
        "pdf_url": extract_note_field(note, "PDF"),
        "abstract_url": extract_note_field(note, "Abstract"),
        "tags": _parse_keywords(fields.get("keywords")),
        "note": _parse_note_text(note),
        "local_pdf_path": _empty_to_none(fields.get("file")),
        "abstract": _empty_to_none(fields.get("abstract")),
    }


def extract_note_field(note: str | None, label: str) -> str | None:
    """Extract a labeled segment (e.g. PDF, Abstract) from a note field."""
    normalized = _empty_to_none(note)
    if normalized is None:
        return None
    prefix = f"{label}:"
    for part in normalized.split(" | "):
        if part.startswith(prefix):
            return part[len(prefix):].strip() or None
    return None



def _parse_keywords(value: str | None) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _build_note(record: NormalizedRecord) -> str | None:
    note = _empty_to_none(record.get("note"))
    pdf_url = _empty_to_none(record.get("pdf_url"))
    abstract_url = _empty_to_none(record.get("abstract_url"))

    segments = [
        segment
        for segment in [
            note,
            f"PDF: {pdf_url}" if pdf_url else None,
            f"Abstract: {abstract_url}" if abstract_url else None,
        ]
        if segment
    ]
    if not segments:
        return None
    return " | ".join(segments)


def _parse_note_text(value: str | None) -> str | None:
    normalized = _empty_to_none(value)
    if normalized is None:
        return None
    first = normalized.split(" | ", 1)[0]
    if first.startswith("PDF:") or first.startswith("Abstract:"):
        return None
    return first


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
    """Return the first available citekey using a numeric suffix when needed."""
    if base not in existing_keys:
        return base

    suffix = 2
    while f"{base}{suffix}" in existing_keys:
        suffix += 1
    return f"{base}{suffix}"


def generate_citekey(data: CitekeyInput, existing_keys: set[str]) -> str:
    """Generate a citekey and resolve collisions against existing keys."""
    base = generate_citekey_base(data)
    return resolve_citekey_collision(base, existing_keys)


def _author_token(authors: list[str]) -> str:
    if not authors:
        return "unknown"

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
