"""BibTeX record mappings, citekey generation, and helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Literal, TypeAlias, TypedDict

from pzi import exit_codes
from pzi.errors import PziError


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
    #: The arXiv id a promote replace took *off* this record when it
    #: became the published version. Kept as a pointer back to the preprint, and
    #: deliberately not `arxiv_id`: `has_preprint_identity` reads that one, so
    #: restoring it would re-select the entry as a promotion candidate on every
    #: future sweep — the loop promotion exists to end.
    preprint_arxiv_id: str | None
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

    # --- Bibliographic detail a journal style needs to render a citation ---
    # Modelled but *not* record-owned: see `_RECORD_FILLABLE_FIELDS`.
    volume: str | None
    number: str | None
    pages: str | None
    publisher: str | None
    issn: str | None
    isbn: str | None
    # BibTeX entry type stated by the source the record came from (an imported
    # .bib says `@inproceedings` outright).  Set only by callers that have such
    # a source; records parsed out of the library deliberately do not carry it,
    # so promotion stays free to retype an entry.
    entry_type: str

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
    fallback_number: str
    fallback_pages: str
    fallback_issn: str
    fallback_isbn: str
    fallback_pdf_url: str

    # --- Deduplication hint from fuzzy similarity ---
    similarity_hint: str | None
    # The entry this record is an *exact* identity match for, recorded only when
    # `--force-new` inserted it beside that entry anyway. Distinct from
    # `similarity_hint`, which is a fuzzy maybe: this one is a certainty, and
    # the warning for it says so.
    duplicate_of: str | None


class BibtexEntry(TypedDict):
    """A single BibTeX entry shape as consumed / produced by bibtexparser v2."""

    entry_type: str
    citekey: str
    fields: dict[str, str]


# Entry types whose venue belongs in `booktitle` rather than `journal`.
PROCEEDINGS_ENTRY_TYPES = frozenset({"inproceedings", "incollection", "conference"})


def venue_field_for_entry_type(entry_type: str) -> str:
    """Return the BibTeX field a record's ``venue`` belongs in for *entry_type*."""
    return "booktitle" if entry_type in PROCEEDINGS_ENTRY_TYPES else "journal"


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
        # A user-facing refusal, not the internal attribute path. This is
        # reachable from an entry hand-edited to `@article{,`, which parses
        # fine and only fails here, at write time.
        raise PziError(
            "refusing to write an entry with no citekey — give it a key",
            code=exit_codes.ENVIRONMENT,
        )

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
        fields[venue_field_for_entry_type(entry_type)] = venue

    doi = _empty_to_none(record.get("doi"))
    if doi is not None:
        fields["doi"] = doi

    url = _empty_to_none(record.get("canonical_url") or record.get("source_url"))
    if url is not None:
        fields["url"] = url

    local_pdf = _empty_to_none(record.get("local_pdf_path"))
    if local_pdf is not None:
        fields["file"] = escape_file_component(local_pdf)

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

    # Namespaced, so BibTeX and biber ignore it: this is a reference for the
    # reader and for pzi, not a citation field. It is *not* written as `eprint`,
    # which would round-trip back into `arxiv_id` and make a promoted entry look
    # like a preprint again.
    preprint_arxiv_id = _empty_to_none(record.get("preprint_arxiv_id"))
    if preprint_arxiv_id is not None:
        fields["pzi-preprint-arxiv-id"] = preprint_arxiv_id

    arxiv_id = _empty_to_none(record.get("arxiv_id"))
    if arxiv_id is not None:
        fields["eprint"] = arxiv_id
        fields["archiveprefix"] = "arXiv"

    for detail_key in _RECORD_FILLABLE_FIELDS:
        detail = _empty_to_none(record.get(detail_key))
        if detail is None:
            continue
        # Normalized here rather than per source, so the en-dash holds for the
        # browser extension's scraped `107-113` too — it reaches the record
        # without passing through any provider normalizer.
        fields[detail_key] = (
            normalize_page_range(detail) or detail if detail_key == "pages" else detail
        )

    return {
        "entry_type": entry_type,
        "citekey": citekey.strip(),
        "fields": fields,
    }


# Fields `record_to_bibtex_entry` emits, i.e. the ones a NormalizedRecord is
# authoritative for. `venue` and `arxiv_id` are handled separately below because
# their on-disk home depends on the existing entry. Every field NOT listed here
# belongs to whoever wrote the .bib and must survive a mutation untouched.
_RECORD_OWNED_FIELDS = (
    "title",
    "author",
    "year",
    "doi",
    "url",
    "file",
    "abstract",
    "keywords",
    "note",
    "pzi-pdf-url",
    "pzi-abstract-url",
    "pzi-preprint-arxiv-id",
)

