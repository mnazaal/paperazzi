"""CLI server plan types and helpers.

Pure: resolves host/port/security from args + config.  The translation-server
lifecycle is owned by `ts_backend.backend_session`, not this module.
"""

from __future__ import annotations

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


ServerPlan: TypeAlias = ServerPlanOk | ServerPlanError


def build_server_plan(
    *,
    host: str | None,
    port: int | None,
    config: dict[str, Any] | None,
    auth_token: str | None = None,
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
        resolved_host = resolved_host or config["api_listen_host"]
        resolved_port = resolved_port or config["api_listen_port"]

    if resolved_host is None or resolved_port is None:
        return {"status": "error", "message": "failed to load config"}

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
    }
