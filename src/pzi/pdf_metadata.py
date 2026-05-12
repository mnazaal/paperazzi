"""Extract metadata from local PDF files using pypdf."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypeAlias

PdfExtractionResult: TypeAlias = dict[str, Any]



DOI_IN_TEXT_PATTERN = re.compile(
    r"(?i)\b(10\.\d{3,9}/[-._;()/:\w]+)\b"
)


def extract_pdf_metadata(path: str) -> PdfExtractionResult:
    """Extract DOI and title candidate from first pages of a PDF."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"doi": None, "title": None, "text_sample": None}

    file_path = Path(path)
    if not file_path.exists():
        return {"doi": None, "title": None, "text_sample": None}

    try:
        reader = PdfReader(str(file_path))
    except (OSError, ValueError):
        return {"doi": None, "title": None, "text_sample": None}

    text_pages: list[str] = []
    for page in reader.pages[:3]:
        try:
            text = page.extract_text()
            if text:
                text_pages.append(text)
        except (OSError, ValueError, AttributeError):
            continue

    full_text = "\n".join(text_pages)
    if not full_text.strip():
        return {"doi": None, "title": None, "text_sample": None}

    doi = _extract_doi_from_text(full_text)
    title = _extract_title_from_text(full_text)

    # Limit sample to first 2000 chars for downstream search
    sample = full_text[:2000].strip() or None

    return {"doi": doi, "title": title, "text_sample": sample}


def _extract_doi_from_text(text: str) -> str | None:
    """Find first DOI in extracted text."""
    match = DOI_IN_TEXT_PATTERN.search(text)
    if match is None:
        return None
    candidate = match.group(1).strip()
    candidate = re.sub(r"\s+", "", candidate)
    return candidate.lower()


def _extract_title_from_text(text: str) -> str | None:
    """Heuristic: first non-empty line that looks like a title.

    Skip common header junk (journal names, DOI lines, copyright,
    author lists, conference names).
    """
    skip_prefixes = (
        "doi:", "doi ", "http", "www.", "copyright", "©",
        "proceedings", "journal", "conference", "arxiv:",
        "received", "accepted", "published", "keywords:",
        "abstract", "introduction", "vol.", "pp.", "page",
        "fig.", "figure", "table", "issn", "isbn",
    )
    skip_patterns = (
        re.compile(r"^\s*\d+\s*$"),  # lone number
        re.compile(r"^\s*[-–—]+\s*$"),  # dashes
        re.compile(r"^\s*\*\s*$"),  # lone asterisk
    )

    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 10:
            continue
        lower = stripped.lower()
        if any(lower.startswith(p) for p in skip_prefixes):
            continue
        if any(p.match(stripped) for p in skip_patterns):
            continue
        # Heuristic: titles are typically one line, 10-200 chars
        if 10 <= len(stripped) <= 200:
            return stripped

    return None
