"""Small file-reading helpers that produce user-facing errors.

Leaf module (only stdlib + :mod:`pzi.errors`) so any layer can use it without
import cycles.  Centralizes UTF-8 text reads so that *every* user-supplied file
(a bib, an import source, a captured ``--page-html``, a ``--from-file`` list)
fails the same friendly way — naming the offending path — instead of leaking a
raw ``UnicodeDecodeError``.
"""

from __future__ import annotations

import os
from pathlib import Path

from pzi.errors import PziError


def read_text_utf8(path: str | Path) -> str:
    """Read *path* as UTF-8 text, naming the file if it is not valid UTF-8."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PziError(f"{path} is not valid UTF-8 text") from exc


def fsync_parent_dir(path: str | Path) -> None:
    """Best-effort fsync of *path*'s parent directory after an ``os.replace``.

    Fsyncing the temp file before rename (already done at each call site)
    only guarantees the file's *content* survives a crash. Without also
    fsyncing the directory, a crash right after ``os.replace`` returns can
    still lose the rename itself on some filesystems/mount options, leaving
    the old file (or nothing) in place. Not supported on all platforms
    (e.g. Windows can't open a directory for fsync), so failures are
    swallowed — this is a durability improvement, not a correctness
    dependency.
    """
    try:
        fd = os.open(str(Path(path).parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