# Fields a record may *fill* but never owns. `record_to_bibtex_entry` emits
# these, so a fresh capture finally carries the volume/issue/page range most
# journal styles need — but they stay out of `_RECORD_OWNED_FIELDS` on purpose.
# An owned key absent from a projection is *deleted* (see `merge_projected_entry`),
# and metadata providers disagree wildly about which of these they report: one
# `update` against a source that happens to omit `pages` would erase a page
# range the user, or their publisher, put there by hand. Filling a gap is always
# safe; overwriting is not, because the entry on disk is the better source
# whenever it already has an answer.
_RECORD_FILLABLE_FIELDS = (
    "volume",
    "number",
    "pages",
    "publisher",
    "issn",
    "isbn",
)


def normalize_page_range(value: object) -> str | None:
    """Render a page range with BibTeX's en-dash.

    Sources report `107-113` with a single hyphen, which TeX typesets as a
    hyphen rather than the en-dash a page range calls for.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "--" in text:
        return text
    return re.sub(r"\s*-\s*", "--", text)


def page_range_from_parts(first: object, last: object) -> str | None:
    """Join a first/last page pair, as OpenAlex reports them.

    A single-page item (`e0234`, or a last page equal to the first) is a page,
    not a range, and must not become `e0234--e0234`.
    """
    start = str(first).strip() if isinstance(first, str | int) and str(first).strip() else None
    end = str(last).strip() if isinstance(last, str | int) and str(last).strip() else None
    if start is None:
        return end
    if end is None or end == start:
        return start
    return f"{start}--{end}"


def set_detail_fields(record: NormalizedRecord, **values: object) -> None:
    """Attach the fillable bibliographic detail fields a source reported.

    Only non-empty values are set, so a source that stays silent about `pages`
    leaves the key absent rather than writing an empty one — `merge_projected_
    entry` fills gaps from what is present and must not be told a gap is filled.
    """
    for key, value in values.items():
        # ISSN/ISBN arrive as arrays; the first is the one styles print.
        if isinstance(value, list):
            value = value[0] if value else None
        if key == "pages":
            value = normalize_page_range(value)
        elif isinstance(value, int):
            value = str(value)
        if isinstance(value, str) and value.strip():
            record[key] = value.strip()  # type: ignore[literal-required]


def apply_record_to_entry(entry: BibtexEntry, record: NormalizedRecord) -> BibtexEntry:
    """Rewrite only the record-owned fields of *entry*, preserving the rest.

    ``record_to_bibtex_entry`` builds an entry from scratch out of the fields
    ``NormalizedRecord`` models, so using it to *update* an existing entry drops
    every other field the user or their publisher put there — ``volume``,
    ``pages``, ``publisher``, ``editor``, ``isbn``, ``series``, and any custom
    key. Updaters must go through here instead: a record-owned key is written
    from the record (or removed when the record cleared it), and anything else
    is left exactly as it was on disk.
    """
    return merge_projected_entry(
        entry, record_to_bibtex_entry(record, entry_type=entry["entry_type"])
    )


def merge_projected_entry(entry: BibtexEntry, projected_entry: BibtexEntry) -> BibtexEntry:
    """Merge an already-projected entry onto the entry currently on disk.

    Same contract as :func:`apply_record_to_entry`, for callers that hold a
    projection rather than the record it came from (e.g. a planned write). The
    projection is authoritative for the fields it owns; the existing entry keeps
    its type and every unmodelled field.
    """
    projected = projected_entry["fields"]
    existing = entry["fields"]
    fields = dict(existing)

    for key in _RECORD_OWNED_FIELDS:
        # The record models `year` as an int, so a year it cannot represent —
        # `2020a`, `in press`, `{\noopsort{1997}}1997` — is missing from *every*
        # projection, not because anyone cleared it. Neither removing it nor
        # overwriting it is a decision the projection is entitled to make: the
        # incoming year was derived without ever seeing the suffix, so writing
        # it destroys exactly the character distinguishing 2021a from 2021b.
        # "Absent" is a different case and stays fillable, and a year the record
        # *can* model stays writable, so `year` is not frozen in general.
        if key == "year" and _has_unmodellable_year(existing):
            continue
        if key in projected:
            fields[key] = projected[key]
        else:
            fields.pop(key, None)

    for key in _RECORD_FILLABLE_FIELDS:
        if key in projected and not (existing.get(key) or "").strip():
            fields[key] = projected[key]

    # One record key (`venue`), two possible homes. Write back to whichever the
    # entry already used: rewriting a proceedings entry's `booktitle` as
    # `journal` is bibliographically wrong and breaks styles that require it.
    # When the entry uses *neither* — the venue is a gap being filled — the entry
    # type decides, as it does for a fresh entry. Defaulting to `journal` there
    # put `journal = {NeurIPS}` on an @inproceedings.
    if "booktitle" in existing and "journal" not in existing:
        venue_key = "booktitle"
    elif "journal" in existing:
        venue_key = "journal"
    else:
        venue_key = venue_field_for_entry_type(entry["entry_type"])
    # The projection puts the venue under whichever key *its* entry type calls
    # for, so read both homes: looking only at `journal` would drop the venue of
    # every proceedings entry merged here.
    venue = projected.get("journal") or projected.get("booktitle")
    if venue is not None:
        fields[venue_key] = venue
    else:
        fields.pop(venue_key, None)

    # `eprint`/`archiveprefix` round-trip into the record only when the prefix
    # says arXiv (see `bibtex_entry_to_record`), so a bioRxiv — or prefix-less —
    # eprint was never the record's to delete.
    if "eprint" in projected:
        fields["eprint"] = projected["eprint"]
        # `.get`, not `[...]`: the two travel together out of
        # `record_to_bibtex_entry`, so indexing worked for every projection it
        # produces — but this function also accepts an already-merged entry as
        # the projection, and a bioRxiv-style bare `eprint` carries no prefix.
        # The projection stays authoritative for the pair either way.
        if "archiveprefix" in projected:
            fields["archiveprefix"] = projected["archiveprefix"]
        else:
            fields.pop("archiveprefix", None)
    elif existing.get("archiveprefix", "").strip().lower() == "arxiv":
        fields.pop("eprint", None)
        fields.pop("archiveprefix", None)

    return {
        "entry_type": entry["entry_type"],
        "citekey": projected_entry["citekey"],
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
        # Read back under its own name. Mapping this to `arxiv_id` would undo
        # the whole point: `has_preprint_identity` reads that key.
        "preprint_arxiv_id": _empty_to_none(fields.get("pzi-preprint-arxiv-id")),
        "tags": _parse_keywords(fields.get("keywords")),
        "note": _empty_to_none(fields.get("note")),
        # The *path*, not the raw field: a Zotero/JabRef composite carries a
        # description and mimetype around it, and every consumer of
        # `local_pdf_path` treats it as a filesystem path.
        "local_pdf_path": primary_pdf_path(fields.get("file")),
        "abstract": _empty_to_none(fields.get("abstract")),
        # Read back so a record that already has an answer looks non-empty to
        # `_conservative_enrich`, which fills only keys that are None/"".
        "volume": _empty_to_none(fields.get("volume")),
        "number": _empty_to_none(fields.get("number")),
        "pages": _empty_to_none(fields.get("pages")),
        "publisher": _empty_to_none(fields.get("publisher")),
        "issn": _empty_to_none(fields.get("issn")),
        "isbn": _empty_to_none(fields.get("isbn")),
    }


#: Characters a producer may backslash-escape inside a `file` component.
#: JabRef escapes `\ : ;` (FileFieldWriter.quote); Better BibTeX also escapes
#: `{ } $`. Unescaping a superset is safe — a backslash before anything else is
#: left alone, which is what Zotero's own decoder does.
_FILE_FIELD_ESCAPABLE = set("\\:;{}$")


def _split_unescaped(value: str, separator: str) -> list[str]:
    """Split on *separator*, honouring backslash escapes, keeping them in place."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == separator:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def escape_file_component(value: str) -> str:
    """Escape a path so :func:`parse_file_field` reads it back whole.

    The exact inverse of :func:`_unescape_file_component`. Only a decoder
    existed, so paths were written raw — and a citekey may legally contain `:`,
    which doubles as the PDF filename stem. `papers/smith:2024:graphs.pdf` then
    read back as Zotero's ``desc:path:mime`` and the stored path became
    ``2024``: the entry reported no PDF forever while the file sat on disk.

    A path with none of these characters is returned unchanged, so ordinary
    libraries see no rewrite.
    """
    return "".join(
        "\\" + char if char in _FILE_FIELD_ESCAPABLE else char for char in value
    )


