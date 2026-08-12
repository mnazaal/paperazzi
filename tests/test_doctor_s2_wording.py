"""`doctor` and `check` disagreed about Semantic Scholar, both honestly.

`doctor` probes S2's DOI-lookup endpoint; `check` uses title search. S2
rate-limits unauthenticated callers per endpoint, so a real session showed
`semantic scholar: ok` from `doctor` and `s2: unreachable for some or all
entries` from `check` a minute apart. Both were true. The bare word "ok" is
what made it read as a contradiction, so it now says what was actually
established — and, when no key is configured, that the quota is the shared one.
"""

from pzi.cli_render import _render_doctor_result


def _render(**s2: object) -> str:
    return "\n".join(
        _render_doctor_result(
            {"semantic_scholar": {"reachable": True, "configured": "not configured", **s2}}
        )
    )


def test_unauthenticated_ok_line_says_the_quota_is_shared() -> None:
    out = _render()

    assert "semantic scholar: ok" in out
    assert "rate-limit" in out.lower()


def test_a_configured_key_does_not_get_the_shared_quota_note() -> None:
    out = _render(configured="configured")

    assert "semantic scholar: ok" in out
    assert "rate-limit" not in out.lower()


def test_an_unreachable_s2_keeps_its_own_failure_line() -> None:
    out = _render(reachable=False, probe_error="HTTP 429")

    assert "HTTP 429" in out
