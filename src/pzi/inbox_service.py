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

from pzi.fileio import fsync_parent_dir
from pzi.tag_service import normalize_tags


@dataclass(frozen=True)
class InboxLine:
    raw: str
    value: str
    tags: list[str] = field(default_factory=list)
    target: str | None = None


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
    return InboxLine(raw=raw, value=value, tags=tags, target=target)


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
        portalocker.lock(lock_fh, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(lock_fh)


def _reread_appended_lines(inbox_path: Path, known_line_count: int) -> list[str]:
    """Return lines appended to the inbox file after the initial drain snapshot.

    The inbox is append-only in practice, so anything beyond the line count
    the drain started with is a line written concurrently and must survive
    the rewrite rather than being silently dropped.
    """
    try:
        current_text = inbox_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return current_text.splitlines()[known_line_count:]


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


def _classify(result: dict[str, Any]) -> str:
    if result.get("status") == "error":
        return "failed"
    return "exists" if result.get("action") == "update" else "added"


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
        return {
            "status": "error",
            "inbox_file": inbox_file,
            "dry_run": dry_run,
            "total": 0,
            "counts": {"added": 0, "exists": 0, "failed": 0},
            "items": [],
            "errors": [f"cannot read inbox file: {exc}"],
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
        if warnings:
            item["warnings"] = warnings
        items.append(item)

    if not dry_run:
        remaining = [
            raw_lines[i]
            for i in range(len(raw_lines))
            if parsed[i] is None or i in failed_indices
        ]
        with with_inbox_lock(path):
            appended = _reread_appended_lines(path, len(raw_lines))
            _write_inbox_atomically(path, remaining + appended)

    return {
        "status": "ok",
        "inbox_file": inbox_file,
        "dry_run": dry_run,
        "total": total,
        "counts": counts,
        "items": items,
        "errors": [],
    }
