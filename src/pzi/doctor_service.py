"""Doctor/health services."""

from __future__ import annotations

import os
import stat
from typing import Any, NotRequired, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pzi.capture_context import resolve_optional_value
from pzi.config import load_config_file
from pzi.errors import REASON_CONFIG, PziError
from pzi.metadata_sources import probe_s2_api


class DoctorBibStatus(TypedDict):
    name: str
    path: str
    path_exists: bool
    papers_dir: str
    papers_dir_exists: bool
    default: bool


class DoctorResult(TypedDict):
    status: str
    #: Health problems, one per line. The envelope's documented failure channel
    #: is `errors`, and doctor only ever populated `config_errors`, so a
    #: `--json` consumer following the contract saw an empty list on every
    #: failure — including the hard config-load one.
    errors: list[str]
    config_path: str
    config_ok: bool
    config_errors: list[str]
    config_warnings: NotRequired[list[str]]
    bibs: list[DoctorBibStatus]
    translation_server_url: str | None
    translation_server_reachable: bool
    translation_probe_error: str | None
    credentials: dict[str, str]
    semantic_scholar: dict[str, Any]
    config_permissions_warning: str | None
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
def doctor_health_problems(result: DoctorResult) -> list[str]:
    """Every reason this library is not healthy; empty means it is.

    Lives here rather than in the runner so `status` and the exit code are two
    readings of one value. They used to be computed separately, and `status` was
    hardcoded `"ok"` whenever the config merely loaded — so a missing bib, an
    unreachable translation-server or a broken key command produced
    `"status": "ok"` on a run that exited 5.
    """
    problems: list[str] = []
    if not result.get("config_ok"):
        problems.extend(result.get("config_errors") or ["config could not be loaded"])
    for bib in result.get("bibs") or []:
        if not bib.get("path_exists"):
            problems.append(f"bib file not found: {bib.get('path')}")
    if result.get("translation_server_url") and not result.get(
        "translation_server_reachable"
    ):
        problems.append(
            f"translation server unreachable at {result['translation_server_url']}"
        )
    key_error = (result.get("semantic_scholar") or {}).get("key_error")
    if key_error:
        # A configured secret command that cannot run is a config fault the user
        # must fix. An unreachable API (`probe_error`) stays advisory — that is
        # not the user's config being wrong.
        problems.append(f"semantic_scholar_api_key_cmd failed: {key_error}")
    return problems


