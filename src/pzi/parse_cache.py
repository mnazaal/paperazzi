"""Cache the read-path parse of a `.bib`, keyed by the content that produced it.

Parsing dominates every read command: on a 25k-entry / 7.2 MB library the parse
is 1.004 s of a 1.13 s read, against a 0.11 s interpreter-and-import floor. So
`pzi entries <citekey>` — a single-record lookup — cost the same as a
whole-library scan, and every command in the tool paid it.

The key is a SHA-256 of the file's bytes, not its mtime and size. That is not
caution, it is what makes the cache have no staleness class at all:
:func:`pzi.bib_repository.read_bib_file_raw_with_failures` is a pure function of
``(file bytes, path)`` — `bibtex` imports only `re`, `unicodedata` and typing,
`parse_bibtex_with_failures` is text-to-entries, and `resolve_file_field` is
string manipulation that never stats — so identical bytes at the same path give
an identical result, always. Hashing 7.2 MB costs 4 ms against a 1004 ms parse,
which is what makes exactness affordable enough that no TTL, no mtime
granularity question and no invalidation logic needs to exist.

There is no config knob, because content-hash keying leaves no correct reason to
turn it off.

JSON and not pickle: pickle is smaller (12.2 MB against 21.5 MB) and marginally
faster to load, but it executes arbitrary code on load, and this file lives in a
user-writable cache directory. The cached values are plain dicts, so JSON
round-trips them unchanged and needs no encoder.

Only the *read* path is cached. Writes parse separately through
`parse_bib_library`, which needs the bibtexparser blocks in order to re-emit
untouched entries as the bytes they were read as, and that is the mechanism the
minimal-diff guarantee rests on. A write leaves the cache stale by definition;
the next read observes a different digest and repopulates it.

Unreadable, malformed or version-mismatched cache files are misses, never
errors — as in :mod:`pzi.ledger`, losing the file costs one parse, whereas
refusing to read a library because a cache file is corrupt is worse than the
problem the cache was there to solve.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pzi.config import default_cache_home

#: Schema version. A file this code does not recognise is discarded, not
#: migrated: it is regenerable from the `.bib` in one parse.
CACHE_VERSION = 1

#: Below this source size the cache is not written at all.
#:
#: Two reasons, and the second is the load-bearing one. A sub-megabyte bib
#: parses in under ~0.15 s, so there is little to buy. More importantly the test
#: suite reads hundreds of small temporary bibs, and caching those would write a
#: file per fixture into the user's cache directory, keyed by a `/tmp` path that
#: will never be read again — the floor is what makes pruning unnecessary,
#: because everything above it is a real library whose path is stable.
MIN_CACHEABLE_BYTES = 1_000_000


def digest_for(text: str) -> str:
    """The cache key for a library's source text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_cache_dir() -> str:
    """Where cache files live: ``<xdg-cache-home>/pzi``.

    Resolved from the environment rather than injected, so the seven read sites
    keep their one-argument signatures. Tests point it elsewhere by setting
    ``XDG_CACHE_HOME``, which `config._xdg_base_dir` already honours.
    """
    return default_cache_home(os.path.expanduser("~"))


def cache_path(cache_dir: str | Path, bib_path: str) -> Path:
    """The cache file for *bib_path* — one per library, overwritten in place."""
    name = hashlib.sha256(os.path.abspath(bib_path).encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"{name}.json"


def load(cache_dir: str | Path, bib_path: str, digest: str) -> tuple[Any, list[str]] | None:
    """The cached parse for *bib_path* at *digest*, or None on any miss.

    The stored path is compared as well as the digest. The filename is only a
    truncated hash of the path, so checking it removes the collision case
    outright rather than leaving it to be argued about.
    """
    try:
        raw = cache_path(cache_dir, bib_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if (
        not isinstance(parsed, dict)
        or parsed.get("version") != CACHE_VERSION
        or parsed.get("digest") != digest
        or parsed.get("bib_path") != os.path.abspath(bib_path)
    ):
        return None
    entries = parsed.get("entries")
    records = parsed.get("records")
    failures = parsed.get("failures")
    if not isinstance(entries, list) or not isinstance(records, list):
        return None
    if not isinstance(failures, list):
        return None
    return {"entries": entries, "records": records}, failures


def store(
    cache_dir: str | Path,
    bib_path: str,
    digest: str,
    result: Any,
    failures: list[str],
    *,
    source_bytes: int,
) -> None:
    """Write the parse for *bib_path* atomically. Best-effort: failures are swallowed.

    A cache that cannot be written must not turn a working read into a failed
    one, so a full disk or a read-only cache directory costs a parse and
    nothing else.
    """
    if source_bytes < MIN_CACHEABLE_BYTES:
        return
    target = cache_path(cache_dir, bib_path)
    payload = {
        "version": CACHE_VERSION,
        "digest": digest,
        "bib_path": os.path.abspath(bib_path),
        "entries": result["entries"],
        "records": result["records"],
        "failures": failures,
    }
    tmp: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=str(target.parent), suffix=".tmp", delete=False, encoding="utf-8"
        ) as handle:
            # `write(dumps(...))`, not `json.dump(payload, handle)`: dumping
            # straight to the handle emits many small writes through the
            # buffered text layer, which measured 0.604 s against 0.093 s for
            # the encoding itself on a 25k-entry library. One encode, one write
            # is the same bytes for a sixth of the time.
            #
            # No indent and no sort either: this file is machine-read only, and
            # pretty-printing it costs more than the load it exists to speed up.
            handle.write(json.dumps(payload))
            tmp = handle.name
        os.replace(tmp, str(target))
    except OSError:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
