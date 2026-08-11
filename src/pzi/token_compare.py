"""Constant-time secret comparison shared by every token check.

Its own module because it has callers on two sides of a layer boundary: the
HTTP front end compares the API bearer token, and the attach-session service
compares the per-capture attach token. Keeping a copy on each side is what
produced the defect this exists to prevent — the front end was hardened against
a non-ASCII candidate and the service, written later, called
``hmac.compare_digest`` directly and answered a 500.
"""

from __future__ import annotations

import hmac


def tokens_match(supplied: str, token: str) -> bool:
    """Constant-time comparison that a non-ASCII candidate cannot crash.

    ``hmac.compare_digest`` raises ``TypeError`` on a str containing non-ASCII,
    so one header value turned an unauthenticated request into a 500. Comparing
    the UTF-8 bytes keeps the timing property and answers what was always the
    right answer: a token that is not the token is invalid, not an error.
    """
    return hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8"))