def config_permissions_warning(config_path: str) -> str | None:
    """Warn when the config file is readable/writable beyond its owner.

    The config can carry secrets and executable ``*_cmd`` / ``browser_pdf_cmd``
    / ``page_metadata_cmd`` hooks, so group/other access is a real exposure
    (read = secret leak, write = arbitrary command execution as the user).
    Returns a recommendation string, or ``None`` when perms are fine or cannot
    be determined (e.g. on a platform without POSIX modes).
    """
    try:
        mode = stat.S_IMODE(os.stat(config_path).st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return (
            f"config file is accessible to group/other (mode {mode:#o}); "
            f"it may hold secrets and executes *_cmd hooks — run "
            f"`chmod 600 {config_path}`"
        )
    return None


def doctor_check(
    *,
    config_path: str,
    home_dir: str,
    translation_probe=None,
    s2_probe=None,
    probe_network: bool = True,
) -> DoctorResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {
            "status": "error",
            "errors": list(config_result["errors"]),
            "reason": REASON_CONFIG,
            "config_path": config_result["path"],
            "config_ok": False,
            "config_errors": config_result["errors"],
            "bibs": [],
            "translation_server_url": None,
            "translation_server_reachable": False,
            "translation_probe_error": None,
            "credentials": {},
            "semantic_scholar": {},
            "config_permissions_warning": config_permissions_warning(
                config_result["path"]
            ),
        }
    config = config_result["config"]

    bibs: list[DoctorBibStatus] = []
    from pathlib import Path as _Path

    for bib in config["bibs"]:
        bibs.append(
            {
                "name": bib["name"],
                "path": bib["path"],
                "path_exists": _Path(bib["path"]).exists(),
                "papers_dir": bib["papers_dir"],
                "papers_dir_exists": _Path(bib["papers_dir"]).exists(),
                "default": bib["default"],
            }
        )

    translation_server_url = config["translation_server_url"]
    reachable = False
    probe_error: str | None = None
    probe = translation_probe or _probe_translation_server
    try:
        reachable = bool(probe(translation_server_url))
    except OSError as exc:
        probe_error = str(exc)
        reachable = False

    # Semantic Scholar reachability.
    # A `semantic_scholar_api_key_cmd` that cannot run is a config fault, and
    # reporting config faults is this command's entire job — so record it and
    # carry on with the remaining diagnostics rather than aborting the report.
    # Kept separate from `probe_error` so "your key command is broken" stays
    # distinguishable from "the API is unreachable".
    s2_key_error: str | None = None
    try:
        s2_key = resolve_optional_value(
            command=config.get("semantic_scholar_api_key_cmd"),
            fallback=config.get("semantic_scholar_api_key"),
        )
    except PziError as exc:
        s2_key_error = exc.message
        s2_key = None
    s2_reachable = False
    s2_key_effective: bool | None = None
    s2_probe_error: str | None = None
    # `GET /health` sets `probe_network=False`. It is a liveness check for the
    # local server, it never surfaced this result — the payload carries neither
    # `reachable` nor `key_effective` — and the outbound call was measured at
    # 93 s when Semantic Scholar stalls, on an endpoint the extension's "Test
    # connection" calls with no timeout of its own. `pzi doctor` still probes:
    # there the user asked about their credentials.
    probe_s2 = s2_probe or (probe_s2_api if probe_network else None)
    if probe_s2 is not None:
        try:
            s2_reachable = bool(probe_s2(api_key=s2_key))
            if s2_reachable:
                s2_key_effective = True
            elif s2_key:
                s2_key_effective = False
            else:
                s2_key_effective = None
        except OSError as exc:
            s2_probe_error = str(exc)

    result: DoctorResult = {
        "status": "ok",
        "errors": [],
        "config_path": config_result["path"],
        "config_ok": True,
        "config_errors": [],
        #: Keys pzi does not recognize. Non-fatal, but a typo'd key silently
        #: reverts to the default — reporting them is what `doctor` is for.
        "config_warnings": list(config_result.get("warnings") or []),
        "bibs": bibs,
        "translation_server_url": translation_server_url,
        "translation_server_reachable": reachable,
        "translation_probe_error": probe_error,
        "credentials": _credential_status(config),
        "semantic_scholar": {
            "configured": _configured_status(
                cmd=config.get("semantic_scholar_api_key_cmd"),
                value=config.get("semantic_scholar_api_key"),
            ),
            "reachable": s2_reachable,
            "key_effective": s2_key_effective,
            "probe_error": s2_probe_error,
            "key_error": s2_key_error,
        },
        "config_permissions_warning": config_permissions_warning(
            config_result["path"]
        ),
    }

    # One source of truth: `status` and the runner's exit code are now two
    # readings of the same list.
    result["errors"] = doctor_health_problems(result)
    result["status"] = "ok" if not result["errors"] else "error"
    return result


def _credential_status(config: dict[str, Any]) -> dict[str, str]:
    return {
        "contact_email": _configured_status(
            cmd=config.get("contact_email_cmd"), value=config.get("contact_email")
        ),
        "unpaywall_email": _configured_status(
            cmd=config.get("unpaywall_email_cmd"), value=config.get("unpaywall_email")
        ),
        "semantic_scholar_api_key": _configured_status(
            cmd=config.get("semantic_scholar_api_key_cmd"),
            value=config.get("semantic_scholar_api_key"),
        ),
    }


def _configured_status(*, cmd: object, value: object) -> str:
    if isinstance(cmd, str) and cmd.strip():
        return "cmd"
    if isinstance(value, str) and value.strip():
        return "plaintext"
    return "not configured"


def _probe_translation_server(url: str, *, timeout: float = 2.0) -> bool:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout):
            return True
    except HTTPError:
        return True
    except URLError:
        return False
