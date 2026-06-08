"""Config types, validation, TOML loading, and TOML serialization."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
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


def _opt_str_from_raw(raw: Mapping[str, object], key: str) -> str | None:
    """Return stripped non-empty string from a raw config mapping, or None."""
    v = raw.get(key)
    if not isinstance(v, str):
        return None
    return v.strip() or None


def _validate_bib_list(
    raw_bibs: object, *, home_dir: str
) -> tuple[list[BibConfig] | None, list[str]]:
    """Validate every bib entry, check for duplicate names and multiple defaults."""
    if not isinstance(raw_bibs, list):
        return None, ["bibs must be a list"]

    errors: list[str] = []
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
    return validated_bibs, []


def _normalize_app_config(raw: Mapping[str, object], validated_bibs: list[BibConfig]) -> AppConfig:
    """Build a normalized AppConfig from already-validated fields.

    Pure normalization — no validation.  Callers must pre-validate.
    """
    raw_api_allowed_origins = raw.get("api_allowed_origins")
    normalized_api_allowed_origins: tuple[str, ...] | None = None
    if isinstance(raw_api_allowed_origins, list):
        normalized_api_allowed_origins = tuple(
            origin.strip()
            for origin in raw_api_allowed_origins
            if isinstance(origin, str) and origin.strip()
        ) or None

    opt = lambda k: _opt_str_from_raw(raw, k)  # noqa: E731
    flaresolverr_url = opt("flaresolverr_url")
    if flaresolverr_url is not None and not _is_http_url(flaresolverr_url):
        flaresolverr_url = None

    raw_translation_server_url = raw.get(
        "translation_server_url", "http://127.0.0.1:1969"
    )
    raw_api_listen_host = raw.get("api_listen_host", "127.0.0.1")
    raw_api_listen_port = raw.get("api_listen_port", 8765)
    raw_api_max_body_bytes = raw.get("api_max_body_bytes", 64 * 1024 * 1024)
    raw_metadata_confidence_min_score = raw.get("metadata_confidence_min_score", 0)
    raw_promote_confidence_threshold = raw.get("promote_confidence_threshold", 3)
    raw_browser_hook = raw.get("browser_hook", True)

    return {
        "translation_server_url": str(raw_translation_server_url),
        "bibs": validated_bibs,
        "api_listen_host": str(raw_api_listen_host).strip(),
        "api_listen_port": int(raw_api_listen_port),  # type: ignore[arg-type]
        "api_auth_token": opt("api_auth_token"),
        "api_allowed_origins": normalized_api_allowed_origins,
        "api_max_body_bytes": int(raw_api_max_body_bytes),  # type: ignore[arg-type]
        "contact_email": opt("contact_email"),
        "contact_email_cmd": opt("contact_email_cmd"),
        "unpaywall_email": opt("unpaywall_email"),
        "unpaywall_email_cmd": opt("unpaywall_email_cmd"),
        "semantic_scholar_api_key": opt("semantic_scholar_api_key"),
        "semantic_scholar_api_key_cmd": opt("semantic_scholar_api_key_cmd"),
        "flaresolverr_url": flaresolverr_url,
        "browser_pdf_cmd": opt("browser_pdf_cmd"),
        "citekey_format": opt("citekey_format"),
        "pdf_filename_format": opt("pdf_filename_format"),
        "metadata_confidence_min_score": int(raw_metadata_confidence_min_score),  # type: ignore[arg-type]
        "promote_confidence_threshold": int(raw_promote_confidence_threshold),  # type: ignore[arg-type]
        "browser_hook": bool(raw_browser_hook),
    }


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

    raw_api_max_body_bytes = raw.get("api_max_body_bytes", 64 * 1024 * 1024)
    if (
        not isinstance(raw_api_max_body_bytes, int)
        or isinstance(raw_api_max_body_bytes, bool)
        or raw_api_max_body_bytes < 0
    ):
        errors.append("api_max_body_bytes must be a non-negative integer")

    raw_metadata_confidence_min_score = raw.get("metadata_confidence_min_score", 0)
    if not isinstance(raw_metadata_confidence_min_score, int) or isinstance(
        raw_metadata_confidence_min_score, bool
    ):
        errors.append("metadata_confidence_min_score must be an integer")

    raw_promote_confidence_threshold = raw.get("promote_confidence_threshold", 3)
    if (
        not isinstance(raw_promote_confidence_threshold, int)
        or isinstance(raw_promote_confidence_threshold, bool)
        or raw_promote_confidence_threshold < 0
    ):
        errors.append("promote_confidence_threshold must be a non-negative integer")

    raw_bibs = raw.get("bibs")
    if not isinstance(raw_bibs, list) or not raw_bibs:
        errors.append("bibs must be a non-empty list")

    if errors:
        return None, errors

    validated_bibs, bib_errors = _validate_bib_list(raw_bibs, home_dir=home_dir)
    if bib_errors:
        return None, bib_errors
    assert validated_bibs is not None

    raw_unpaywall_email = raw.get("unpaywall_email")
    if raw_unpaywall_email is not None and not isinstance(raw_unpaywall_email, str):
        errors.append("unpaywall_email must be a string when provided")

    raw_unpaywall_email_cmd = raw.get("unpaywall_email_cmd")
    if raw_unpaywall_email_cmd is not None and not isinstance(raw_unpaywall_email_cmd, str):
        errors.append("unpaywall_email_cmd must be a string when provided")

    raw_contact_email = raw.get("contact_email")
    if raw_contact_email is not None and not isinstance(raw_contact_email, str):
        errors.append("contact_email must be a string when provided")

    raw_contact_email_cmd = raw.get("contact_email_cmd")
    if raw_contact_email_cmd is not None and not isinstance(raw_contact_email_cmd, str):
        errors.append("contact_email_cmd must be a string when provided")

    if errors:
        return None, errors

    raw_s2_key = raw.get("semantic_scholar_api_key")
    if raw_s2_key is not None and not isinstance(raw_s2_key, str):
        errors.append("semantic_scholar_api_key must be a string when provided")

    raw_s2_key_cmd = raw.get("semantic_scholar_api_key_cmd")
    if raw_s2_key_cmd is not None and not isinstance(raw_s2_key_cmd, str):
        errors.append("semantic_scholar_api_key_cmd must be a string when provided")

    raw_citekey_format = raw.get("citekey_format")
    if raw_citekey_format is not None and not isinstance(raw_citekey_format, str):
        errors.append("citekey_format must be a string when provided")

    raw_pdf_filename_format = raw.get("pdf_filename_format")
    if raw_pdf_filename_format is not None and not isinstance(raw_pdf_filename_format, str):
        errors.append("pdf_filename_format must be a string when provided")

    raw_browser_hook = raw.get("browser_hook", True)
    if not isinstance(raw_browser_hook, bool):
        errors.append("browser_hook must be a boolean")

    if errors:
        return None, errors

    return _normalize_app_config(raw, validated_bibs), []


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


def resolve_library_target(
    bibs: list[BibConfig], selector: str | None, *, home_dir: str
) -> BibConfig | None:
    """Resolve default/configured library name or direct .bib path target."""
    if selector is None:
        return resolve_bib(bibs, None)

    normalized_selector = selector.strip()
    normalized_path = _normalize_path(normalized_selector, home_dir=home_dir)
    for bib in bibs:
        if bib["name"] == normalized_selector:
            return bib
        if _normalize_path(bib["path"], home_dir=home_dir) == normalized_path:
            return bib

    if normalized_selector.endswith(".bib"):
        return {
            "name": os.path.splitext(os.path.basename(normalized_path))[0],
            "path": normalized_path,
            "papers_dir": derive_papers_dir(normalized_path),
            "default": False,
        }

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


# ---------------------------------------------------------------------------
# TOML file loading
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_RELATIVE_PATH = ".config/pzi/config.toml"

LoadConfigResult: TypeAlias = dict[str, Any]


def default_config_path(home_dir: str) -> str:
    """Return the default TOML config path under the given home directory."""
    return str(Path(home_dir) / DEFAULT_CONFIG_RELATIVE_PATH)


def load_config_file(path: str, *, home_dir: str) -> LoadConfigResult:
    """Load, parse, and validate a TOML config file."""
    config_path = Path(path)
    if not config_path.exists():
        return {
            "config": None,
            "errors": [f"config file not found: {config_path}"],
            "path": str(config_path),
        }

    try:
        raw_bytes = config_path.read_bytes()
    except OSError as exc:
        return {
            "config": None,
            "errors": [f"failed to read config file: {exc}"],
            "path": str(config_path),
        }

    try:
        raw_config = tomllib.loads(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        return {
            "config": None,
            "errors": ["config file must be valid UTF-8 text"],
            "path": str(config_path),
        }
    except tomllib.TOMLDecodeError as exc:
        return {
            "config": None,
            "errors": [f"invalid TOML: {exc}"],
            "path": str(config_path),
        }

    config, errors = validate_app_config(raw_config, home_dir=home_dir)
    return {"config": config, "errors": errors, "path": str(config_path)}


def load_default_config(*, home_dir: str) -> LoadConfigResult:
    """Load config from the default path under the given home directory."""
    return load_config_file(default_config_path(home_dir), home_dir=home_dir)


# ---------------------------------------------------------------------------
# TOML serialization
# ---------------------------------------------------------------------------

# Characters that must be escaped in TOML basic strings.
# \\ MUST come first to avoid re-escaping introduced backslashes.
_TOML_ESCAPE_MAP = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}

# Control characters (U+0000-U+001F) not handled by _TOML_ESCAPE_MAP
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _escape(value: str) -> str:
    """Escape a string value for a TOML basic string."""
    for char, escaped in _TOML_ESCAPE_MAP.items():
        value = value.replace(char, escaped)

    def _ctrl_escape(m: re.Match[str]) -> str:
        """Produce a ``\\uXXXX`` escape for a matched control character."""
        return f"\\u{ord(m.group(0)):04x}"

    return _CONTROL_RE.sub(_ctrl_escape, value)


def _optional_string(key: str, value: str | None) -> list[str]:
    """Return a single TOML key = value line if value is not None."""
    if value is not None:
        return [f'{key} = "{_escape(value)}"']
    return []


def _optional_int(key: str, value: int | None) -> list[str]:
    """Return a single TOML key = value line if value is not None."""
    if value is not None:
        return [f"{key} = {value}"]
    return []


def _optional_string_list(key: str, value: tuple[str, ...] | None) -> list[str]:
    """Return a TOML key = [...] line if value is not None and non-empty."""
    if value:
        items = ", ".join(f'"{_escape(item)}"' for item in value)
        return [f"{key} = [{items}]"]
    return []


def dump_app_config(config: AppConfig) -> str:
    """Serialize a full AppConfig to TOML text."""
    lines: list[str] = [
        f'translation_server_url = "{_escape(config["translation_server_url"])}"',
        f'api_listen_host = "{_escape(config["api_listen_host"])}"',
        f'api_listen_port = {config["api_listen_port"]}',
    ]

    lines.extend(_optional_string("api_auth_token", config.get("api_auth_token")))
    lines.extend(_optional_string_list("api_allowed_origins", config.get("api_allowed_origins")))
    lines.extend(_optional_int("api_max_body_bytes", config.get("api_max_body_bytes")))
    lines.extend(_optional_string("contact_email", config.get("contact_email")))
    lines.extend(_optional_string("contact_email_cmd", config.get("contact_email_cmd")))
    lines.extend(_optional_string("unpaywall_email", config.get("unpaywall_email")))
    lines.extend(_optional_string("unpaywall_email_cmd", config.get("unpaywall_email_cmd")))
    lines.extend(
        _optional_string("semantic_scholar_api_key", config.get("semantic_scholar_api_key"))
    )
    lines.extend(
        _optional_string(
            "semantic_scholar_api_key_cmd",
            config.get("semantic_scholar_api_key_cmd"),
        )
    )
    lines.extend(_optional_string("flaresolverr_url", config.get("flaresolverr_url")))
    lines.extend(_optional_string("browser_pdf_cmd", config.get("browser_pdf_cmd")))
    lines.extend(_optional_string("citekey_format", config.get("citekey_format")))
    lines.extend(_optional_string("pdf_filename_format", config.get("pdf_filename_format")))

    for bib in config["bibs"]:
        lines.append("")
        lines.append("[[bibs]]")
        lines.append(f'name = "{_escape(bib["name"])}"')
        lines.append(f'path = "{_escape(bib["path"])}"')
        lines.append(f'papers_dir = "{_escape(bib["papers_dir"])}"')
        lines.append(f"default = {'true' if bib['default'] else 'false'}")

    return "\n".join(lines) + "\n"


def load_and_resolve_bib(
    *, config_path: str, home_dir: str, bib_selector: str | None
) -> tuple[AppConfig, BibConfig] | list[str]:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return config_result["errors"]
    config = config_result["config"]
    bib = resolve_library_target(config["bibs"], bib_selector, home_dir=home_dir)
    if bib is None:
        return ["no matching library target found or selection is ambiguous"]
    return config, bib