def _unescape_file_component(value: str) -> str:
    out: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            # A backslash before anything else is literal, matching Zotero's
            # decoder — `C:\test.pdf` from a Windows JabRef must survive.
            out.append(char if char in _FILE_FIELD_ESCAPABLE else "\\" + char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            out.append(char)
    if escaped:
        out.append("\\")
    return "".join(out)


#: A trailing dot-suffix of 1-6 alphanumerics — what a complete attachment
#: path ends in. Spaces excluded on purpose: "Vol. 2" is a title fragment,
#: not an extension.
_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def parse_file_field(value: str | None) -> list[str]:
    """Attachment paths from a BibTeX ``file`` field, in order.

    pzi writes a bare path. Zotero, JabRef and Better BibTeX write a composite:
    ``description:path:mimetype``, with several attachments joined by ``;`` —
    so ``Full Text PDF:/p/x.pdf:application/pdf`` is *one* attachment, not a
    path. Reading the whole value as a path is why a Zotero-imported entry
    reported no PDF while the file sat right there, and why an incidental
    `tag add` prefixed the bib directory onto an already-absolute path.

    Only the paths are returned. The description and mimetype are deliberately
    not surfaced: `file` is a record-owned field, so anything the record cannot
    carry is deleted by `merge_projected_entry` on the next update. Preserving
    the original text is the *writer's* job — see
    `bib_serialize.merge_preserving_unchanged_source`.

    Arity follows Zotero's own rule: a component count of 1 is a bare path, 3 or
    more is ``desc:path:mime`` (a 4th is JabRef's source URL). A 2-component
    value is ambiguous — most often a path that simply contains a colon — and is
    treated as a path, which is what JabRef's parser does with ``file.pdf::``.

    A ``;`` is likewise ambiguous: Better BibTeX joins several bare paths with
    it, but a title-derived filename can *contain* one ("Metric Elicitation;
    Moving from Theory to Practice.pdf" — pzi's own `pdf_filename_format`
    produces these). The discriminator is what the split leaves behind: a BBT
    join is made of complete paths, so every fragment carries a file extension,
    while splitting a semicolon-bearing filename strands its leading fragments
    extensionless. A split in which no record is composite-shaped and some
    fragment has no extension is therefore content, not structure — the whole
    value is one path. (Zotero escapes a literal ``;`` in composites as
    a backslash-escaped ``;``, so composite values never reach this heuristic.)
    """
    if not value or not value.strip():
        return []
    records = [r for r in _split_unescaped(value.strip(), ";") if r.strip()]
    if len(records) > 1 and not any(
        len(_split_unescaped(record, ":")) >= 3 for record in records
    ):
        if any(
            not _FILE_EXTENSION_RE.search(record.strip()) for record in records
        ):
            records = [value.strip()]
    paths: list[str] = []
    for record in records:
        if not record.strip():
            continue
        parts = _split_unescaped(record, ":")
        if len(parts) >= 3:
            candidate = parts[1]
        elif len(parts) == 2:
            # `:x.pdf` and `x.pdf:` are the degenerate forms JabRef collapses to
            # a bare link; anything else with one colon is a path containing one.
            candidate = parts[1] if not parts[0].strip() else record
        else:
            candidate = parts[0]
        cleaned = _unescape_file_component(candidate).strip()
        if not cleaned:
            # `file.pdf::` — a producer wrote the separators but only filled the
            # description. JabRef collapses this to a bare link, so fall back to
            # the first component that has anything in it.
            for part in parts:
                fallback = _unescape_file_component(part).strip()
                if fallback:
                    cleaned = fallback
                    break
        if cleaned:
            paths.append(cleaned)
    return paths


def primary_pdf_path(value: str | None) -> str | None:
    """The attachment a `file` field is *about*: the first PDF, else the first."""
    paths = parse_file_field(value)
    if not paths:
        return None
    for path in paths:
        if path.lower().endswith(".pdf"):
            return path
    return paths[0]


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


def _is_modelled_year(value: str | None) -> bool:
    """Whether :func:`_parse_year` can carry *value* through the record model."""
    return _parse_year(value) is not None


def _has_unmodellable_year(fields: Mapping[str, str]) -> bool:
    """Does *fields* hold a ``year`` the record model cannot represent?

    Distinct from "has no year": an absent year is a gap a projection may fill,
    while a present-but-unmodellable one is library content the projection never
    saw and must not overwrite.
    """
    raw = fields.get("year")
    return bool(raw and raw.strip()) and not _is_modelled_year(raw)


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


#: Fields that belong to the user, never to a metadata provider. `update` and
#: `promote` both refuse to overwrite these when merging fetched metadata over
#: an existing entry. One definition because the two commands must not drift:
#: a field that is user-owned for one and provider-owned for the other means
#: the same library loses a note or a tag depending on which command touched it.
USER_OWNED_FIELDS = frozenset({"tags", "local_pdf_path", "citekey", "note"})


def changed_fields(before: Mapping[str, object], after: Mapping[str, object]) -> list[str]:
    """Field names whose value differs between *before* and *after*.

    Over the **union** of both key sets, so a field the change *removed* is
    reported. Iterating `after` alone can only ever name fields that survived,
    which is why promotion silently dropped `arxiv_id`, the arXiv DOI and the
    preprint URLs from its own report of what it had changed.
    """
    return sorted(
        key for key in set(before) | set(after) if after.get(key) != before.get(key)
    )


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
