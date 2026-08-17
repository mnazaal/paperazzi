"""The compatibility policy names its own mechanisms — item 430.

`README.md`'s "Versioning and compatibility" section makes a promise per frozen
surface and names the test that enforces it. That is the whole reason the
promise is checkable rather than a claim, so a named mechanism that no longer
exists is worse than naming none: the reader has a reference that looks solid
and is not.

Nothing else in the README had this problem, because nothing else in it pointed
at a file as evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
SECTION = "## Versioning and compatibility"

#: Backticked paths in the policy table are the mechanisms it promises. Matched
#: rather than listed, so adding a row to the table extends this check for free
#: — a hand-copied list here would be one more thing to keep in step.
_PATH = re.compile(r"`(tests/[\w./]+)`")


def _policy_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index(SECTION)
    end = text.index("\n## ", start + len(SECTION))
    return text[start:end]


def test_the_policy_section_is_there() -> None:
    """A guard on the two tests below, which would pass vacuously without it."""
    section = _policy_section()
    assert "Breaking any of them is a major version" in section
    assert "until the next major version" in section, (
        "the deprecation lifetime is the one thing the policy has to state"
    )


def test_every_mechanism_the_policy_names_exists() -> None:
    """A policy that cites a deleted test is a promise with nothing behind it."""
    named = sorted(set(_PATH.findall(_policy_section())))

    assert len(named) >= 7, (
        f"the policy table should name one mechanism per frozen surface; found {named}"
    )
    missing = [path for path in named if not (REPO_ROOT / path).exists()]
    assert not missing, (
        f"the compatibility policy names mechanisms that do not exist: {missing}. "
        "Either the file moved — update the README — or the surface is no longer "
        "pinned, in which case the promise about it is no longer true."
    )


def test_the_retired_key_the_policy_quotes_is_the_real_one() -> None:
    """The worked example is quoted from `RETIRED_CONFIG_KEYS`, so it can drift.

    Retiring a *different* key one day and leaving `rate_limit_rpm` in the prose
    would leave the policy describing a mechanism by an example that no longer
    demonstrates it.
    """
    from pzi.config import RETIRED_CONFIG_KEYS

    section = _policy_section()
    quoted = [key for key in RETIRED_CONFIG_KEYS if f"`{key}`" in section or key in section]
    assert quoted, (
        "the policy's worked example names no key that is actually retired; "
        f"retired keys are {sorted(RETIRED_CONFIG_KEYS)}"
    )
    # And the message it quotes is the message the loader really produces.
    for key in quoted:
        opening = RETIRED_CONFIG_KEYS[key].split(":")[0]
        assert opening in section, (
            f"the policy quotes a message for {key!r} that the loader does not "
            f"produce; it says: {RETIRED_CONFIG_KEYS[key]!r}"
        )
