"""Zotero-style filename templates and small Better BibTeX citekey subset."""

from __future__ import annotations

import re
import shlex
import unicodedata
from collections.abc import Mapping
from typing import Any

from pzi.bibtex import generate_citekey_base, normalize_authors, resolve_citekey_collision

_TEMPLATE_RE = re.compile(r"{{\s*:?\s*([A-Za-z][A-Za-z0-9_]*)\s*([^{}]*)}}")
_NON_CITEKEY = re.compile(r"[^a-z0-9]+")
_FILENAME_FORBIDDEN = re.compile(r"[\\/\x00-\x1f:]+")
_WHITESPACE = re.compile(r"\s+")
_STOPWORDS = frozenset({"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"})


def render_zotero_template(template: str, record: Mapping[str, Any]) -> str:
    """Render the useful Zotero 7 file-renaming template subset.

    Supports `{{ field ... }}` and `{{ :field ... }}` variables with `prefix`,
    `suffix`, `start`, `truncate`, `replaceFrom`, `replaceTo`, `regexOpts`, and
    `case` options. Unsupported variables render empty so copied Zotero
    templates degrade safely.
    """

    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        options = _parse_options(match.group(2))
        value = _template_value(variable, record)
        return _apply_options(value, options)

    return _TEMPLATE_RE.sub(replace, template).strip()


def format_pdf_filename(template: str | None, record: Mapping[str, Any]) -> str:
    """Render a safe PDF filename, appending `.pdf` when needed."""
    stem = render_zotero_template(template, record) if template else ""
    if not stem:
        citekey = record.get("citekey")
        stem = str(citekey) if citekey else generate_citekey_base(_citekey_input(record))
    stem = _sanitize_filename_stem(stem)
    if not stem:
        stem = "paper"
    # Strip any existing .pdf suffix to avoid double extension
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    return f"{stem}.pdf"


def format_citekey(
    template: str | None,
    record: Mapping[str, Any],
    existing_keys: set[str],
) -> str:
    """Render a citekey from Zotero-style or common Better BibTeX templates."""
    if template:
        if "{{" in template:
            base = render_zotero_template(template, record)
        else:
            base = _render_better_bibtex_formula(template, record)
    else:
        base = generate_citekey_base(_citekey_input(record))

    cleaned = _sanitize_citekey(base)
    if not cleaned:
        cleaned = generate_citekey_base(_citekey_input(record))
    return resolve_citekey_collision(cleaned, existing_keys)


def _parse_options(text: str) -> dict[str, str]:
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    options: dict[str, str] = {}
    for token in lexer:
        if "=" not in token:
            options[token] = "true"
            continue
        key, value = token.split("=", 1)
        options[key] = value
    return options


def _apply_options(value: str, options: Mapping[str, str]) -> str:
    if not value:
        return ""

    if "match" in options and re.search(options["match"], value) is None:
        return ""

    if "replaceFrom" in options:
        flags = re.IGNORECASE if "i" in options.get("regexOpts", "") else 0
        count = 0 if "g" in options.get("regexOpts", "") else 1
        value = re.sub(
            options["replaceFrom"],
            options.get("replaceTo", ""),
            value,
            count=count,
            flags=flags,
        )

    if "start" in options:
        try:
            value = value[int(options["start"]):]
        except ValueError:
            pass

    if "truncate" in options:
        try:
            value = value[: int(options["truncate"])]
        except ValueError:
            pass

    value = value.strip()
    case = options.get("case")
    if case in {"lower", "lowercase"}:
        value = value.lower()
    elif case in {"upper", "uppercase"}:
        value = value.upper()
    elif case in {"hyphen", "kebab"}:
        value = _WHITESPACE.sub("-", value.lower())
    elif case == "snake":
        value = _WHITESPACE.sub("_", value.lower())

    if not value:
        return ""
    return f"{options.get('prefix', '')}{value}{options.get('suffix', '')}"


