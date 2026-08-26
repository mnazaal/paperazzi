"""Inbox service: parse, drain, and append to a persistent inbox file."""

from __future__ import annotations

import contextlib
import os
import random
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import portalocker

from pzi.add_planning import classify_capture_outcome
from pzi.bib_repository import (
    LOCK_TIMEOUT_SECONDS,
    acquire_lock_with_timeout,
)
from pzi.errors import REASON_UNAVAILABLE
from pzi.fileio import fsync_parent_dir
from pzi.tag_service import normalize_tags


@dataclass(frozen=True)
class InboxLine:
    value: str
    tags: list[str] = field(default_factory=list)
    target: str | None = None
    #: Tokens on the line the format has no meaning for, reported as a per-item
    #: warning rather than discarded.
    unrecognized: list[str] = field(default_factory=list)


class DrainItem(TypedDict):
    value: str
    status: str          # "added" | "exists" | "failed"
    citekey: str | None
    errors: list[str]
    warnings: NotRequired[list[str]]


class DrainResult(TypedDict):
    status: str          # "ok" | "error"
    inbox_file: str
    dry_run: bool
    total: int
    counts: dict[str, int]
    items: list[DrainItem]
    errors: list[str]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only when
    #: `status` is `"error"`, so `pzi.http_status.status_for_service_result`
    #: and `pzi.errors.exit_code_for_error` can classify the failure without
    #: matching on `errors[]` message text.
    reason: NotRequired[str]


# ---------------------------------------------------------------------------
# Line parsing
# ---------------------------------------------------------------------------


