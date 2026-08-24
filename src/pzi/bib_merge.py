"""What a write will say: merge and write planning, decided before any I/O.

Split out of :mod:`pzi.bib_repository`, which performs the writes this module
plans. Nothing here opens a file, takes a lock, or handles a ``Path`` — the
whole module works on records and entries already in memory. That was true
before the split too, but only as a property a reader had to re-derive; here it
is a property of the import graph, and `tests/test_layer_boundaries.py` checks
it. This is the code where a bug rewrites the user's metadata with no error
raised anywhere, so it is worth making structural.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypeAlias, TypedDict, cast

from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    merge_projected_entry,
    record_to_bibtex_entry,
)
from pzi.identifiers import detect_preprint_source
from pzi.similarity import find_exact_match

WriteAction = Literal["insert", "update"]


class WritePlan(TypedDict):
    """An insert/update operation planned against a BibTeX library.

    The five core keys are always present; only ``force_new`` is optional (set
    by force-new inserts).  It is a plain ``dict`` at runtime, so dynamic
    construction / spreads still work — ``cast`` at sites that build it from a
    plain dict copy.
    """

    action: WriteAction
    index: int | None
    record: NormalizedRecord
    entry: BibtexEntry
    #: The fields **this write decided**, in `merge_entries`' record vocabulary
    #: (`tags`, `authors`, `canonical_url`, …) — *not* a description of what the
    #: file gained. The two answers differ after
    #: :func:`pzi.bib_repository._rebase_update_plan_against_current` hands a
    #: field back to a concurrent writer: this writer did decide that field,
    #: and lost it.
    changed_fields: list[str]
    force_new: NotRequired[bool]
    #: The entry ``entry`` was merged onto when this plan was built, for update
    #: plans that merged onto one. It is the *base* of the three-way merge in
    #: :func:`pzi.bib_repository._rebase_update_plan_against_current`: without
    #: it, a rebase cannot tell a field the writer deliberately set from a
    #: stale copy of a field it never touched, and silently reverts a
    #: concurrent writer's edit. Only
    #: :func:`plan_bib_write` sets it — a plan assembled by hand (promote's
    #: replace-mode preview) deliberately has no base, and rebases as before.
    base_entry: NotRequired[BibtexEntry]


# Loose record-shaped dict accepted by merge_entries (carries arbitrary keys).
MergeableEntry: TypeAlias = dict[str, Any]


class MergeDecision(TypedDict):
    """Result of :func:`merge_entries`: the merged record and what changed."""

    merged: NormalizedRecord
    changed_fields: list[str]


#: `bibtex.USER_OWNED_FIELDS` in BibTeX spelling. These are the fields a rebase
#: restores from the on-disk entry, because their absence from a plan means "the
#: writer had no opinion", never "delete it" — unlike the identity fields
#: promote's replace mode strips deliberately. `citekey` is the entry key, not a
#: field, and is validated separately.
_USER_OWNED_ENTRY_FIELDS = ("note", "keywords", "file")


def _apply_untouched_fields_from_current(
    rebased: BibtexEntry,
    *,
    base: BibtexEntry,
    planned: BibtexEntry,
    current: BibtexEntry,
) -> None:
    """Three-way merge: the current entry wins every field the plan left alone.

    *base* is the entry the plan merged onto when it was built. A field whose
    planned value still equals its base value was never decided by this writer —
    it is a copy carried along — so a concurrent writer's version of it must
    survive. A field the plan did change wins, which is what makes a deliberate
    edit (and promote's deliberate *deletions*) still apply.

    Field order is taken from the current entry first, so the result does not
    depend on set-iteration order: `PYTHONHASHSEED` varying between runs would
    otherwise reorder fields and break byte-identical rewrites.
    """
    for field in dict.fromkeys([*current["fields"], *planned["fields"], *base["fields"]]):
        planned_value = planned["fields"].get(field)
        base_value = base["fields"].get(field)
        if planned_value != base_value:
            continue  # this writer decided this field — it wins
        current_value = current["fields"].get(field)
        if current_value == base_value:
            continue  # nobody else touched it either
        if current_value is None:
            rebased["fields"].pop(field, None)  # the other writer deleted it
        else:
            rebased["fields"][field] = current_value


def _carry_unmodelled_fields(
    survivor: BibtexEntry, dropped: BibtexEntry
) -> tuple[BibtexEntry, list[str]]:
    """Copy fields only *dropped* has onto *survivor*; name the ones in conflict.

    A merge deletes one of the two blocks, and the record model cannot hold
    ``volume``/``pages``/``publisher``/``isbn``/a custom key — so everything the
    dropped entry knew that the survivor does not was silently destroyed, with
    no ``.bak`` and no way for the dry run to show it. Conflicts stay
    survivor-wins; they are reported rather than resolved.
    """
    merged_fields = dict(survivor["fields"])
    conflicts: list[str] = []
    for key, value in dropped["fields"].items():
        if key not in merged_fields:
            merged_fields[key] = value
        elif merged_fields[key] != value:
            conflicts.append(key)
    carried: BibtexEntry = {**survivor, "fields": merged_fields}  # type: ignore[typeddict-item]
    return carried, sorted(conflicts)


# ---------------------------------------------------------------------------
# Write planning
# ---------------------------------------------------------------------------

# `note` is deliberately absent: it is user-owned (`bibtex.USER_OWNED_FIELDS`),
# and listing it here meant an incoming record whose note happened to be longer
# replaced the user's prose — reachable through `pzi import`. It is filled only
# when the entry has none, below.
_PREFER_LONGER_TEXT_FIELDS = frozenset({"title", "venue", "abstract"})
_FILL_IF_MISSING_FIELDS = frozenset(
    {
        "doi",
        "arxiv_id",
        "canonical_url",
        "source_url",
        "pdf_url",
        "abstract_url",
        "local_pdf_path",
        "note",
        "source_name",
        "source_payload",
    }
)


_ITEM_TYPE_TO_ENTRY_TYPE: dict[str, str] = {
    "journalArticle": "article",
    "conferencePaper": "inproceedings",
    "book": "book",
    "bookSection": "incollection",
    "thesis": "phdthesis",
    "preprint": "unpublished",
    "webpage": "unpublished",
    "report": "techreport",
    "manuscript": "unpublished",
    "presentation": "unpublished",
    "computerProgram": "misc",
}


def resolve_entry_type(record: NormalizedRecord) -> str:
    """Determine BibTeX entry type from record metadata."""
    item_type = record.get("item_type")
    if isinstance(item_type, str) and item_type.strip():
        mapped = _ITEM_TYPE_TO_ENTRY_TYPE.get(item_type.strip())
        if mapped is not None:
            return mapped

    # A source that states its own BibTeX type (an imported .bib) outranks the
    # heuristics below, but not a provider's `item_type` above: that is fresh
    # evidence about what the work is, where this is only what some file said.
    declared = record.get("entry_type")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()

    if detect_preprint_source(record) is not None:
        return "unpublished"

    return "article"



def _reported_changed_fields(record: NormalizedRecord) -> list[str]:
    """The record keys worth showing a user as "what this write sets".

    ``item_type`` is routing information — it picks the ``@type`` — not a field
    that lands in the entry, so listing it among an insert's changed fields is
    noise that varies with which provider answered.
    """
    return sorted(key for key in record if key != "item_type")


def plan_bib_write(
    incoming_record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    entry_type: str = "article",
    force_new: bool = False,
    index: dict[tuple[Any, str], list[int]] | None = None,
    existing_entries: list[BibtexEntry] | None = None,
    source_entry: BibtexEntry | None = None,
) -> WritePlan:
    """Plan an insert or update operation for a normalized record.

    *index* is an optional prebuilt identity index (see
    :func:`pzi.similarity.build_identity_index`) over *existing_records*, reused
    to avoid rebuilding it on each call in the write path.

    *existing_entries* is the parsed entry list matching *existing_records*.
    **Pass it whenever the plan may become an update.** Both write sinks apply
    ``plan["entry"]`` verbatim, so the merge onto the on-disk entry happens here
    or not at all: a plan built without it carries a bare projection of the
    record, and applying that drops every BibTeX field the record model does not
    carry (``volume``, ``pages``, ``publisher``, ...).

    *source_entry* is the BibTeX entry the record was *read from*, when there is
    one — i.e. an import. An insert then carries the source entry with the
    projection merged onto it, so the same unmodelled fields survive the copy
    into the library. Without it, ``pzi import`` reported a clean success while
    dropping ``volume``, ``pages``, ``publisher``, ``editor``, ``series``,
    ``isbn`` and ``crossref`` (whose loss also breaks the inheritance link to
    an ``@proceedings`` entry imported alongside it).
    """
    if entry_type == "article":
        entry_type = resolve_entry_type(incoming_record)

    def _insert_entry() -> BibtexEntry:
        projection = record_to_bibtex_entry(incoming_record, entry_type=entry_type)
        if source_entry is None:
            return projection
        return merge_projected_entry(source_entry, projection)

    if force_new:
        return {
            "action": "insert",
            "index": None,
            "record": incoming_record,
            "entry": _insert_entry(),
            "changed_fields": _reported_changed_fields(incoming_record),
            "force_new": True,
        }

    match_index = find_exact_match(incoming_record, list(existing_records), index=index)
    if match_index is None:
        return {
            "action": "insert",
            "index": None,
            "record": incoming_record,
            "entry": _insert_entry(),
            "changed_fields": _reported_changed_fields(incoming_record),
        }

    existing_record = existing_records[match_index]
    merge_decision = merge_entries(
        cast(MergeableEntry, dict(existing_record)),
        cast(MergeableEntry, dict(incoming_record)),
    )
    merged_record = merge_decision["merged"]
    entry = record_to_bibtex_entry(merged_record, entry_type=entry_type)
    # Only merge when the entry list is demonstrably the same snapshot as the
    # record list — a skewed snapshot (e.g. a re-read after a concurrent edit)
    # would merge onto the wrong entry, which is worse than the field loss.
    base_entry: BibtexEntry | None = None
    if (
        existing_entries is not None
        and len(existing_entries) == len(existing_records)
        and existing_entries[match_index]["citekey"] == existing_record.get("citekey")
    ):
        base_entry = existing_entries[match_index]
        entry = merge_projected_entry(base_entry, entry)
    plan: WritePlan = {
        "action": "update",
        "index": match_index,
        "record": merged_record,
        "entry": entry,
        "changed_fields": merge_decision["changed_fields"],
    }
    if base_entry is not None:
        # Carried so a rebase can tell this plan's own edits from the copy of
        # the entry it merged onto. `entry` above is the *whole* entry, not a
        # diff, so without the base every stale field in it looks deliberate.
        plan["base_entry"] = base_entry
    return plan


def merge_entries(existing: MergeableEntry, incoming: MergeableEntry) -> MergeDecision:
    """Merge an incoming record into an existing entry conservatively."""
    merged = cast(MergeableEntry, dict(existing))
    changed_fields: list[str] = []

    existing_tags = existing.get("tags") or []
    incoming_tags = incoming.get("tags") or []
    merged_tags = sorted({*existing_tags, *incoming_tags})
    # Compared as a *set*: `merged_tags` is sorted and `existing_tags` is in the
    # user's own order, so re-adding a paper whose `keywords` are not
    # alphabetical reported `changed_fields: ["tags"]` and rewrote the field —
    # reordering keywords nobody asked to reorder, and reporting a change that
    # was only the comparison's own doing.
    if set(merged_tags) != set(existing_tags):
        merged["tags"] = merged_tags
        changed_fields.append("tags")
    elif existing.get("tags") is not None:
        merged["tags"] = existing["tags"]

    existing_authors = existing.get("authors") or []
    incoming_authors = incoming.get("authors") or []
    merged_authors = (
        incoming_authors
        if len(incoming_authors) > len(existing_authors)
        else existing_authors
    )
    if merged_authors != existing_authors:
        merged["authors"] = merged_authors
        changed_fields.append("authors")

    existing_year = existing.get("year")
    incoming_year = incoming.get("year")
    merged_year = existing_year if existing_year is not None else incoming_year
    if merged_year != existing_year:
        merged["year"] = merged_year
        changed_fields.append("year")

    for field in _PREFER_LONGER_TEXT_FIELDS:
        current_value = existing.get(field)
        incoming_value = incoming.get(field)
        merged_value = _prefer_more_informative_text(current_value, incoming_value)
        if merged_value != current_value:
            merged[field] = merged_value
            changed_fields.append(field)

    for field in _FILL_IF_MISSING_FIELDS:
        current_value = existing.get(field)
        incoming_value = incoming.get(field)
        has_current = False
        if current_value is not None:
            if isinstance(current_value, str):
                has_current = bool(current_value.strip())
            elif isinstance(current_value, list):
                has_current = bool(current_value)
            else:
                has_current = True
        merged_value = current_value if has_current else incoming_value
        if merged_value != current_value:
            merged[field] = merged_value
            changed_fields.append(field)

    return {
        "merged": cast(NormalizedRecord, merged),
        "changed_fields": sorted(set(changed_fields)),
    }


def _prefer_more_informative_text(
    existing: str | None, incoming: str | None,
) -> str | None:
    if existing is None or (isinstance(existing, str) and not existing.strip()):
        return incoming
    if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
        return existing
    return incoming if len(incoming.strip()) > len(existing.strip()) else existing