def _template_value(variable: str, record: Mapping[str, Any]) -> str:
    key = variable[0].lower() + variable[1:]
    if key in {"firstCreator", "firstcreator", "auth"}:
        return _first_creator(record)
    if key in {"authors", "creators"}:
        return " and ".join(_author_family_names(record))
    if key == "year":
        year = record.get("year")
        return str(year) if year is not None else ""
    if key == "title":
        return str(record.get("title") or "")
    if key in {"citationKey", "citationkey", "citekey"}:
        return str(record.get("citekey") or "")
    if key in {"publicationTitle", "publicationtitle", "venue"}:
        return str(record.get("venue") or "")
    if key == "doi":
        return str(record.get("doi") or "")
    if key in {"itemType", "itemtype"}:
        return str(record.get("item_type") or record.get("itemType") or "")
    value = record.get(key)
    return str(value) if value is not None else ""


def _render_better_bibtex_formula(template: str, record: Mapping[str, Any]) -> str:
    parts = [part.strip() for part in template.split("+")]
    rendered: list[str] = []
    for part in parts:
        rendered.append(_render_bbt_part(part, record))
    return "".join(rendered)


def _render_bbt_part(part: str, record: Mapping[str, Any]) -> str:
    is_single_quoted = part.startswith("'") and part.endswith("'")
    is_double_quoted = part.startswith('"') and part.endswith('"')
    if is_single_quoted or is_double_quoted:
        return part[1:-1]

    lower = part.lower()
    filters = lower.split(".")
    head = filters[0]
    value = ""
    if head == "auth":
        value = _first_creator(record)
    elif head.startswith("shorttitle"):
        value = _shorttitle(record, head)
    elif head == "title":
        value = str(record.get("title") or "")
    elif head == "year":
        value = str(record.get("year") or "")
    elif head in {"doi", "venue"}:
        value = str(record.get(head) or "")
    else:
        value = str(record.get(part) or "")

    for flt in filters[1:]:
        if flt == "lower":
            value = value.lower()
        elif flt == "upper":
            value = value.upper()
        elif flt == "fold":
            value = _ascii(value)
        elif flt == "clean":
            value = _sanitize_citekey(value)
    return value


def _shorttitle(record: Mapping[str, Any], token: str) -> str:
    title = str(record.get("title") or "")
    match = re.search(r"shorttitle\((\d+)(?:\s*,\s*(\d+))?\)", token)
    n = int(match.group(1)) if match else 3
    words = [_sanitize_citekey(w) for w in title.split()]
    words = [w for w in words if w and w not in _STOPWORDS]
    return "".join(word[:n] for word in words[:1])


def _first_creator(record: Mapping[str, Any]) -> str:
    names = _author_family_names(record)
    return names[0] if names else ""


def _author_family_names(record: Mapping[str, Any]) -> list[str]:
    authors = normalize_authors(record.get("authors"))
    if not authors:
        return []
    families: list[str] = []
    _bare = re.compile(r"^[A-Z]\.$")  # skip single-initial entries like "N."
    for author in authors:
        if not isinstance(author, str) or not author.strip():
            continue
        text = author.strip()
        if _bare.match(text):
            continue
        family = text.split(",", 1)[0] if "," in text else text.split()[-1]
        families.append(family.strip())
    return families


def _citekey_input(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authors": normalize_authors(record.get("authors")),
        "title": record.get("title"),
        "year": record.get("year"),
    }


def _sanitize_citekey(value: str) -> str:
    return _NON_CITEKEY.sub("", _ascii(value).lower())


def _sanitize_filename_stem(value: str) -> str:
    cleaned = _ascii(value)
    cleaned = _FILENAME_FORBIDDEN.sub(" ", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().strip(".")
    # Truncate by encoded byte length to avoid exceeding filesystem limits
    encoded = cleaned.encode("utf-8")
    if len(encoded) > 240:
        # Decode back, dropping incomplete multi-byte sequences at the boundary
        cleaned = encoded[:240].decode("utf-8", errors="ignore")
    return cleaned


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