def parse_inbox_line(raw: str) -> InboxLine | None:
    """Parse one inbox file line into an InboxLine, or None for blank/comment.

    Format: <value> [#tag1 #tag2] [@bib-name]
    A line starting with '#' (after optional whitespace) is a comment.
    URL fragments (https://host/path#frag) are safe: the '#' is inside the
    first whitespace token, not a separate token.
    """
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        return None
    tokens = stripped.split()
    value = tokens[0]
    tags = [t[1:] for t in tokens[1:] if t.startswith("#") and len(t) > 1]
    target = next(
        (t[1:] for t in tokens[1:] if t.startswith("@") and len(t) > 1), None
    )
    # Anything else on the line is dropped, and used to be dropped silently:
    # `10.1000/x #ml #Deep Learning` loses `Learning`, and so does a second URL
    # or a typo'd `# tag`. The line still parses — refusing it would be worse —
    # but the drain now says what it ignored.
    seen_target = False
    unrecognized: list[str] = []
    for token in tokens[1:]:
        if token.startswith("#") and len(token) > 1:
            continue
        if token.startswith("@") and len(token) > 1 and not seen_target:
            seen_target = True
            continue
        unrecognized.append(token)
    return InboxLine(
        value=value, tags=tags, target=target, unrecognized=unrecognized
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


@contextmanager
def with_inbox_lock(inbox_path: Path) -> Iterator[None]:
    """Take an advisory exclusive lock scoped to an inbox file.

    A drain reads the whole file, then spends the entire processing loop
    (network calls, deliberate delays) before rewriting it — a long window in
    which an external writer (browser extension, editor) can append a new
    line. Holding this lock only around the final re-read+rewrite (not the
    whole drain) keeps that window small without blocking appenders for the
    drain's full duration.
    """
    lock_path = Path(str(inbox_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(lock_path), "a") as lock_fh:
        # Same reasoning as `with_bib_lock`: a bare `portalocker.lock` blocks in
        # the kernel forever, so a wedged holder hangs the drain with no message.
        acquire_lock_with_timeout(
            lock_fh,
            portalocker.LOCK_EX,
            bib_path=str(inbox_path),
            timeout=LOCK_TIMEOUT_SECONDS,
        )
        try:
            yield
        finally:
            portalocker.unlock(lock_fh)


def _appended_since(inbox_path: Path, snapshot_text: str) -> list[str] | None:
    """Lines appended after *snapshot_text*, or ``None`` if it was not an append.

    The inbox is append-only in practice, so a line written while the drain's
    network calls were in flight must survive the rewrite rather than being
    dropped. What identifies such a line is that the snapshot is still a *prefix*
    of the file — not that the file has more lines than before.

    Counting lines was wrong in both directions: an external edit that shortened
    the file handed back lines that were never new, and one that lengthened an
    existing line handed back the edited line while dropping the genuinely new
    one. ``None`` says the file was rewritten rather than appended to, which is
    not something this function can reconcile — see the caller.
    """
    try:
        current_text = inbox_path.read_text(encoding="utf-8")
    except OSError:
        # `None`, not `[]`. `[]` means "nothing was appended", which sends the
        # caller on to rewrite the file — from a snapshot it has just failed to
        # confirm is still current. The rewritten-underneath path right below
        # is handled carefully for exactly this reason; an unreadable file is
        # the same "cannot reconcile" answer.
        return None
    if not current_text.startswith(snapshot_text):
        return None
    return current_text[len(snapshot_text):].splitlines()


def _write_inbox_atomically(inbox_path: Path, lines: list[str]) -> None:
    """Atomically rewrite the inbox file (POSIX rename)."""
    content = "\n".join(lines) + ("\n" if lines else "")
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=str(inbox_path.parent), suffix=".tmp", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    try:
        os.replace(tmp, str(inbox_path))
        fsync_parent_dir(inbox_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


_classify = classify_capture_outcome


def drain_inbox(
    *,
    config_path: str,
    home_dir: str,
    inbox_path: str | Path,
    dry_run: bool = False,
    extra_tags: list[str] | None = None,
    delay: float = 1.0,
    add_fn: Callable[..., Any] | None = None,
) -> DrainResult:
    """Drain an inbox file into the library.

    Reads every URL/DOI from inbox_path, calls add for each, then atomically
    rewrites the file keeping only failed entries (and comments/blank lines).
    With dry_run=True the add is previewed and the file is not modified.
    """
    path = Path(inbox_path)
    inbox_file = str(path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {
            "status": "ok",
            "inbox_file": inbox_file,
            "dry_run": dry_run,
            "total": 0,
            "counts": {"added": 0, "exists": 0, "failed": 0},
            "items": [],
            "errors": [],
        }
    except OSError as exc:
        # A dependency the caller needs (readable access to the inbox file) is
        # not there right now — `REASON_UNAVAILABLE`, not an unclassified
        # error, so the HTTP route answers 503 and the CLI exits the same code
        # it always has instead of both falling back to a generic 400/error.
        return {
            "status": "error",
            "inbox_file": inbox_file,
            "dry_run": dry_run,
            "total": 0,
            "counts": {"added": 0, "exists": 0, "failed": 0},
            "items": [],
            "errors": [f"cannot read inbox file: {exc}"],
            "reason": REASON_UNAVAILABLE,
        }

    raw_lines = raw_text.splitlines()
    parsed: list[InboxLine | None] = [parse_inbox_line(line) for line in raw_lines]
    processable = [i for i, p in enumerate(parsed) if p is not None]

    if add_fn is None:
        from pzi.add_service import add_input_to_bib
        add_fn = add_input_to_bib

    total = len(processable)
    counts: dict[str, int] = {"added": 0, "exists": 0, "failed": 0}
    items: list[DrainItem] = []
    failed_indices: set[int] = set()

    for seq, raw_i in enumerate(processable):
        line = parsed[raw_i]
        assert line is not None

        if seq > 0 and delay > 0:
            time.sleep(delay + random.uniform(0, delay * 0.25))

        merged_tags = normalize_tags((extra_tags or []) + list(line.tags))
        record_overrides: dict[str, object] = {}
        if merged_tags:
            record_overrides["tags"] = merged_tags

        try:
            result = add_fn(
                config_path=config_path,
                home_dir=home_dir,
                value=line.value,
                record_overrides=record_overrides,
                bib_selector=line.target,
                dry_run=dry_run,
                force_new=False,
            )
        except Exception as exc:
            result = {
                "status": "error",
                "action": None,
                "citekey": None,
                "message": str(exc),
                "errors": [str(exc)],
                "warnings": [],
            }

        bucket = _classify(result)
        counts[bucket] += 1
        if bucket == "failed":
            failed_indices.add(raw_i)

        item: DrainItem = {
            "value": line.value,
            "status": bucket,
            "citekey": result.get("citekey"),
            "errors": list(result.get("errors") or []),
        }
        warnings = list(result.get("warnings") or [])
        if line.unrecognized:
            warnings.insert(
                0,
                "ignored unrecognized token(s): " + " ".join(line.unrecognized)
                + " (tags are #tag, target is @bib-name; neither may contain spaces)",
            )
        if warnings:
            item["warnings"] = warnings
        items.append(item)

    errors: list[str] = []
    if not dry_run:
        remaining = [
            raw_lines[i]
            for i in range(len(raw_lines))
            if parsed[i] is None or i in failed_indices
        ]
        with with_inbox_lock(path):
            appended = _appended_since(path, raw_text)
            if appended is None:
                # Someone rewrote the file rather than appending to it, so
                # `remaining` no longer describes it and writing that back would
                # destroy their edit. Leaving the drained lines in place costs a
                # re-drain, which `add` answers with `exists`; clobbering an edit
                # costs the edit.
                errors.append(
                    f"{inbox_file} was modified while draining, so the entries "
                    "just added were left in it — remove them by hand or re-run "
                    "the drain"
                )
            else:
                _write_inbox_atomically(path, remaining + appended)

    return {
        "status": "ok",
        "inbox_file": inbox_file,
        "dry_run": dry_run,
        "total": total,
        "counts": counts,
        "items": items,
        "errors": errors,
    }
