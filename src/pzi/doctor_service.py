"""Doctor/health services."""

from __future__ import annotations

from typing import Any, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pzi.capture_context import resolve_optional_value
from pzi.config import load_config_file
from pzi.metadata_sources import probe_s2_api

DoctorBibStatus: TypeAlias = dict[str, Any]



DoctorResult: TypeAlias = dict[str, Any]



def doctor_check(
    *,
    config_path: str,
    home_dir: str,
    translation_probe=None,
    s2_probe=None,
) -> DoctorResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {
            "status": "error",
            "config_path": config_result["path"],
            "config_ok": False,
            "config_errors": config_result["errors"],
            "bibs": [],
            "translation_server_url": None,
            "translation_server_reachable": False,
            "translation_probe_error": None,
            "credentials": {},
            "semantic_scholar": {},
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
    s2_key = resolve_optional_value(
        command=config.get("semantic_scholar_api_key_cmd"),
        fallback=config.get("semantic_scholar_api_key"),
    )
    s2_reachable = False
    s2_key_effective: bool | None = None
    s2_probe_error: str | None = None
    probe_s2 = s2_probe or probe_s2_api
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

    return {
        "status": "ok",
        "config_path": config_result["path"],
        "config_ok": True,
        "config_errors": [],
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
        },
    }


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
