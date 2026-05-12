"""Pure config normalization and validation helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, TypeAlias
from urllib.parse import urlsplit

BibConfig: TypeAlias = dict[str, Any]



AppConfig: TypeAlias = dict[str, Any]



def validate_bib_config(
    raw: Mapping[str, object], *, home_dir: str
) -> tuple[BibConfig | None, list[str]]:
    """Validate one bib config and derive computed defaults."""
    errors: list[str] = []

    raw_name = raw.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        errors.append("bib.name must be a non-empty string")

    raw_path = raw.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append("bib.path must be a non-empty string")

    raw_papers_dir = raw.get("papers_dir")
    if raw_papers_dir is not None and not isinstance(raw_papers_dir, str):
        errors.append("bib.papers_dir must be a string when provided")

    raw_default = raw.get("default", False)
    if not isinstance(raw_default, bool):
        errors.append("bib.default must be a boolean")

    if errors:
        return None, errors

    assert isinstance(raw_name, str)
    assert isinstance(raw_path, str)
    assert isinstance(raw_default, bool)

    path = _normalize_path(raw_path, home_dir=home_dir)
    papers_dir = (
        _normalize_path(raw_papers_dir, home_dir=home_dir)
        if isinstance(raw_papers_dir, str)
        else derive_papers_dir(path)
    )

    config: BibConfig = {
        "name": raw_name.strip(),
        "path": path,
        "papers_dir": papers_dir,
        "default": raw_default,
    }
    return config, []


def validate_app_config(
    raw: Mapping[str, object], *, home_dir: str
) -> tuple[AppConfig | None, list[str]]:
    """Validate application config into one plain normalized shape."""
    errors: list[str] = []

    raw_translation_server_url = raw.get(
        "translation_server_url", "http://127.0.0.1:1969"
    )
    if not isinstance(raw_translation_server_url, str) or not _is_http_url(
        raw_translation_server_url
    ):
        errors.append("translation_server_url must be an http or https URL")

    raw_api_listen_host = raw.get("api_listen_host", "127.0.0.1")
    if not isinstance(raw_api_listen_host, str) or not raw_api_listen_host.strip():
        errors.append("api_listen_host must be a non-empty string")

    raw_api_listen_port = raw.get("api_listen_port", 8765)
    if (
        not isinstance(raw_api_listen_port, int)
        or isinstance(raw_api_listen_port, bool)
        or not (1 <= raw_api_listen_port <= 65535)
    ):
        errors.append("api_listen_port must be an integer between 1 and 65535")

    raw_api_auth_token = raw.get("api_auth_token")
    if raw_api_auth_token is not None and not isinstance(raw_api_auth_token, str):
        errors.append("api_auth_token must be a string when provided")

    raw_api_allowed_origins = raw.get("api_allowed_origins")
    if raw_api_allowed_origins is not None and not (
        isinstance(raw_api_allowed_origins, list)
        and all(isinstance(origin, str) for origin in raw_api_allowed_origins)
    ):
        errors.append("api_allowed_origins must be a list of strings when provided")

    raw_api_max_body_bytes = raw.get("api_max_body_bytes", 5 * 1024 * 1024)
    if (
        not isinstance(raw_api_max_body_bytes, int)
        or isinstance(raw_api_max_body_bytes, bool)
        or raw_api_max_body_bytes < 0
    ):
        errors.append("api_max_body_bytes must be a non-negative integer")

    raw_bibs = raw.get("bibs")
    if not isinstance(raw_bibs, list) or not raw_bibs:
        errors.append("bibs must be a non-empty list")

    if errors:
        return None, errors

    assert isinstance(raw_translation_server_url, str)
    assert isinstance(raw_api_listen_host, str)
    assert isinstance(raw_api_listen_port, int)
    assert isinstance(raw_api_max_body_bytes, int)
    assert isinstance(raw_bibs, list)

    validated_bibs: list[BibConfig] = []
    for index, bib_value in enumerate(raw_bibs):
        if not isinstance(bib_value, Mapping):
            errors.append(f"bibs[{index}] must be a mapping")
            continue

        bib_config, bib_errors = validate_bib_config(bib_value, home_dir=home_dir)
        if bib_errors:
            errors.extend(f"bibs[{index}].{error}" for error in bib_errors)
            continue
        assert bib_config is not None
        validated_bibs.append(bib_config)

    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in (bib["name"] for bib in validated_bibs):
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    duplicate_names = sorted(duplicates)
    if duplicate_names:
        errors.extend(f"duplicate bib name: {name}" for name in duplicate_names)

    default_count = sum(1 for bib in validated_bibs if bib["default"])
    if default_count > 1:
        errors.append("at most one bib may be marked as default")

    if errors:
        return None, errors

    raw_unpaywall_email = raw.get("unpaywall_email")
    if raw_unpaywall_email is not None and not isinstance(raw_unpaywall_email, str):
        errors.append("unpaywall_email must be a string when provided")

    raw_unpaywall_email_cmd = raw.get("unpaywall_email_cmd")
    if raw_unpaywall_email_cmd is not None and not isinstance(raw_unpaywall_email_cmd, str):
        errors.append("unpaywall_email_cmd must be a string when provided")

    if errors:
        return None, errors

    def _opt_str(key: str) -> str | None:
        v = raw.get(key)
        if not isinstance(v, str):
            return None
        return v.strip() or None

    normalized_api_allowed_origins: tuple[str, ...] | None = None
    if isinstance(raw_api_allowed_origins, list):
        normalized_api_allowed_origins = tuple(
            origin.strip()
            for origin in raw_api_allowed_origins
            if isinstance(origin, str) and origin.strip()
        ) or None

    raw_s2_key = raw.get("semantic_scholar_api_key")
    if raw_s2_key is not None and not isinstance(raw_s2_key, str):
        errors.append("semantic_scholar_api_key must be a string when provided")

    raw_s2_key_cmd = raw.get("semantic_scholar_api_key_cmd")
    if raw_s2_key_cmd is not None and not isinstance(raw_s2_key_cmd, str):
        errors.append("semantic_scholar_api_key_cmd must be a string when provided")

    if errors:
        return None, errors

    flaresolverr_url = _opt_str("flaresolverr_url")
    if flaresolverr_url is not None and not _is_http_url(flaresolverr_url):
        flaresolverr_url = None

    config: AppConfig = {
        "translation_server_url": raw_translation_server_url,
        "bibs": validated_bibs,
        "api_listen_host": raw_api_listen_host.strip(),
        "api_listen_port": raw_api_listen_port,
        "api_auth_token": _opt_str("api_auth_token"),
        "api_allowed_origins": normalized_api_allowed_origins,
        "api_max_body_bytes": raw_api_max_body_bytes,
        "unpaywall_email": _opt_str("unpaywall_email"),
        "unpaywall_email_cmd": _opt_str("unpaywall_email_cmd"),
        "semantic_scholar_api_key": _opt_str("semantic_scholar_api_key"),
        "semantic_scholar_api_key_cmd": _opt_str("semantic_scholar_api_key_cmd"),
        "flaresolverr_url": flaresolverr_url,
        "browser_pdf_cmd": _opt_str("browser_pdf_cmd"),
    }
    return config, []


def derive_papers_dir(bib_path: str) -> str:
    """Return the default sibling papers directory for a bib file."""
    return os.path.join(os.path.dirname(bib_path), "papers")


def resolve_bib(bibs: list[BibConfig], selector: str | None) -> BibConfig | None:
    """Resolve a bib by explicit selector or default policy."""
    if selector is not None:
        normalized_selector = selector.strip()
        for bib in bibs:
            if bib["name"] == normalized_selector or bib["path"] == normalized_selector:
                return bib
        return None

    if len(bibs) == 1:
        return bibs[0]

    defaults = [bib for bib in bibs if bib["default"]]
    if len(defaults) == 1:
        return defaults[0]

    return None


def _normalize_path(value: str, *, home_dir: str) -> str:
    expanded = value.strip()
    if expanded == "~":
        return os.path.normpath(home_dir)
    if expanded.startswith("~/"):
        expanded = os.path.join(home_dir, expanded[2:])
    return os.path.normpath(os.path.abspath(expanded))


def _is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)
