"""Capture context resolution helpers for add/capture workflow."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pzi.config import AppConfig, BibConfig

DEFAULT_TOKEN_FILENAME = "api_token"


def _read_default_token_file(data_home: str | None) -> str | None:
    """Read the auto-discovered API token from ``<data_home>/api_token``.

    Returns the stripped token, or ``None`` when the file is missing/empty.
    This is what lets ``config.toml`` carry no token reference at all — the
    file location is derived from the *running* user's resolved data home, so a
    committed config leaks neither the secret nor an absolute home path.
    """
    if not data_home:
        return None
    try:
        content = (Path(data_home) / DEFAULT_TOKEN_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return None
    return content.strip() or None


def resolve_api_auth_token(
    config: Mapping[str, Any],
    *,
    run_command: Callable[[str], str] | None = None,
    resolve_secret: Callable[[str | None, str | None], str | None] | None = None,
) -> str | None:
    """Resolve the effective API auth token.

    Precedence: ``api_auth_token_cmd`` → ``api_auth_token`` plaintext →
    auto-read ``<pzi_data_home>/api_token``. The last step means a fresh
    ``pzi init`` config needs no token line while auth stays enabled.
    """
    resolver = resolve_secret or (
        lambda command, fallback: resolve_optional_value(
            command=command, fallback=fallback, run_command=run_command
        )
    )
    explicit = resolver(config.get("api_auth_token_cmd"), config.get("api_auth_token"))
    if explicit:
        return explicit
    return _read_default_token_file(config.get("pzi_data_home"))


@dataclass(frozen=True)
class CaptureContext:
    """Immutable runtime context for one capture: resolved config, bib, and the
    per-invocation knobs (secrets, browser overrides, API endpoint)."""

    config: AppConfig
    bib: BibConfig
    contact_email: str | None
    unpaywall_email: str | None
    s2_api_key: str | None
    browser_pdf_cmd: str | None
    browser: str | None
    citekey_format: str | None
    pdf_filename_format: str | None
    api_url: str
    api_auth_token: str | None
    desktop_fallback_hosts: set[str]
    pdf_discovery_parallel: bool
    ezproxy_host: str | None


def resolve_optional_value(
    *,
    command: str | None,
    fallback: str | None,
    run_command: Callable[[str], str] | None = None,
) -> str | None:
    """Resolve optional secret/config value, with command output taking priority."""
    if not command:
        return fallback
    runner = run_command or run_shell_command
    return runner(command).strip() or None


def run_shell_command(command: str) -> str:
    """Run a configured shell-style command and return stdout.

    Only simple commands (no shell operators like &&, |, ;, $(), ``) are
    accepted.  If the command string contains shell metacharacters the
    call is rejected to prevent accidental injection through config.
    """
    _reject_shell_metacharacters(command)
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("empty shell command in config")
    try:
        result = subprocess.run(tokens, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"secret command timed out after 10s (did it prompt for input?): {command!r}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"secret command exited with code {result.returncode}: "
            f"{result.stderr.strip() or '(no stderr)'}"
        )
    return result.stdout


def _reject_shell_metacharacters(command: str) -> None:
    """Raise ValueError when *command* contains dangerous shell syntax."""
    # Reject characters / patterns that a shell would interpret even
    # though we use shell=False — a config typo or injection attempt
    # should fail loudly rather than silently passing opaque args.
    dangerous = {"&&", "||", "|", ";", "$", "`", "&", "(", ")", "{", "}", "<", ">", "\n", "\r"}
    for char in dangerous:
        if char in command:
            raise ValueError(
                f"shell metacharacter {char!r} not allowed in config command: "
                f"{command!r}"
            )


def build_capture_context(
    *,
    config: AppConfig,
    bib: BibConfig,
    browser_pdf_cmd_override: str | None,
    browser: str | None,
    resolve_secret: Callable[[str | None, str | None], str | None] | None = None,
) -> CaptureContext:
    """Build runtime capture context from resolved config and bib selection."""
    resolver = resolve_secret or (
        lambda command, fallback: resolve_optional_value(
            command=command, fallback=fallback
        )
    )
    contact_email = resolver(config.get("contact_email_cmd"), config.get("contact_email"))
    unpaywall_email = resolver(
        config["unpaywall_email_cmd"], config["unpaywall_email"]
    ) or contact_email
    # Derive api_url from configured host/port.
    api_url = config.get("api_url")
    if not api_url:
        api_host = config.get("api_listen_host", "127.0.0.1")
        api_port = config.get("api_listen_port", 8765)
        api_url = f"http://{api_host}:{api_port}"
    return CaptureContext(
        config=config,
        bib=bib,
        contact_email=contact_email,
        unpaywall_email=unpaywall_email,
        s2_api_key=resolver(
            config["semantic_scholar_api_key_cmd"],
            config["semantic_scholar_api_key"],
        ),
        browser_pdf_cmd=browser_pdf_cmd_override or config.get("browser_pdf_cmd"),
        browser=browser,
        citekey_format=config.get("citekey_format"),
        pdf_filename_format=config.get("pdf_filename_format"),
        api_url=api_url,
        api_auth_token=resolve_api_auth_token(config, resolve_secret=resolver),
        desktop_fallback_hosts=set(config.get("desktop_fallback_hosts", [])),
        pdf_discovery_parallel=config.get("pdf_discovery_parallel", False),
        ezproxy_host=config.get("ezproxy_host"),
    )


# ---------------------------------------------------------------------------
# API identity helpers (merged from api_identity.py)
# ---------------------------------------------------------------------------


def resolve_contact_email(
    config: Mapping[str, Any], *, run_command: Callable[[str], str] | None = None
) -> str | None:
    return resolve_optional_value(
        command=config.get("contact_email_cmd"),
        fallback=config.get("contact_email"),
        run_command=run_command,
    )


def resolve_unpaywall_email(
    config: Mapping[str, Any], *, run_command: Callable[[str], str] | None = None
) -> str | None:
    explicit = resolve_optional_value(
        command=config.get("unpaywall_email_cmd"),
        fallback=config.get("unpaywall_email"),
        run_command=run_command,
    )
    if explicit:
        return explicit
    return resolve_contact_email(config, run_command=run_command)


def metadata_user_agent(contact_email: str | None) -> str:
    if contact_email:
        return f"pzi/1.0 (mailto:{contact_email})"
    return "pzi/1.0"
