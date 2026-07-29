"""Search CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.cli_render import _error_lines, _render_search_matches
from pzi.commands.common import emit_usage_error, print_lines, target_list
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
        return emit_usage_error(
            args,
            "at least one of --query, --author, --year, --tag is required",
            command_path=("search",),
            stdout=stdout,
            stderr=stderr,
        )

    as_json = getattr(args, "json", False)
    ok = True
    found_any = False
    all_matches: list[dict] = []
    all_errors: list[str] = []
    searched_bibs: list[str] = []
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
        if result.get("matches"):
            found_any = True
        if as_json:
            # One document for the whole run, not one per library: `--target`
            # may be repeated, and a consumer should not have to branch on how
            # many were searched. Each match carries its own bib_name.
            bib_name = result.get("bib_name")
            if isinstance(bib_name, str):
                searched_bibs.append(bib_name)
            for match in result.get("matches") or []:
                all_matches.append({**match, "bib_name": bib_name})
            all_errors.extend(result.get("errors") or [])
        elif result["status"] == "ok":
            print_lines(_render_search_matches(result), stdout)
            if not result.get("matches"):
                print("no matches", file=stderr)
        else:
            print_lines(_error_lines("search failed", result["errors"]), stderr)
    if as_json:
        cli_json.emit_result(
            {
                "status": "ok" if ok else "error",
                "bib_name": ", ".join(searched_bibs) if searched_bibs else None,
                "errors": all_errors,
                "searched_bibs": searched_bibs,
            },
            stdout,
            command="search",
            items=all_matches,
        )
    if not ok:
        return exit_codes.ENVIRONMENT
    # grep's convention: nothing matched is a reportable outcome, not an error.
    return exit_codes.OK if found_any else exit_codes.FINDINGS
