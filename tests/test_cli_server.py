"""Server startup planning: bind host, auth requirement, and auth visibility."""

from __future__ import annotations

from pzi.cli_server import build_server_plan

_CONFIG = {
    "api_listen_host": "127.0.0.1",
    "api_listen_port": 8765,
}


def test_loopback_bind_without_a_token_is_allowed_but_reports_auth_off() -> None:
    plan = build_server_plan(host=None, port=None, config=dict(_CONFIG), auth_token=None)

    assert plan["status"] == "ok"
    # The operator must be able to see that the API is unauthenticated. A token
    # resolved from a different XDG data home silently comes back as None, so
    # "it started fine" is not evidence that auth is on.
    assert plan["auth_enabled"] is False


def test_loopback_bind_with_a_token_reports_auth_on() -> None:
    plan = build_server_plan(
        host=None, port=None, config=dict(_CONFIG), auth_token="s3cret"
    )

    assert plan["status"] == "ok"
    assert plan["auth_enabled"] is True


def test_non_loopback_bind_without_a_token_is_refused() -> None:
    plan = build_server_plan(
        host="192.168.1.5", port=8765, config=dict(_CONFIG), auth_token=None
    )

    assert plan["status"] == "error"
    assert "non-loopback" in plan["message"]


def test_wildcard_bind_is_refused_because_no_request_can_satisfy_the_host_check() -> None:
    """0.0.0.0 accepted the bind but then rejected every real request.

    The Host check guards against DNS rebinding by requiring the request's Host
    to match the bind address; a wildcard bind has no such address, so only a
    literal `Host: 0.0.0.0` passed and the server was silently unusable. Refuse
    it up front with an actionable message instead.
    """
    plan = build_server_plan(
        host="0.0.0.0", port=8765, config=dict(_CONFIG), auth_token="s3cret"
    )

    assert plan["status"] == "error"
    assert "0.0.0.0" in plan["message"]
    assert "specific" in plan["message"] or "explicit" in plan["message"]


def test_wildcard_ipv6_bind_is_refused_too() -> None:
    plan = build_server_plan(
        host="::", port=8765, config=dict(_CONFIG), auth_token="s3cret"
    )

    assert plan["status"] == "error"
