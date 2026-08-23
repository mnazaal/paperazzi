"""`_source_diff` must describe a change without reading the whole library.

`difflib.unified_diff` over two 130k-line files was **59% of a
`preview_batch_write`** on a 22,232-entry library — 6.5 s of 11.6 s. It is
dominant *because* untouched entries are now written back byte-identically
(item 566), so the two texts differ only around the entry that changed and
difflib is rediscovering that across the whole file.

Trimming the common prefix and suffix before calling difflib is 241x faster.
The risk in doing so is the `@@` hunk headers: they carry real file line
numbers, and a trimmed call renumbers them from the start of the slice. A diff
that lies about *where* a change is would be worse than a slow one.

So these tests are a differential against the untrimmed implementation, compared
**byte for byte, headers included**.
"""

from __future__ import annotations

import difflib

import pytest

from pzi.bib_repository import _source_diff


def _untrimmed(old: str, new: str, path: str) -> str:
    """`_source_diff` as it was: difflib over both whole files."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
        )
    )


def _library(n: int) -> str:
    return "\n".join(
        f"@article{{key{i:05d},\n  title = {{Thing {i}}},\n  year = {{2020}}\n}}\n"
        for i in range(n)
    )


_BIG = _library(400)
_LINES = _BIG.splitlines(keepends=True)


def _replace_line(text: str, index: int, replacement: str) -> str:
    lines = text.splitlines(keepends=True)
    lines[index] = replacement
    return "".join(lines)


_CASES: dict[str, tuple[str, str]] = {
    "no change at all": (_BIG, _BIG),
    "edit in the middle": (_BIG, _replace_line(_BIG, len(_LINES) // 2, "  title = {Edited}\n")),
    "edit on the very first line": (_BIG, _replace_line(_BIG, 0, "@article{renamed,\n")),
    "edit on the very last line": (_BIG, _replace_line(_BIG, len(_LINES) - 1, "}\n")),
    "insertion in the middle": (
        _BIG,
        "".join(_LINES[:200] + ["  keywords = {added}\n"] + _LINES[200:]),
    ),
    "deletion in the middle": (_BIG, "".join(_LINES[:200] + _LINES[204:])),
    "two separate edits far apart": (
        _BIG,
        _replace_line(
            _replace_line(_BIG, 5, "  title = {First}\n"),
            len(_LINES) - 6,
            "  title = {Second}\n",
        ),
    ),
    "empty to empty": ("", ""),
    "empty to one entry": ("", "@article{a,\n  title = {A}\n}\n"),
    "one entry to empty": ("@article{a,\n  title = {A}\n}\n", ""),
    "single line file": ("one line\n", "another line\n"),
    "trailing newline added": ("@article{a}\n", "@article{a}\n\n"),
    "no trailing newline at all": ("@article{a}", "@article{b}"),
    "whole file replaced": (_BIG, _library(50)),
    "file grows a lot": (_library(10), _BIG),
}


@pytest.mark.parametrize("name", list(_CASES), ids=list(_CASES))
def test_the_trimmed_diff_is_byte_identical_to_the_untrimmed_one(name: str) -> None:
    """Including the `@@` headers, which carry real file line numbers."""
    old, new = _CASES[name]
    assert _source_diff(old, new, "/lib.bib") == _untrimmed(old, new, "/lib.bib")


def test_hunk_headers_name_the_real_line_numbers() -> None:
    """The specific failure trimming invites: a diff that lies about where.

    An edit far down the file must report its position in the *file*, not its
    offset within whatever slice the implementation chose to diff.
    """
    edited = _replace_line(_BIG, 900, "  title = {Far Down}\n")
    diff = _source_diff(_BIG, edited, "/lib.bib")

    headers = [line for line in diff.splitlines() if line.startswith("@@")]
    assert len(headers) == 1, diff
    # Line 900 (0-indexed) is line 901 to a human; unified_diff opens the hunk
    # three lines of context earlier.
    assert "-898" in headers[0], headers[0]


def test_an_unchanged_file_produces_no_diff() -> None:
    assert _source_diff(_BIG, _BIG, "/lib.bib") == ""
