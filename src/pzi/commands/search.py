"""Search CLI command runner."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import TextIO

from pzi.cli_render import _error_lines, _render_search_matches
from pzi.commands.common import print_lines, target_list
from pzi.search_service import SearchResult, search_bib

SearchService = Callable[..., SearchResult]


def run_search_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: Sequence[str] | None,
    search_bib_fn: SearchService = search_bib,
) -> int:
    """Run `pzi search` using injected service for thin-I/O testing."""
    if not any((args.query, args.author, args.year, args.tag)):
        print("error: at least one of --query, --author, --year, --tag is required", file=stderr)
        return 1

    as_json = getattr(args, "json", False)
    ok = True
    json_results = []
    for target in target_list(bib_selector):
        result = search_bib_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            query=args.query,
            author=args.author,
            year=args.year,
            tag=args.tag,
        )
        if result["status"] != "ok":
            ok = False
        if as_json:
            json_results.append(result)
        elif result["status"] == "ok":
            print_lines(_render_search_matches(result), stdout)
        else:
            print_lines(_error_lines("search failed", result["errors"]), stderr)
    if as_json:
        print(json.dumps(json_results, indent=2, default=str), file=stdout)
    return 0 if ok else 1
