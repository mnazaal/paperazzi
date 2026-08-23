"""CLI runner for `pzi entries` (list, single-record detail, or --stats)."""

from __future__ import annotations

from pzi import cli_json, exit_codes
from pzi.bib_service import bib_stats, clamp_limit, entry_detail, list_entries
from pzi.cli_render import error_lines, render_bib_stats, render_cell
from pzi.commands.common import (
    emit_usage_error,
    has_read_warnings,
    print_lines,
    print_read_warnings,
    resolve_target,
)
from pzi.errors import exit_code_for_error

#: Flags that describe *which page of a list* to show. `entries <citekey>` and
#: `entries --stats` produce no list, so they parsed these and did nothing —
#: which reads as a working filter that silently is not one.
_LIST_ONLY_FLAGS = (("offset", "--offset"), ("limit", "--limit"), ("sort", "--sort"))


def _list_only_flag_used(args) -> str | None:
    for attr, flag in _LIST_ONLY_FLAGS:
        if getattr(args, attr, None) is not None:
            return flag
    return None


def run_entries_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if getattr(args, "stats", False) or getattr(args, "citekey", None):
        used = _list_only_flag_used(args)
        if used is not None:
            subject = "--stats" if getattr(args, "stats", False) else "a citekey"
            return emit_usage_error(
                args,
                f"{used} selects a page of the entry list and cannot be combined "
                f"with {subject}",
                command_path=("entries",),
                stdout=stdout,
                stderr=stderr,
            )
    if getattr(args, "stats", False):
        if getattr(args, "citekey", None):
            # `--stats` summarizes the whole library, so a citekey alongside it
            # asks for two different things. It used to be discarded silently:
            # `pzi entries smith2024 --stats` printed library-wide statistics
            # under a command line that named one entry.
            return emit_usage_error(
                args,
                "--stats summarizes the library and cannot be combined with a citekey",
                command_path=("entries",),
                stdout=stdout,
                stderr=stderr,
            )
        return _run_stats(args, home_dir, config_path, stdout, stderr, bib_selector)
    if getattr(args, "citekey", None):
        return _run_detail(args, home_dir, config_path, stdout, stderr, bib_selector)
    return _run_list(args, home_dir, config_path, stdout, stderr, bib_selector)


def _run_list(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    result = list_entries(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        offset=max(0, args.offset if args.offset is not None else 0),
        limit=clamp_limit(args.limit),
        sort=args.sort if args.sort is not None else "citekey",
    )
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="entries")
        if result["status"] != "ok":
            return exit_codes.ENVIRONMENT
        # The same verdict the text branch below reaches. `--json` is a
        # rendering choice, and computing the code twice is how the two answers
        # drift apart.
        return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK
    if result["status"] == "ok":
        items = result["items"]
        if not items:
            # Name the total even when this page is empty: `--offset` past the
            # end printed a bare "(no entries)", indistinguishable from an empty
            # library. `--json` has always carried the total.
            total = result.get("total", 0)
            if total:
                print(
                    f"(no entries at offset {result.get('offset', 0)}; "
                    f"{total} in {result['bib_name']})",
                    file=stderr,
                )
            else:
                print("(no entries)", file=stderr)
            # Before the summary path's call, because this branch returns first:
            # a library that read as empty *because the parser dropped every
            # block*, or because the file is not there at all, reported a bare
            # "(no entries)" and exit 0 — the one case where the warnings matter
            # most was the one case that never printed them.
            print_read_warnings(result, stderr)
            return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK
        for item in items:
            ck = item["citekey"]
            title = item.get("title", "") or ""
            year_str = str(item["year"]) if item.get("year") else ""
            authors = "; ".join(item.get("authors") or [])
            # Fixed five tab-separated columns, always. The PDF flag used to be
            # glued onto the authors column without a separator, so awk -F'\t'
            # read it as part of an author name.
            has_pdf = "pdf" if item.get("has_pdf") else ""
            # Through `render_cell`: a tab in a captured title shifted every
            # later column, and a newline invented an entire extra row that
            # a script reading this would parse as another entry.
            print(
                f"{render_cell(ck)}\t{render_cell(year_str)}\t{render_cell(title)}\t"
                f"{render_cell(authors)}\t{render_cell(has_pdf)}",
                file=stdout,
            )
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
        return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK
    print_lines(error_lines("failed to list entries", result["errors"]), stderr)
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
            print_lines(error_lines(result["message"], result["errors"]), stderr)
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
        return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK
    print(f"citekey: {record.get('citekey', '')}", file=stdout)
    print(f"title: {record.get('title') or ''}", file=stdout)
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
    return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK


def _run_stats(args, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    result = bib_stats(bib_path=target["path"], papers_dir=target["papers_dir"])
    if getattr(args, "json", False):
        # `bib_stats` takes a path rather than a selector, so it cannot name the
        # library; the runner resolved the target to call it and can. This was
        # the seventh call site of a fix the other six got, so the envelope here
        # carried `"bib_name": null` permanently against a README documenting it
        # as a real value.
        cli_json.emit_result(
            result, stdout, command="entries --stats", items=[],
            bib_name=target["name"],
        )
        if result["status"] != "ok":
            return exit_codes.ENVIRONMENT
        return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK
    if result["status"] == "ok":
        print_lines(render_bib_stats(result), stdout)
        print_read_warnings(result, stderr)
        return exit_codes.FINDINGS if has_read_warnings(result) else exit_codes.OK
    print_lines(error_lines("stats failed", result["errors"]), stderr)
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
