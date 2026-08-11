"""Config types, validation, TOML loading, and TOML serialization."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypedDict
from urllib.parse import urlsplit

from pzi.format_templates import describe_template_error

# Minimum `resolution_match.score_match` result (0–100) for `update --promote`
# to write a published version over a preprint. Named because it is read from
# three places — the loader, the validator, and `config.template.toml` — and a
# copy that drifts out of step silently loosens or tightens the gate.
DEFAULT_PROMOTE_CONFIDENCE_THRESHOLD = 60

# Where the translation-server is assumed to be when the config does not say.
# Named for the same reason as the threshold above — it was written out twice,
# in the loader and the validator — and additionally so the test suite can
# repoint it at a dead port. A config that omits the key otherwise resolves to
# the *real* default, which means a test seeding such a config silently reaches
# whatever the developer happens to have listening on 1969.
DEFAULT_TRANSLATION_SERVER_URL = "http://127.0.0.1:1969"


class BibConfig(TypedDict):
    name: str
    path: str
    papers_dir: str
    default: bool


class AppConfig(TypedDict):
    translation_server_url: str
    bibs: list[BibConfig]
    api_listen_host: str
    api_listen_port: int
    api_auth_token: str | None
    api_auth_token_cmd: str | None
    api_allowed_origins: tuple[str, ...] | None
    #: Directories the HTTP `/capture` route may ingest a local PDF from.
    #: Empty by default: the route is extension-facing and the extension only
    #: ever sends http(s) URLs, so a local path is accepted only where the user
    #: has deliberately opted in.
    capture_source_dirs: tuple[str, ...]
    #: The only file `POST /inbox/drain` may read and rewrite. Unset closes the
    #: route, since draining rewrites the target in place.
    inbox_path: str | None
    api_max_body_bytes: int
    contact_email: str | None
    contact_email_cmd: str | None
    unpaywall_email: str | None
    unpaywall_email_cmd: str | None
    semantic_scholar_api_key: str | None
    semantic_scholar_api_key_cmd: str | None
    flaresolverr_url: str | None
    browser_pdf_cmd: str | None
    citekey_format: str | None
    pdf_filename_format: str | None
    pdf_file_path_style: str
    page_metadata_cmd: str | None
    page_metadata_timeout_seconds: int
    metadata_confidence_min_score: int
    promote_confidence_threshold: int
    metadata_cache_ttl: int
    browser_hook: bool
    pzi_data_home: str
    node_path: str | None
    api_url: str
    browser_profile_path: str | None
    browser_engine: str
    pdf_discovery_parallel: bool
    desktop_fallback_hosts: list[str]
    ezproxy_host: str | None



def validate_bib_config(
    raw: Mapping[str, object], *, home_dir: str, base_dir: str | None = None
) -> tuple[BibConfig | None, list[str]]:
    """Validate one bib config and derive computed defaults."""
    errors: list[str] = []

    name = _opt_str_from_raw(raw, "name")
    if name is None:
        errors.append("bib.name must be a non-empty string")

    raw_path_val = _opt_str_from_raw(raw, "path")
    if raw_path_val is None:
        errors.append("bib.path must be a non-empty string")

    raw_papers_dir = raw.get("papers_dir")
    if raw_papers_dir is not None and not isinstance(raw_papers_dir, str):
        errors.append("bib.papers_dir must be a string when provided")

    raw_default = raw.get("default", False)
    if not isinstance(raw_default, bool):
        errors.append("bib.default must be a boolean")

    if errors:
        return None, errors

    assert raw_path_val is not None, "already validated"
    assert name is not None, "already validated"
    assert isinstance(raw_default, bool), "already validated"
    path = _normalize_path(raw_path_val, home_dir=home_dir, base_dir=base_dir)
    papers_dir = (
        _normalize_path(raw_papers_dir, home_dir=home_dir, base_dir=base_dir)
        if isinstance(raw_papers_dir, str)
        else derive_papers_dir(path)
    )

    config: BibConfig = {
        "name": name,
        "path": path,
        "papers_dir": papers_dir,
        "default": raw_default,
    }
    return config, []


def _safe_int(value: object, default: int, *, min_value: int = 0) -> int:
    """Return an int from a trusted-or-unknown raw config value, or *default*."""
    if isinstance(value, int) and not isinstance(value, bool):
        return max(min_value, value)
    return default


def _safe_bool(value: object, default: bool) -> bool:
    """Return a bool from a trusted-or-unknown raw config value, or *default*."""
    if isinstance(value, bool):
        return value
    return default


def _opt_str_from_raw(raw: Mapping[str, object], key: str) -> str | None:
    """Return stripped non-empty string from a raw config mapping, or None."""
    v = raw.get(key)
    if not isinstance(v, str):
        return None
    return v.strip() or None


def _expanded_opt(raw: Mapping[str, object], key: str) -> str | None:
    """Like :func:`_opt_str_from_raw` but expands a leading ``~``.

    Path-valued keys are compared against resolved paths at use time, so they
    have to be expanded here or a configured ``~/inbox.txt`` never matches.
    """
    value = _opt_str_from_raw(raw, key)
    return os.path.expanduser(value) if value is not None else None


def _validate_bib_list(
    raw_bibs: object, *, home_dir: str, base_dir: str | None = None
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

        bib_config, bib_errors = validate_bib_config(
            bib_value, home_dir=home_dir, base_dir=base_dir
        )
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


def _normalize_app_config(
    raw: Mapping[str, object], validated_bibs: list[BibConfig], *, home_dir: str
) -> AppConfig:
    """Build a normalized AppConfig from already-validated fields.

    Pure normalization — no validation.  Callers must pre-validate.
    """
    raw_api_allowed_origins = raw.get("api_allowed_origins")
    normalized_api_allowed_origins: tuple[str, ...] | None = None
    if isinstance(raw_api_allowed_origins, list):
        # No trailing `or None`: an explicit `[]` has to survive as `()` so the
        # user can say "allow no origins at all". `None` keeps meaning "the key
        # was absent", which is what selects the defaults downstream.
        normalized_api_allowed_origins = tuple(
            origin.strip()
            for origin in raw_api_allowed_origins
            if isinstance(origin, str) and origin.strip()
        )

    raw_capture_source_dirs = raw.get("capture_source_dirs")
    normalized_capture_source_dirs: tuple[str, ...] = ()
    if isinstance(raw_capture_source_dirs, list):
        normalized_capture_source_dirs = tuple(
            os.path.expanduser(d.strip())
            for d in raw_capture_source_dirs
            if isinstance(d, str) and d.strip()
        )

    def opt(k: str) -> str | None:
        return _opt_str_from_raw(raw, k)

    flaresolverr_url = opt("flaresolverr_url")
    if flaresolverr_url is not None and not _is_http_url(flaresolverr_url):
        flaresolverr_url = None

    raw_translation_server_url = raw.get(
        "translation_server_url", DEFAULT_TRANSLATION_SERVER_URL
    )
    raw_api_listen_host = raw.get("api_listen_host", "127.0.0.1")
    raw_api_listen_port = raw.get("api_listen_port", 8765)
    raw_api_max_body_bytes = raw.get("api_max_body_bytes", 64 * 1024 * 1024)
    raw_metadata_confidence_min_score = raw.get("metadata_confidence_min_score", 0)
    raw_promote_confidence_threshold = raw.get(
        "promote_confidence_threshold", DEFAULT_PROMOTE_CONFIDENCE_THRESHOLD
    )
    raw_metadata_cache_ttl = raw.get("metadata_cache_ttl", 0)
    raw_browser_hook = raw.get("browser_hook", True)
    raw_pzi_data_home = raw.get("pzi_data_home")
    raw_browser_engine = raw.get("browser_engine", "chromium")
    raw_pdf_discovery_parallel = raw.get("pdf_discovery_parallel", False)
    raw_pdf_file_path_style = raw.get("pdf_file_path_style", "absolute")
    raw_page_metadata_timeout_seconds = raw.get("page_metadata_timeout_seconds", 5)
    raw_desktop_fallback_hosts = raw.get(
        "desktop_fallback_hosts", DEFAULT_DESKTOP_FALLBACK_HOSTS
    )

    api_url = opt("api_url")
    api_listen_host = str(raw_api_listen_host).strip()
    api_listen_port = _safe_int(raw_api_listen_port, 8765, min_value=1)
    if not api_url:
        api_url = f"http://{api_listen_host}:{api_listen_port}"

    return {
        "translation_server_url": str(raw_translation_server_url),
        "bibs": validated_bibs,
        "api_listen_host": api_listen_host,
        "api_listen_port": api_listen_port,
        "api_auth_token": opt("api_auth_token"),
        "api_auth_token_cmd": opt("api_auth_token_cmd"),
        "api_allowed_origins": normalized_api_allowed_origins,
        "capture_source_dirs": normalized_capture_source_dirs,
        "inbox_path": _expanded_opt(raw, "inbox_path"),
        "api_max_body_bytes": _safe_int(raw_api_max_body_bytes, 64 * 1024 * 1024),
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
        "pdf_file_path_style": str(raw_pdf_file_path_style).strip() or "absolute",
        "page_metadata_cmd": opt("page_metadata_cmd"),
        "page_metadata_timeout_seconds": max(
            1, _safe_int(raw_page_metadata_timeout_seconds, 5, min_value=1)
        ),
        "metadata_confidence_min_score": _safe_int(raw_metadata_confidence_min_score, 0),
        "promote_confidence_threshold": _safe_int(
            raw_promote_confidence_threshold, DEFAULT_PROMOTE_CONFIDENCE_THRESHOLD
        ),
        "metadata_cache_ttl": max(0, _safe_int(raw_metadata_cache_ttl, 0)),
        "browser_hook": _safe_bool(raw_browser_hook, True),
        "pzi_data_home": (
            default_data_home(home_dir)
            if raw_pzi_data_home is None
            else os.path.expanduser(str(raw_pzi_data_home))
        ),
        "node_path": opt("node_path"),
        "api_url": api_url,
        "browser_profile_path": opt("browser_profile_path"),
        "browser_engine": str(raw_browser_engine).strip() or "chromium",
        "pdf_discovery_parallel": _safe_bool(raw_pdf_discovery_parallel, False),
        "desktop_fallback_hosts": (
            _normalize_host_list(raw_desktop_fallback_hosts)
            if isinstance(raw_desktop_fallback_hosts, list)
            else DEFAULT_DESKTOP_FALLBACK_HOSTS
        ),
        "ezproxy_host": opt("ezproxy_host"),
    }


def validate_app_config(
    raw: Mapping[str, object], *, home_dir: str, base_dir: str | None = None
) -> tuple[AppConfig | None, list[str]]:
    """Validate application config into one plain normalized shape."""
    errors: list[str] = []

    raw_translation_server_url = raw.get(
        "translation_server_url", DEFAULT_TRANSLATION_SERVER_URL
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

    raw_api_auth_token_cmd = raw.get("api_auth_token_cmd")
    if raw_api_auth_token_cmd is not None and not isinstance(raw_api_auth_token_cmd, str):
        errors.append("api_auth_token_cmd must be a string when provided")

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

    raw_promote_confidence_threshold = raw.get(
        "promote_confidence_threshold", DEFAULT_PROMOTE_CONFIDENCE_THRESHOLD
    )
    if (
        not isinstance(raw_promote_confidence_threshold, int)
        or isinstance(raw_promote_confidence_threshold, bool)
        or not 0 <= raw_promote_confidence_threshold <= 100
    ):
        errors.append("promote_confidence_threshold must be an integer between 0 and 100")

    raw_metadata_cache_ttl = raw.get("metadata_cache_ttl", 0)
    if (
        not isinstance(raw_metadata_cache_ttl, int)
        or isinstance(raw_metadata_cache_ttl, bool)
        or raw_metadata_cache_ttl < 0
    ):
        errors.append("metadata_cache_ttl must be a non-negative integer")

    raw_pdf_file_path_style = raw.get("pdf_file_path_style", "absolute")
    if raw_pdf_file_path_style not in {"absolute", "relative"}:
        errors.append("pdf_file_path_style must be 'absolute' or 'relative'")

    raw_page_metadata_cmd = raw.get("page_metadata_cmd")
    if raw_page_metadata_cmd is not None and not isinstance(raw_page_metadata_cmd, str):
        errors.append("page_metadata_cmd must be a string when provided")

    # `_opt_str_from_raw` returns None for any non-string, so a wrong type reads
    # as "unset" and vanishes. That is exactly the silent fallback the
    # `node_path` docs disclaim ("a set-but-broken value is a hard error"), and
    # it applied to all five of these keys.
    for key in (
        "flaresolverr_url",
        "api_url",
        "browser_pdf_cmd",
        "node_path",
        "browser_profile_path",
    ):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            errors.append(f"{key} must be a string when provided")

    # Both are URLs and both were discarded in silence when malformed:
    # `flaresolverr_url` was nulled by the normalizer with no error at all,
    # which left the feature off while `add_planning` told the user to
    # configure the thing they had configured.
    for key in ("flaresolverr_url", "api_url"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip() and not _is_http_url(value.strip()):
            errors.append(f"{key} must be an http or https URL")

    raw_page_metadata_timeout_seconds = raw.get("page_metadata_timeout_seconds", 5)
    if (
        not isinstance(raw_page_metadata_timeout_seconds, int)
        or isinstance(raw_page_metadata_timeout_seconds, bool)
        or raw_page_metadata_timeout_seconds < 1
    ):
        errors.append("page_metadata_timeout_seconds must be a positive integer")

    raw_bibs = raw.get("bibs")
    if not isinstance(raw_bibs, list) or not raw_bibs:
        errors.append("bibs must be a non-empty list")

    if errors:
        return None, errors

    validated_bibs, bib_errors = _validate_bib_list(
        raw_bibs, home_dir=home_dir, base_dir=base_dir
    )
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

    raw_capture_source_dirs = raw.get("capture_source_dirs")
    if raw_capture_source_dirs is not None and (
        not isinstance(raw_capture_source_dirs, list)
        or not all(isinstance(d, str) for d in raw_capture_source_dirs)
    ):
        errors.append("capture_source_dirs must be a list of strings when provided")

    raw_inbox_path = raw.get("inbox_path")
    if raw_inbox_path is not None and not isinstance(raw_inbox_path, str):
        errors.append("inbox_path must be a string when provided")

    raw_citekey_format = raw.get("citekey_format")
    if raw_citekey_format is not None and not isinstance(raw_citekey_format, str):
        errors.append("citekey_format must be a string when provided")
    elif isinstance(raw_citekey_format, str):
        # The template renderer degrades on a malformed template rather than
        # raising, so without this check a typo would silently drop the option
        # from every citekey it generates.
        template_error = describe_template_error(raw_citekey_format)
        if template_error is not None:
            errors.append(f"citekey_format is not a valid template: {template_error}")

    raw_pdf_filename_format = raw.get("pdf_filename_format")
    if raw_pdf_filename_format is not None and not isinstance(raw_pdf_filename_format, str):
        errors.append("pdf_filename_format must be a string when provided")
    elif isinstance(raw_pdf_filename_format, str):
        template_error = describe_template_error(raw_pdf_filename_format)
        if template_error is not None:
            errors.append(f"pdf_filename_format is not a valid template: {template_error}")

    raw_ezproxy_host = raw.get("ezproxy_host")
    if raw_ezproxy_host is not None and (
        not isinstance(raw_ezproxy_host, str) or not _is_bare_hostname(raw_ezproxy_host)
    ):
        errors.append(
            "ezproxy_host must be a bare hostname "
            "(e.g. proxy.lib.university.edu) when provided"
        )

    raw_browser_hook = raw.get("browser_hook", True)
    if not isinstance(raw_browser_hook, bool):
        errors.append("browser_hook must be a boolean")

    raw_pdf_discovery_parallel = raw.get("pdf_discovery_parallel", False)
    if not isinstance(raw_pdf_discovery_parallel, bool):
        errors.append("pdf_discovery_parallel must be a boolean")

    raw_browser_engine = raw.get("browser_engine", "chromium")
    if (
        not isinstance(raw_browser_engine, str)
        or raw_browser_engine.strip() not in {"chromium", "firefox", "webkit"}
    ):
        errors.append("browser_engine must be 'chromium', 'firefox', or 'webkit'")

    raw_desktop_fallback_hosts = raw.get(
        "desktop_fallback_hosts", DEFAULT_DESKTOP_FALLBACK_HOSTS
    )
    if raw_desktop_fallback_hosts is not None and not isinstance(raw_desktop_fallback_hosts, list):
        errors.append("desktop_fallback_hosts must be a list when provided")

    if errors:
        return None, errors

    raw_pzi_data_home = raw.get("pzi_data_home")
    if raw_pzi_data_home is not None and (
        not isinstance(raw_pzi_data_home, str) or not raw_pzi_data_home.strip()
    ):
        errors.append("pzi_data_home must be a non-empty string")
        return None, errors

    return _normalize_app_config(raw, validated_bibs, home_dir=home_dir), []


def derive_papers_dir(bib_path: str) -> str:
    """Return the default sibling papers directory for a bib file."""
    return os.path.join(os.path.dirname(bib_path), "papers")


def resolve_bib(bibs: list[BibConfig]) -> BibConfig | None:
    """Pick the default bib: the only one, or the one marked default.

    Selector handling lives in :func:`resolve_library_target`, which normalizes
    both sides of a path comparison. The selector branch that used to be here
    compared ``bib["path"]`` by raw string equality against user input, so
    ``--target ~/ml.bib`` would not have matched a config storing
    ``/home/you/ml.bib`` — it was never reached, and would have been wrong if it
    had been.
    """
    if len(bibs) == 1:
        return bibs[0]

    defaults = [bib for bib in bibs if bib["default"]]
    if len(defaults) == 1:
        return defaults[0]

    return None


def is_configured_selector(
    bibs: list[BibConfig], selector: str | None, *, home_dir: str
) -> bool:
    """True when *selector* names a library the config already declares.

    The HTTP API confines requests to configured libraries with this: on the CLI
    a direct ``.bib`` path is a deliberate convenience, but over HTTP it would
    let any request that reaches the API — the extension, or any local process
    when auth is off — make pzi create and write a library anywhere the user can
    write.
    """
    if selector is None:
        return True
    normalized_selector = selector.strip()
    normalized_path = _normalize_path(normalized_selector, home_dir=home_dir)
    return any(
        bib["name"] == normalized_selector
        or _normalize_path(bib["path"], home_dir=home_dir) == normalized_path
        for bib in bibs
    )


def resolve_library_target(
    bibs: list[BibConfig], selector: str | None, *, home_dir: str
) -> BibConfig | None:
    """Resolve default/configured library name or direct .bib path target."""
    if selector is None:
        return resolve_bib(bibs)

    normalized_selector = selector.strip()
    normalized_path = _normalize_path(normalized_selector, home_dir=home_dir)
    for bib in bibs:
        if bib["name"] == normalized_selector:
            return bib
        if _normalize_path(bib["path"], home_dir=home_dir) == normalized_path:
            return bib

    if normalized_selector.endswith(".bib"):
        # A direct path must exist. `pzi entries --target typo.bib` used to
        # report `entries: 0` at exit 0 — indistinguishable from a clean
        # library, and `README.md` promises exit 5 for an unknown target.
        # A *configured* name pointing at a not-yet-created file still resolves
        # above: that is a library the user declared and pzi will create.
        if not os.path.exists(normalized_path):
            return None
        return {
            "name": os.path.splitext(os.path.basename(normalized_path))[0],
            "path": normalized_path,
            "papers_dir": derive_papers_dir(normalized_path),
            "default": False,
        }

    return None


def _normalize_path(value: str, *, home_dir: str, base_dir: str | None = None) -> str:
    """Absolute, normalized form of a configured path.

    A relative value resolves against *base_dir* — the directory holding the
    config file that named it — not the process's current directory. Resolving
    against the CWD meant `path = "ml.bib"` pointed at a different file
    depending on where `pzi` was run from: correct from the project root, and a
    second empty library anywhere else, created without a word.

    *base_dir* is omitted where there is no config file to resolve against (a
    `--target` typed on the command line, which the user means relative to
    where they are standing).
    """
    expanded = value.strip()
    if expanded == "~":
        return os.path.normpath(home_dir)
    if expanded.startswith("~/"):
        expanded = os.path.join(home_dir, expanded[2:])
    elif base_dir is not None and not os.path.isabs(expanded):
        expanded = os.path.join(base_dir, expanded)
    return os.path.normpath(os.path.abspath(expanded))


def tildify_path(value: str, *, home_dir: str) -> str:
    """Inverse of :func:`_normalize_path`: fold a leading *home_dir* to ``~``.

    Lets generated config reference ``~/...`` instead of an absolute home path,
    so a committed ``config.toml`` does not expose the user's home layout. Paths
    outside *home_dir* (e.g. a system ``/usr/bin/python3``) are returned
    unchanged — they are not a privacy leak and may not round-trip through ``~``.
    """
    normalized = os.path.normpath(value)
    home = os.path.normpath(home_dir)
    if normalized == home:
        return "~"
    prefix = home + os.sep
    if normalized.startswith(prefix):
        return "~/" + normalized[len(prefix):]
    return value


def _is_http_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _is_bare_hostname(value: str) -> bool:
    """Accept bare hostname like proxy.lib.university.edu (dots, no scheme)."""
    if urlsplit(value).scheme:  # rejects anything with scheme
        return False
    # Must contain at least one dot, no slashes, no spaces
    return "." in value and "/" not in value and " " not in value


def _normalize_host_list(raw: list[object]) -> list[str]:
    """Convert a raw config list to a deduplicated sorted hostname list."""
    hosts: list[str] = []
    seen: set[str] = set()
    for item in raw:
        host = str(item).strip().lower() if isinstance(item, str) else None
        if host and host not in seen:
            hosts.append(host)
            seen.add(host)
    # Returns an empty list unchanged: the caller decides what absent means, and
    # collapsing `[]` to the defaults here made "no fallback hosts"
    # inexpressible.
    return hosts


# ---------------------------------------------------------------------------
# TOML file loading
# ---------------------------------------------------------------------------

DEFAULT_DESKTOP_FALLBACK_HOSTS = [
    "biorxiv.org",
    "medrxiv.org",
    "researchsquare.com",
    "ssrn.com",
    "authorea.com",
]

LoadConfigResult: TypeAlias = dict[str, Any]


def _xdg_base_dir(env_var: str, home_dir: str, fallback_rel: str) -> str:
    """Return an XDG base directory per the XDG Base Directory spec.

    Uses ``$env_var`` when it is set to an *absolute* path; otherwise falls
    back to ``home_dir/fallback_rel``. The spec mandates ignoring relative
    values, so a non-absolute ``$env_var`` is treated as unset.
    """
    value = os.environ.get(env_var)
    if value and os.path.isabs(value):
        return value
    return os.path.join(home_dir, fallback_rel)


def xdg_config_home(home_dir: str) -> str:
    """Return ``$XDG_CONFIG_HOME`` (if absolute) else ``home_dir/.config``."""
    return _xdg_base_dir("XDG_CONFIG_HOME", home_dir, ".config")


def xdg_data_home(home_dir: str) -> str:
    """Return ``$XDG_DATA_HOME`` (if absolute) else ``home_dir/.local/share``."""
    return _xdg_base_dir("XDG_DATA_HOME", home_dir, ".local/share")


def default_data_home(home_dir: str) -> str:
    """Return the default pzi data home: ``<xdg-data-home>/pzi``."""
    return os.path.join(xdg_data_home(home_dir), "pzi")


def default_config_path(home_dir: str) -> str:
    """Return the default TOML config path: ``<xdg-config-home>/pzi/config.toml``."""
    return os.path.join(xdg_config_home(home_dir), "pzi", "config.toml")


#: Keys pzi used to accept, and why they went. A config carrying one still
#: loads — it is a warning, not an error — but "unknown config key" would
#: suggest a typo when the real answer is that the feature was removed.
RETIRED_CONFIG_KEYS: dict[str, str] = {
    "rate_limit_rpm": (
        "config key 'rate_limit_rpm' is retired and ignored: the inbound HTTP "
        "rate limiter was removed. It was keyed on the peer address, so every "
        "local process shared one bucket on loopback, and it ran after the auth "
        "gate, so it never metered a failed token. The API token is the control."
    ),
}


def unknown_config_keys(raw: Mapping[str, object]) -> list[str]:
    """Warn about top-level keys pzi does not know.

    A *warning*, not an error, so a config written for a newer pzi still loads.
    Silently ignoring them meant a typo in `capture_source_dirs`,
    `inbox_path`, `promote_confidence_threshold` or `pdf_file_path_style` left
    the default in place with no diagnostic — the setting simply did nothing.
    """
    known = set(AppConfig.__annotations__) | {"bibs"}
    return [
        RETIRED_CONFIG_KEYS.get(key) or f"unknown config key {key!r} (ignored)"
        for key in sorted(raw)
        if key not in known
    ]


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

    config, errors = validate_app_config(
        raw_config, home_dir=home_dir, base_dir=str(config_path.parent)
    )
    return {
        "config": config,
        "errors": errors,
        "warnings": unknown_config_keys(raw_config),
        "path": str(config_path),
    }


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


# Public name for reuse by other config writers (e.g. setup_service).
escape_toml_string = _escape


@dataclass(frozen=True)
class BibResolutionFailure:
    """Why a config + target could not be resolved.

    *reason* is the structured discriminator callers branch on.  It replaces
    comparing the returned error list against an exact message string, which
    made control flow depend on the wording of a diagnostic.
    """

    reason: Literal["config_invalid", "target_unresolved"]
    errors: list[str]


def load_bib_target(
    *, config_path: str, home_dir: str, bib_selector: str | None
) -> tuple[AppConfig, BibConfig] | BibResolutionFailure:
    """Load the config and resolve one library target from it."""
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return BibResolutionFailure("config_invalid", config_result["errors"])
    config = config_result["config"]
    bib = resolve_library_target(config["bibs"], bib_selector, home_dir=home_dir)
    if bib is None:
        return BibResolutionFailure(
            "target_unresolved",
            [_unresolved_target_error(config["bibs"], bib_selector, home_dir=home_dir)],
        )
    return config, bib


def _unresolved_target_error(
    bibs: list[BibConfig], selector: str | None, *, home_dir: str
) -> str:
    """Say which of the three ways the target failed to resolve, and name the rest.

    One string — "no matching library target found or selection is ambiguous" —
    covered an unknown name, a `.bib` path that does not exist, and an ambiguous
    selection with no default. Those need three different actions from the user,
    and the config is loaded and in hand, so it can also list what *would* have
    worked instead of making them go read the file.
    """
    names = [bib["name"] for bib in bibs]
    configured = ", ".join(names) if names else "none"

    if selector is None:
        if not bibs:
            return "config declares no libraries; add a [[bibs]] table to config.toml"
        return (
            f"no default library and no --target given "
            f"(configured: {configured}; mark one `default = true`)"
        )

    normalized = selector.strip()
    if normalized.endswith(".bib"):
        return (
            f"--target {selector!r} is a .bib path that does not exist "
            f"(a direct path must already exist; configured libraries: {configured})"
        )
    return f"--target {selector!r} is not a configured library (configured: {configured})"
