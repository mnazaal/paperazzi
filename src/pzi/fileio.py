"""Small filesystem helpers that produce user-facing errors.

Leaf module (only stdlib + :mod:`pzi.errors`) so any layer can use it without
import cycles.  Centralizes UTF-8 text reads so that *every* user-supplied file
(a bib, an import source, a captured ``--page-html``, a ``--from-file`` list)
fails the same friendly way — naming the offending path — instead of leaking a
raw ``UnicodeDecodeError``.
"""

from __future__ import annotations

import errno
import os
import tempfile
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


def directory_folds_case(directory: str | Path) -> bool:
    """Does *directory*'s filesystem treat two spellings as one name?

    A property of the filesystem, not of the machine. macOS folds case by
    default and Linux does not, but a synced or network mount can fold beneath
    a Linux home that does not, and a case-sensitive APFS volume can sit
    beneath a macOS one that does. So this asks the directory that will hold
    the files — never ``/tmp``, which is routinely a different filesystem, and
    never ``sys.platform``.

    Answered from an existing name wherever there is one: its spelling is
    case-swapped and looked up, then confirmed with ``samefile`` so that a
    case-sensitive directory genuinely holding both spellings is not misread
    as folding. A ``.pdf`` extension is itself a cased name, so a papers
    directory decides this on its first entry, without a write.

    Only an empty directory falls through to creating a temp file. That keeps
    ``--dry-run`` over a populated library free of filesystem writes, and keeps
    the probe out of a synced folder where a create-and-delete is upload churn.

    Reported as ``False`` — case-sensitive, pzi's existing behaviour — when the
    directory is missing, unreadable, or empty and unwritable. Nothing can be
    renamed into such a directory, so the answer costs nothing.
    """
    path = Path(directory)
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                swapped = entry.name.swapcase()
                if swapped == entry.name:
                    continue
                other = path / swapped
                if not os.path.exists(other):
                    return False
                return os.path.samefile(path / entry.name, other)
    except OSError:
        return False

    try:
        handle, probe = tempfile.mkstemp(dir=str(path), prefix=".pziCase", suffix=".tmp")
    except OSError:
        return False
    os.close(handle)
    try:
        head, name = os.path.split(probe)
        return os.path.exists(os.path.join(head, name.replace(".pziCase", ".pzicase")))
    finally:
        os.unlink(probe)

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
