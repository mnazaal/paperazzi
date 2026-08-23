"""Tag and search services."""

from __future__ import annotations

import unicodedata
from typing import Literal, NotRequired, TypedDict, cast

from pzi.bib_repository import (
    find_entry_index,
    read_bib_file,
    read_bib_file_with_notices,
    update_bib_entry,
)
from pzi.bibtex import NormalizedRecord, apply_record_to_entry
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.errors import REASON_CONFIG, REASON_USAGE

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tag normalization
# ---------------------------------------------------------------------------

def normalize_tag(value: str) -> str | None:
    """Normalize a user tag into a lowercase slug, or None if it has no letters.

    Unicode-aware. The old rule was NFKD → `encode("ascii", "ignore")` → split
    on `[^a-z0-9]+`, which reduces a CJK, Cyrillic, Greek, Arabic or Hebrew tag
    to the empty string — so a tag in the user's own language was refused as
    "no valid tags supplied", with no hint that the alphabet was the problem.

    Latin accents still fold (`Café` → `cafe`): the combining marks are dropped
    after NFKD, which is what makes `cafe` and `café` the same tag rather than
    two. Characters that are not alphanumeric in *any* script — punctuation,
    emoji, symbols — remain separators, so an emoji-only tag still normalizes
    to nothing. That is a real refusal rather than an alphabet judgement, and
    `_mutate_entry_tags` now says which.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    folded = unicodedata.normalize("NFC", without_marks).casefold()
    collapsed = "".join(char if char.isalnum() else "-" for char in folded)
    return "-".join(part for part in collapsed.split("-") if part) or None


def normalize_tags(values: list[str]) -> list[str]:
    """Normalize, deduplicate, and sort tags for stable storage."""
    normalized_values = [normalize_tag(value) for value in values]
    unique_values = {value for value in normalized_values if value is not None}
    return sorted(unique_values)


def parse_tag_csv(value: str) -> list[str]:
    """Parse a comma-separated tag string using the shared normalization rules."""
    return normalize_tags(value.split(","))


class TagListResult(TypedDict):
    """Tags — what `pzi.list_tags()` returns.

    With a citekey, that entry's tags and `citekey` set; without one, every
    tag in the library and `citekey` None.
    """

    status: str
    bib_name: str | None
    citekey: str | None
    tags: list[str]
    errors: list[str]
    #: Blocks the parser dropped (a duplicate citekey, an unparseable block).
    #: `tag list` reports the tags it *could* read; without this, an entry the
    #: read silently skipped looked like an entry with no tags.
    warnings: NotRequired[list[str]]
    # Same structured failure kind as `TagChangeResult`, so `tag list` can
    # distinguish an unknown citekey (exit 3) from a bad --target (exit 5)
    # without matching on message text.
    reason: NotRequired[str]


class TagChangeResult(TypedDict):
    """One tag mutation — what `pzi.add_tags()` / `pzi.remove_tags()` return.

    `tags` is the entry's full tag list *afterwards*, not the argument, and
    `changed` is false when the mutation was a no-op (a tag already present, or
    already absent) rather than a failure.
    """

    status: str
    bib_name: str | None
    citekey: str | None
    tags: list[str]
    changed: bool
    dry_run: bool
    message: str
    errors: list[str]
    # Structured failure kind, so callers pick an exit code without matching on
    # the message text.
    reason: NotRequired[str]


def list_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str | None = None,
) -> TagListResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "reason": REASON_CONFIG,
            "bib_name": None,
            "citekey": citekey,
            "tags": [],
            "errors": resolved.errors,
        }
    _config, bib = resolved
    read_result, dropped_blocks = read_bib_file_with_notices(bib["path"])
    records = read_result["records"]

    if citekey is not None:
        matching = [r for r in records if r.get("citekey") == citekey]
        if not matching:
            return {
                "status": "error",
                "bib_name": bib["name"],
                "citekey": citekey,
                "tags": [],
                "reason": "not_found",
                "errors": [f"citekey not found: {citekey}"],
            }
        raw_tags = list(matching[0].get("tags") or [])
        return {
            "status": "ok",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": sorted({t for t in raw_tags if isinstance(t, str)}),
            "errors": [],
            "warnings": dropped_blocks,
        }

    all_tags: set[str] = set()
    for record in records:
        for tag in record.get("tags") or []:
            if isinstance(tag, str):
                all_tags.add(tag)
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": None,
        "tags": sorted(all_tags),
        "errors": [],
        "warnings": dropped_blocks,
    }


def add_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    tags: list[str],
    dry_run: bool = False,
) -> TagChangeResult:
    return _mutate_entry_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
        tags=tags,
        mode="add",
        dry_run=dry_run,
    )


def remove_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    tags: list[str],
    dry_run: bool = False,
) -> TagChangeResult:
    return _mutate_entry_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
        tags=tags,
        mode="remove",
        dry_run=dry_run,
    )


def _mutate_entry_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    tags: list[str],
    mode: Literal["add", "remove"],
    dry_run: bool,
) -> TagChangeResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "reason": REASON_CONFIG,
            "bib_name": None,
            "citekey": citekey,
            "tags": [],
            "changed": False,
            "dry_run": dry_run,
            "message": "could not resolve target bib",
            "errors": resolved.errors,
        }
    config, bib = resolved
    read_result = read_bib_file(bib["path"])
    entries = list(read_result["entries"])

    match_index = find_entry_index(entries, citekey)
    if match_index is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": [],
            "changed": False,
            "dry_run": dry_run,
            "message": "citekey not found",
            "reason": "not_found",
            "errors": [f"citekey not found: {citekey}"],
        }

    normalized_tags = normalize_tags(tags)
    if not normalized_tags:
        rejected = ", ".join(repr(tag) for tag in tags) or "(none)"
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": [],
            "changed": False,
            "dry_run": dry_run,
            "message": (
                f"no valid tags supplied: {rejected} "
                "(a tag needs at least one letter or digit; punctuation, "
                "symbols and emoji are treated as separators)"
            ),
            "reason": REASON_USAGE,
            "errors": [
                f"no valid tags supplied: {rejected} "
                "(a tag needs at least one letter or digit)"
            ],
        }

    current_record = cast(NormalizedRecord, dict(read_result["records"][match_index]))
    current_tags = list(current_record.get("tags") or [])

    def _apply(existing: list[str]) -> list[str]:
        """Add or remove *normalized_tags*, matching on normalized form.

        Both sides are normalized for the *comparison* only, as
        ``search_service`` already does: a tag stored as ``Machine Learning``
        was unreachable from any input, so it could never be removed and adding
        it again left the entry carrying both spellings.

        The stored spelling itself is kept. Rewriting 157 of a real library's
        162 tags is not something ``pzi tag add`` was asked to do; a tag pzi
        *composes* is a slug, as it always was.
        """
        requested = set(normalized_tags)
        if mode != "add":
            return sorted(tag for tag in existing if normalize_tag(tag) not in requested)
        present = {normalize_tag(tag) for tag in existing}
        return sorted([*existing, *(tag for tag in normalized_tags if tag not in present)])

    merged_sorted = _apply(current_tags)
    changed = merged_sorted != sorted(current_tags)

    if not changed:
        return {
            "status": "ok",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": merged_sorted,
            "changed": False,
            "dry_run": dry_run,
            "message": "no changes",
            "errors": [],
        }

    updated_record = cast(NormalizedRecord, dict(current_record))
    updated_record["tags"] = merged_sorted

    if not dry_run:
        file_path_style = config.get("pdf_file_path_style", "absolute")

        def _updater(entry, record):
            # Re-derive from the record the repository hands back *under the
            # lock*, not from the snapshot this run opened with. Writing the
            # pre-lock snapshot back silently reverted any edit another process
            # made in between — a different title and abstract were restored
            # while the command reported `status: ok`. `update_service`
            # already does it this way and says why.
            #
            # That includes the tag arithmetic itself: computing the new set
            # from the pre-lock tags and writing it verbatim meant two `tag add`
            # runs racing on one entry kept only the second one's tag, with the
            # loser reporting success.
            locked_record = cast(NormalizedRecord, dict(record))
            locked_record["tags"] = _apply(list(record.get("tags") or []))
            return apply_record_to_entry(entry, locked_record)

        update_result = update_bib_entry(
            bib["path"], citekey, _updater, file_path_style=file_path_style
        )
        if not update_result["found"]:
            return {
                "status": "error",
                "bib_name": bib["name"],
                "citekey": citekey,
                "tags": [],
                "changed": False,
                "dry_run": dry_run,
                "message": "citekey not found",
                "reason": "not_found",
                "errors": [f"citekey not found: {citekey}"],
            }
        # Report what was written under the lock, not what the pre-lock snapshot
        # predicted — those differ exactly when another writer got there first,
        # which is the case the caller most needs told about.
        written = update_result.get("record") or {}
        merged_sorted = list(written.get("tags") or [])

    if dry_run:
        message = f"would {'add' if mode == 'add' else 'remove'} tags"
    else:
        message = f"{'added' if mode == 'add' else 'removed'} tags"
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": citekey,
        "tags": merged_sorted,
        "changed": True,
        "dry_run": dry_run,
        "message": message,
        "errors": [],
    }
