"""Which files are the user-facing documentation, in one place.

Several tests check a claim the docs make against the code that has to back it
— the exit-code table, the HTTP route count, the frozen-surface promises, the
test paths the docs cite. Each of them used to open `README.md` directly, so
when the reference half moved to `docs/reference.md` all four broke at once,
and each would have needed the same one-line edit.

The prose is expected to keep moving between these two files; which of them a
given paragraph sits in is a presentation decision and not something a
correctness test should have an opinion about. So the tests ask for "the user
docs" and get both.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every file a user is expected to read. `docs/security.md` is deliberately
#: absent: `test_http_route_inventory` names it separately, because its claim is
#: about that document specifically.
USER_DOC_PATHS: tuple[Path, ...] = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "reference.md",
)


def user_docs_text() -> str:
    """Every user-facing document, concatenated.

    Joined with newlines so a regex anchored to a line start still matches the
    first line of the second file.
    """
    return "\n".join(path.read_text(encoding="utf-8") for path in USER_DOC_PATHS)
