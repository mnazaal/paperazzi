"""Small file-reading helpers that produce user-facing errors.

Leaf module (only stdlib + :mod:`pzi.errors`) so any layer can use it without
import cycles.  Centralizes UTF-8 text reads so that *every* user-supplied file
(a bib, an import source, a captured ``--page-html``, a ``--from-file`` list)
fails the same friendly way — naming the offending path — instead of leaking a
raw ``UnicodeDecodeError``.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

from pzi.errors import PziError


def read_text_utf8(path: str | Path) -> str:
    """Read *path* as UTF-8 text, naming the file if it is not valid UTF-8.

    Decodes as ``utf-8-sig`` so a byte-order mark is consumed rather than
    surviving as a leading ``\\ufeff`` character: a BibTeX file written by a
    Windows editor would otherwise round-trip with the BOM pushed below the
    first entry, and a ``--from-file`` list would carry an invisible character
    into its first identifier. Files without a BOM decode identically.
    """
    try:
        return Path(path).read_text(encoding="utf-8-sig")
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


def write_all(fd: int, content: bytes) -> None:
    """Write every byte of *content* to *fd*, or raise.

    ``os.write`` may write fewer bytes than it was given, and its return value
    was previously discarded — a short write installed a truncated library (a
    monkeypatched 7-byte write left the file holding ``@articl``) with the fsync
    and the atomic replace both reporting success.

    One copy: `bib_repository` and `pdf_download` each had their own, and only
    one of them carried the reasoning above.
    """
    view = memoryview(content)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:  # pragma: no cover — kernels do not do this in practice
            raise OSError(
                errno.EIO,
                f"short write: {written} of {len(view)} bytes written",
            )
        written += count
