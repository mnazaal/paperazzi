"""GET route boundary for the HTTP API.

This module keeps socket/server concerns out of request handling: functions take
plain request data and return ``(status_code, response_dict)`` tuples. Parsing
helpers are pure; endpoint handlers are thin imperative shells around service
calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pzi.bib_repository import read_bib_file
from pzi.bib_service import list_bibs, list_entries
from pzi.config import load_and_resolve_bib
from pzi.doctor_service import doctor_check
from pzi.http_payloads import (
    detail_payload,
    entries_payload,
    search_payload,
    tag_list_payload,
)
from pzi.http_status import status_for_service_result
from pzi.search_service import search_bib
from pzi.tag_service import list_tags

JsonResponse = tuple[int, dict[str, Any]]
ExactGetHandler = Callable[[str, str, dict[str, str]], JsonResponse]
PrefixGetHandler = Callable[[str, str, str, dict[str, str]], JsonResponse]


@dataclass(frozen=True)
class GetRoute:
    path: str
    handler: ExactGetHandler


@dataclass(frozen=True)
class GetPrefixRoute:
    prefix: str
    handler: PrefixGetHandler


def process_get_request(
    path: str,
    config_path: str,
    home_dir: str,
) -> tuple[int, dict[str, Any]]:
    """Process a GET path without server/socket dependencies."""
    parsed = urlsplit(path)
    p = parsed.path
    qs = _parse_query(parsed.query)

    for route in GET_ROUTES:
        if p == route.path:
            return route.handler(config_path, home_dir, qs)
    for route in GET_PREFIX_ROUTES:
        if p.startswith(route.prefix):
            return route.handler(config_path, home_dir, p[len(route.prefix):], qs)
    return 404, {"error": "not found"}


def _handle_health_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> JsonResponse:
    return 200, _health_payload(config_path, home_dir)


def _handle_bibs_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> JsonResponse:
    result = list_bibs(config_path=config_path, home_dir=home_dir)
    status = status_for_service_result(result)
    return status, result


def _parse_query(query_string: str) -> dict[str, str]:
    """Parse a URL query string into a flat dict of single-value params."""
    result: dict[str, str] = {}
    for key, values in parse_qs(query_string).items():
        if values:
            result[key] = values[0]
    return result


def _handle_search_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    bib_selector = qs.get("bib") or None
    query = qs.get("q") or None
    author = qs.get("author") or None
    year_raw = qs.get("year")
    year: int | None = None
    if year_raw is not None:
        try:
            year = int(year_raw)
        except ValueError:
            return 400, {"error": "year must be an integer"}
    tag = qs.get("tag") or None

    result = search_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        query=query,
        author=author,
        year=year,
        tag=tag,
    )
    status = status_for_service_result(result)
    return status, search_payload(result)


def _handle_entries_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    bib_selector = qs.get("bib") or None
    offset = max(0, _parse_int(qs.get("offset"), 0))
    limit = _parse_int(qs.get("limit"), 50)
    limit = max(1, min(limit, 500))
    sort = qs.get("sort") or "citekey"

    result = list_entries(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        offset=offset,
        limit=limit,
        sort=sort,
    )
    status = status_for_service_result(result)
    return status, entries_payload(result, offset, limit)


def _handle_detail_get(
    config_path: str, home_dir: str, citekey: str, bib_selector: str | None,
) -> tuple[int, dict[str, Any]]:
    if not citekey:
        return 400, {"error": "citekey required"}

    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}
    _config, bib = resolved

    read_result = read_bib_file(bib["path"])
    for record in read_result["records"]:
        if record.get("citekey") == citekey:
            return 200, detail_payload(record, bib["name"])
    return 404, {"error": f"citekey not found: {citekey}"}


def _handle_tags_get(
    config_path: str, home_dir: str, citekey: str | None, bib_selector: str | None,
) -> tuple[int, dict[str, Any]]:
    result = list_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
    )
    status = status_for_service_result(result)
    return status, tag_list_payload(result)


def _handle_export_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    fmt = qs.get("format", "bibtex")
    if fmt not in ("bibtex", "csv", "json", "ris"):
        return 400, {"error": f"unsupported format: {fmt}"}

    bib_selector = qs.get("bib") or None
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}

    _config, bib = resolved
    from pzi.export_service import export_bibtex, export_csv, export_json, export_ris

    exporters = {
        "bibtex": export_bibtex,
        "csv": export_csv,
        "json": export_json,
        "ris": export_ris,
    }
    result = exporters[fmt](bib_path=bib["path"])

    if result["status"] != "ok":
        return 500, {"error": "export failed", "errors": result.get("errors", [])}

    return 200, {
        "format": result["format"],
        "total_entries": result["total_entries"],
        "content_type": result["content_type"],
        "content": result["content"],
    }


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _health_payload(config_path: str, home_dir: str) -> dict[str, Any]:
    result = doctor_check(config_path=config_path, home_dir=home_dir)
    return {
        "status": result["status"],
        "config_ok": result["config_ok"],
        "config_errors": result["config_errors"],
        "translation_server_url": result["translation_server_url"],
        "translation_server_reachable": result["translation_server_reachable"],
    }


def _handle_detail_prefix_get(
    config_path: str, home_dir: str, citekey: str, qs: dict[str, str],
) -> JsonResponse:
    return _handle_detail_get(config_path, home_dir, citekey, qs.get("bib"))


def _handle_tags_prefix_get(
    config_path: str, home_dir: str, citekey: str, qs: dict[str, str],
) -> JsonResponse:
    return _handle_tags_get(config_path, home_dir, citekey, qs.get("bib"))


def _handle_tags_exact_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> JsonResponse:
    return _handle_tags_get(config_path, home_dir, None, qs.get("bib"))


GET_ROUTES: tuple[GetRoute, ...] = (
    GetRoute("/health", _handle_health_get),
    GetRoute("/bibs", _handle_bibs_get),
    GetRoute("/search", _handle_search_get),
    GetRoute("/entries", _handle_entries_get),
    GetRoute("/tags", _handle_tags_exact_get),
    GetRoute("/export", _handle_export_get),
)


GET_PREFIX_ROUTES: tuple[GetPrefixRoute, ...] = (
    GetPrefixRoute("/detail/", _handle_detail_prefix_get),
    GetPrefixRoute("/tags/", _handle_tags_prefix_get),
)
