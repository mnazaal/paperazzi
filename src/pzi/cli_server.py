"""CLI server plan types and helpers.

Pure: resolves host/port/security from args + config.  The translation-server
lifecycle is owned by `ts_backend.backend_session`, not this module.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Literal, TypeAlias, TypedDict

from pzi.http_security import (
    DEFAULT_MAX_BODY_BYTES,
    HttpSecurityConfig,
    build_http_security_config,
    loopback_bind_host,
)


class ServerPlanError(TypedDict):
    status: Literal["error"]
    message: str


class ServerPlanOk(TypedDict):
    status: Literal["ok"]
    host: str
    port: int
    security: HttpSecurityConfig
    # Whether requests will be checked against a token. Surfaced so the operator
    # can see it at startup: a token that resolves from a different data home
    # (a differing XDG_DATA_HOME between `pzi init` and the server) comes back
    # as None, and the server would otherwise start silently unauthenticated.
    auth_enabled: bool


ServerPlan: TypeAlias = ServerPlanOk | ServerPlanError


def build_server_plan(
    *,
    host: str | None,
    port: int | None,
    config: dict[str, Any] | None,
    auth_token: str | None = None,
    allow_no_auth: bool = False,
) -> ServerPlan:
    """Resolve server host/port/security without I/O.

    ``auth_token`` is the already-resolved effective token (from
    ``api_auth_token_cmd`` or the ``api_auth_token`` plaintext fallback);
    resolving the ``_cmd`` runs a subprocess, so the caller (``commands.server``)
    does it and passes the result here to keep this function I/O-free. When not
    passed, falls back to the plaintext ``api_auth_token`` in ``config``.
    """
    if config is None and (host is None or port is None):
        return {"status": "error", "message": "failed to load config"}

    resolved_host = host
    resolved_port = port
    if config is not None:
        # `is None`, not falsiness: an explicit `--port 0` used to be swallowed
        # here and replaced by the config port, while the config-failure path
        # below let it through to an ephemeral bind — the two paths disagreed.
        # (`--port 0` is now rejected by the parser, but the guard on the next
        # line is the one that was always meant to decide this.)
        if resolved_host is None:
            resolved_host = config["api_listen_host"]
        if resolved_port is None:
            resolved_port = config["api_listen_port"]

    if resolved_host is None or resolved_port is None:
        return {"status": "error", "message": "failed to load config"}

    if _is_wildcard_bind(resolved_host):
        return {
            "status": "error",
            "message": (
                f"refusing to serve on the wildcard address {resolved_host}: the "
                "Host check that guards against DNS rebinding has no bind address "
                "to match, so every request would be rejected. Bind to a specific "
                "address (127.0.0.1 for local use, or the LAN address to share)."
            ),
        }

    if auth_token is None and config is not None:
        auth_token = config.get("api_auth_token")
    if not auth_token and not loopback_bind_host(resolved_host):
        return {
            "status": "error",
            "message": (
                "refusing to serve unauthenticated API on a non-loopback host; "
                "set api_auth_token or bind to 127.0.0.1/localhost"
            ),
        }
    if not auth_token and not allow_no_auth:
        # Even on loopback: without a token every route — search, export, the
        # PDFs, `capture`, `update` and `delete` — is available to any process
        # on the machine. This used to start anyway behind a printed warning,
        # and a warning the server then ignores is not a guard.
        #
        # The opt-out is a CLI flag rather than a config key on purpose: nothing
        # reachable from `config.toml` or from the HTTP API itself can turn
        # authentication off. Minting a token here instead was the alternative,
        # and was rejected because a silently rotated token gives an already
        # paired extension nothing but 401s and no explanation.
        return {
            "status": "error",
            "message": (
                "refusing to serve an unauthenticated API: any process on this "
                "machine could read, capture and delete entries. Run `pzi init` "
                "to write a token (your extension reads the same one), or pass "
                "--no-auth to serve without authentication deliberately."
            ),
        }

    security = build_http_security_config(
        auth_token=auth_token,
        allowed_origins=config.get("api_allowed_origins") if config is not None else None,
        max_body_bytes=config.get("api_max_body_bytes", DEFAULT_MAX_BODY_BYTES)
        if config is not None
        else DEFAULT_MAX_BODY_BYTES,
        rate_limit_rpm=config.get("rate_limit_rpm", 60) if config is not None else 60,
        listen_host=resolved_host,
    )
    return {
        "status": "ok",
        "host": resolved_host,
        "port": resolved_port,
        "security": security,
        "auth_enabled": bool(auth_token),
    }


def _is_wildcard_bind(host: str) -> bool:
    """True for addresses that bind every interface (0.0.0.0, ::, and friends)."""
    candidate = host.strip().strip("[]").lower()
    if candidate in {"*", "0.0.0.0", "::"}:
        return True
    try:
        return ipaddress.ip_address(candidate).is_unspecified
    except ValueError:
        return False
