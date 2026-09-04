"""Search CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TextIO

from pzi import cli_json, exit_codes
from pzi.cli_render import error_lines, render_search_matches
from pzi.commands.common import (
    emit_usage_error,
    print_lines,
    print_read_warnings,
    target_list,
)
from pzi.errors import exit_code_for_error
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
    if not any((args.query, args.author, args.year, args.tag, args.venue, args.doi)):
        return emit_usage_error(
            args,
            "at least one of --query, --author, --year, --tag, --venue, --doi is required",
            command_path=("search",),
            stdout=stdout,
            stderr=stderr,
        )

    as_json = getattr(args, "json", False)
    ok = True
    found_any = False
    #: The first failure's exit code, from the shared `reason` mapper. Returning
    #: a hardcoded ENVIRONMENT meant a tag that normalizes to nothing — which
    #: `search_service` classifies as REASON_USAGE — exited 5 while the emitted
    #: envelope said `"reason": "usage"`, and while the sibling usage check a few
    #: lines above exited 2 for the same class of mistake.
    failure_code: int | None = None
    collected: list[tuple[str, dict]] = []
    for target in target_list(bib_selector):
        result = search_bib_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            query=args.query,
            author=args.author,
            year=args.year,
            tag=args.tag,
            venue=args.venue,
            doi=args.doi,
            sort=getattr(args, "sort", None),
            offset=getattr(args, "offset", 0) or 0,
            limit=getattr(args, "limit", None),
        )
        if result["status"] != "ok":
            ok = False
            if failure_code is None:
                failure_code = exit_code_for_error(result)
        if result.get("matches"):
            found_any = True
        collected.append((target or "default", dict(result)))
        if as_json:
            continue
        if result["status"] == "ok":
            print_lines(render_search_matches(result), stdout)
            print_read_warnings(result, stderr)
            shown = len(result.get("matches", []))
            total = result.get("total", shown)
            if shown < total:
                # Only when a page actually hid something. The counts go to
                # stderr so a paged `pzi search ... | wc -l` still counts rows.
                print(
                    f"showing {shown} of {total} matches "
                    f"(--offset {result.get('offset', 0)}); raise --limit for more",
                    file=stderr,
                )
            if not result.get("matches"):
                print("no matches", file=stderr)
        else:
            # Name the target: with `--target` repeated, "search failed" with no
            # library named leaves the user to guess which one.
            label = result.get("bib_name") or target or "default"
            print_lines(error_lines(f"search failed ({label})", result["errors"]), stderr)
    if as_json:
        # One document for the whole run, not one per library, built by the
        # shared merge so nothing the service reported is dropped — the
        # hand-built envelope here silently lost `warnings`.
        merged = cli_json.merge_target_results(collected, command="search")
        cli_json.emit_result(
            merged, stdout, command="search", items=merged["items"],
        )
    if not ok:
        return failure_code if failure_code is not None else exit_codes.ENVIRONMENT
    # grep's convention: nothing matched is a reportable outcome, not an error.
    return exit_codes.OK if found_any else exit_codes.FINDINGS
