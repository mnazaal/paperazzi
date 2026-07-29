"""CLI runner for `pzi entries` (list, single-record detail, or --stats)."""

from __future__ import annotations

from pzi import cli_json, exit_codes
from pzi.bib_service import bib_stats, entry_detail, list_entries
from pzi.cli_render import _error_lines, _render_bib_stats
from pzi.commands.common import (
    exit_code_for_error,
    print_lines,
    print_read_warnings,
    resolve_target,
)


def run_entries_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if getattr(args, "stats", False):
        return _run_stats(args, home_dir, config_path, stdout, stderr, bib_selector)
    if getattr(args, "citekey", None):
        return _run_detail(args, home_dir, config_path, stdout, stderr, bib_selector)
    return _run_list(args, home_dir, config_path, stdout, stderr, bib_selector)


def _run_list(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    result = list_entries(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        offset=max(0, args.offset),
        limit=max(1, min(args.limit, 500)),
        sort=args.sort,
    )
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="entries")
        return exit_codes.OK if result["status"] == "ok" else exit_codes.ENVIRONMENT
    if result["status"] == "ok":
        items = result["items"]
        if not items:
            print("(no entries)", file=stderr)
            return exit_codes.OK
        for item in items:
            ck = item["citekey"]
            title = item.get("title", "") or ""
            year_str = str(item["year"]) if item.get("year") else ""
            authors = "; ".join(item.get("authors") or [])
            # Fixed five tab-separated columns, always. The PDF flag used to be
            # glued onto the authors column without a separator, so awk -F'\t'
            # read it as part of an author name.
            has_pdf = "pdf" if item.get("has_pdf") else ""
            print(f"{ck}\t{year_str}\t{title}\t{authors}\t{has_pdf}", file=stdout)
        total = result["total"]
        offset = result["offset"]
        limit = result["limit"]
        shown = min(len(items), limit)
        print_read_warnings(result, stderr)
        # Summary goes to stderr so `pzi entries | cut` stays clean.
        print(
            f"{offset + 1}-{offset + shown} of {total} entries "
            f"(bib: {result['bib_name']}, sort: {result['sort']})",
            file=stderr,
        )
        return exit_codes.OK
    print_lines(_error_lines("failed to list entries", result["errors"]), stderr)
    return exit_codes.ENVIRONMENT


def _run_detail(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    result = entry_detail(
        config_path=config_path,
        home_dir=home_dir,
        citekey=args.citekey,
        bib_selector=bib_selector,
    )
    if result["status"] != "ok":
        # The `--json` branch below sits after this guard, so an unknown citekey
        # used to emit no document at all — the one case a script most needs to
        # classify.
        if getattr(args, "json", False):
            cli_json.emit_result(result, stdout, command="entries", items=[])
        else:
            print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return exit_code_for_error(result)
    record = result["record"]
    print_read_warnings(result, stderr)
    if getattr(args, "json", False):
        # One record still arrives as a one-item envelope, so `.items[]` is the
        # same jq path as the listing. The service's own `record` key is dropped
        # rather than duplicated beside `items`.
        cli_json.emit_result(
            {k: v for k, v in result.items() if k != "record"},
            stdout,
            command="entries",
            items=[record],
        )
        return exit_codes.OK
    print(f"citekey: {record.get('citekey', '')}", file=stdout)
    print(f"title: {record.get('title', '')}", file=stdout)
    year = record.get("year")
    if year:
        print(f"year: {year}", file=stdout)
    authors = record.get("authors")
    if isinstance(authors, list) and authors:
        names = [name for name in (_author_name(a) for a in authors) if name]
        if names:
            print(f"authors: {'; '.join(names)}", file=stdout)
    for key in ("venue", "doi", "arxiv_id", "canonical_url"):
        val = record.get(key)
        if val:
            print(f"{key}: {val}", file=stdout)
    pdf = record.get("local_pdf_path")
    if pdf:
        print(f"pdf: {pdf}", file=stdout)
    tags = record.get("tags")
    if isinstance(tags, list) and tags:
        print(f"tags: {', '.join(str(t) for t in tags)}", file=stdout)
    abstract = record.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        print(f"\nabstract:\n{abstract.strip()}", file=stdout)
    return exit_codes.OK


def _run_stats(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    result = bib_stats(bib_path=target["path"], papers_dir=target["papers_dir"])
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="entries --stats", items=[])
        return exit_codes.OK if result["status"] == "ok" else exit_codes.ENVIRONMENT
    if result["status"] == "ok":
        print_lines(_render_bib_stats(result), stdout)
        print_read_warnings(result, stderr)
        return exit_codes.OK
    print_lines(_error_lines("stats failed", result["errors"]), stderr)
    return exit_codes.ENVIRONMENT


def _author_name(author: object) -> str:
    """Format a single author entry (plain string or CSL given/family dict)."""
    if isinstance(author, str):
        return author.strip()
    if isinstance(author, dict):
        given = str(author.get("given", "")).strip()
        family = str(author.get("family", "")).strip()
        return f"{given} {family}".strip()
    return ""
