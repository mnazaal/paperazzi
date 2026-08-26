"""Direct tests for `tokens_match` (D6).

It guards both the API bearer token and attach-session tokens, but had no
test of its own — only indirect coverage through those two callers. Its
whole reason to exist is the non-ASCII crash `hmac.compare_digest` has on a
raw `str` (see the module docstring); that case deserves a direct check, not
one inferred from a caller's HTTP status.
"""

from __future__ import annotations

from pzi.token_compare import tokens_match


def test_tokens_match_when_equal() -> None:
    assert tokens_match("secret-token", "secret-token") is True


def test_tokens_match_false_on_mismatch() -> None:
    assert tokens_match("secret-token", "other-token") is False


def test_tokens_match_false_when_one_is_empty() -> None:
    assert tokens_match("", "secret-token") is False
    assert tokens_match("secret-token", "") is False


def test_tokens_match_true_when_both_empty() -> None:
    # Not a callers' concern here (no caller should hand it an empty
    # configured token), but the function's own contract is "equal bytes
    # match" and two empty strings are equal.
    assert tokens_match("", "") is True


def test_tokens_match_handles_non_ascii_on_either_side_without_raising() -> None:
    """`hmac.compare_digest` raises `TypeError` on a non-ASCII `str`; this is
    the exact crash the module exists to prevent."""
    assert tokens_match("tökén", "tökén") is True
    assert tokens_match("tökén", "secret-token") is False
    assert tokens_match("secret-token", "tökén") is False
    # Both sides non-ASCII and different from each other.
    assert tokens_match("tökén", "tökèn") is False
