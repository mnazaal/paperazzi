"""CLI runner for `pzi entries` (list, single-record detail, or --stats)."""

from __future__ import annotations

import json

from pzi.bib_service import bib_stats, entry_detail, list_entries
from pzi.cli_render import _error_lines, _render_bib_stats
from pzi.commands.common import print_lines, resolve_target_or_error


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
        print(json.dumps(result, indent=2, default=str), file=stdout)
        return 0 if result["status"] == "ok" else 1
    if result["status"] == "ok":
        items = result["items"]
        if not items:
            print("(no entries)", file=stdout)
            return 0
        for item in items:
            ck = item["citekey"]
            title = item.get("title", "") or ""
            year_str = str(item["year"]) if item.get("year") else ""
            authors = item.get("authors", "")
            pdf_marker = " [PDF]" if item.get("has_pdf") else ""
            line = f"{ck}\t{year_str}\t{title}"
            if authors:
                line += f"\t{authors}"
            line += pdf_marker
            print(line, file=stdout)
        total = result["total"]
        offset = result["offset"]
        limit = result["limit"]
        shown = min(len(items), limit)
        # Summary goes to stderr so `pzi entries | cut` stays clean.
        print(
            f"{offset + 1}-{offset + shown} of {total} entries "
            f"(bib: {result['bib_name']}, sort: {result['sort']})",
            file=stderr,
        )
        return 0
    print_lines(_error_lines("failed to list entries", result["errors"]), stderr)
    return 1


def _run_detail(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    result = entry_detail(
        config_path=config_path,
        home_dir=home_dir,
        citekey=args.citekey,
        bib_selector=bib_selector,
    )
    if result["status"] != "ok":
        print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return 1
    record = result["record"]
    if getattr(args, "json", False):
        print(json.dumps(record, indent=2, default=str), file=stdout)
        return 0
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
    return 0


def _run_stats(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector, stderr=stderr,
    )
    if resolved is None:
        return 1
    _config, target = resolved

    result = bib_stats(bib_path=target["path"], papers_dir=target["papers_dir"])
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        return 0 if result["status"] == "ok" else 1
    if result["status"] == "ok":
        print_lines(_render_bib_stats(result), stdout)
        return 0
    print_lines(_error_lines("stats failed", result["errors"]), stderr)
    return 1


def _author_name(author: object) -> str:
    """Format a single author entry (plain string or CSL given/family dict)."""
    if isinstance(author, str):
        return author.strip()
    if isinstance(author, dict):
        given = str(author.get("given", "")).strip()
        family = str(author.get("family", "")).strip()
        return f"{given} {family}".strip()
    return ""
