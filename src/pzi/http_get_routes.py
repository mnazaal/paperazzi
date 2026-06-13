"""Pure GET route handlers for the HTTP API.

All functions in this module are pure: they take request data as arguments
and return (status_code, response_dict) tuples. No I/O beyond service calls.
No references to BaseHTTPRequestHandler or server sockets.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

from pzi.bib_repository import read_bib_file
from pzi.bib_service import list_bibs
from pzi.config import load_and_resolve_bib
from pzi.doctor_service import doctor_check
from pzi.http_payloads import (
    detail_payload,
    entries_payload,
    search_payload,
    tag_list_payload,
)
from pzi.search_service import search_bib
from pzi.tag_service import list_tags


def process_get_request(
    path: str,
    config_path: str,
    home_dir: str,
) -> tuple[int, dict[str, Any]]:
    """Pure: process a GET request path, returning (status, body_dict).

    Testable without a server socket. Used by _handle_get.
    """
    parsed = urlsplit(path)
    p = parsed.path
    qs = _parse_query(parsed.query)

    if p == "/health":
        return 200, _health_payload(config_path, home_dir)
    if p == "/bibs":
        result = list_bibs(config_path=config_path, home_dir=home_dir)
        status = 200 if result["status"] == "ok" else 500
        return status, result
    if p == "/search":
        return _handle_search_get(config_path, home_dir, qs)
    if p == "/entries":
        return _handle_entries_get(config_path, home_dir, qs)
    if p.startswith("/detail/"):
        citekey = p[len("/detail/"):]
        bib_selector = qs.get("bib")
        return _handle_detail_get(config_path, home_dir, citekey, bib_selector)
    if p.startswith("/tags/"):
        citekey = p[len("/tags/"):]
        bib_selector = qs.get("bib")
        return _handle_tags_get(config_path, home_dir, citekey, bib_selector)
    if p == "/export":
        return _handle_export_get(config_path, home_dir, qs)
    return 404, {"error": "not found"}


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
    status = 200 if result["status"] == "ok" else 400
    return status, search_payload(result)


def _handle_entries_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    bib_selector = qs.get("bib") or None
    offset = _parse_int(qs.get("offset"), 0)
    limit = _parse_int(qs.get("limit"), 50)
    limit = max(1, min(limit, 500))

    result = search_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    status = 200 if result["status"] == "ok" else 400
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
    config_path: str, home_dir: str, citekey: str, bib_selector: str | None,
) -> tuple[int, dict[str, Any]]:
    if not citekey:
        return 400, {"error": "citekey required"}

    result = list_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
    )
    status = 200 if result["status"] == "ok" else 400
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
