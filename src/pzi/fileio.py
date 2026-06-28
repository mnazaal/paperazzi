"""Small file-reading helpers that produce user-facing errors.

Leaf module (only stdlib + :mod:`pzi.errors`) so any layer can use it without
import cycles.  Centralizes UTF-8 text reads so that *every* user-supplied file
(a bib, an import source, a captured ``--page-html``, a ``--from-file`` list)
fails the same friendly way — naming the offending path — instead of leaking a
raw ``UnicodeDecodeError``.
"""

from __future__ import annotations

from pathlib import Path

from pzi.errors import PziError


def read_text_utf8(path: str | Path) -> str:
    """Read *path* as UTF-8 text, naming the file if it is not valid UTF-8."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PziError(f"{path} is not valid UTF-8 text") from exc
