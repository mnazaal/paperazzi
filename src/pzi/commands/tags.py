"""Tag CLI command runner."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.cli_render import error_lines, render_tag_mutation_success
from pzi.commands.common import exit_code_for_error, print_lines, print_read_warnings
from pzi.tag_service import add_tags, list_tags, parse_tag_csv, remove_tags

TagService = Callable[..., Mapping[str, Any]]
TagParser = Callable[[str], list[str]]


def run_tag_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    list_tags_fn: TagService = list_tags,
    add_tags_fn: TagService = add_tags,
    remove_tags_fn: TagService = remove_tags,
    parse_tag_csv_fn: TagParser = parse_tag_csv,
) -> int:
    """Run `pzi tag` using injected services for thin-I/O testing."""
    if args.tag_command == "list":
        result = list_tags_fn(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
        )
        if getattr(args, "json", False):
            cli_json.emit_result(result, stdout, command="tag list", items=result.get("tags"))
            if result["status"] == "ok":
                return exit_codes.OK
            return exit_code_for_error(result)
        if result["status"] == "ok":
            print_read_warnings(result, stderr)
            for tag in result["tags"]:
                print(tag, file=stdout)
            return exit_codes.OK
        print_lines(error_lines("failed to list tags", result["errors"]), stderr)
        return exit_code_for_error(result)

    flat_tags = [tag for raw in args.tags for tag in parse_tag_csv_fn(raw)]
    service = add_tags_fn if args.tag_command == "add" else remove_tags_fn
    result = service(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
        tags=flat_tags,
        dry_run=args.dry_run,
    )
    # `tag list` above already emits the envelope; these two used the raw
    # serializer, so a mutation's JSON had no `command` and no `.items[]`.
    mutation_command = f"tag {args.tag_command}"
    if result["status"] == "ok":
        if getattr(args, "json", False):
            cli_json.emit_result(
                result, stdout, command=mutation_command, items=result.get("tags") or [],
            )
        else:
            print(render_tag_mutation_success(result), file=stdout)
        return exit_codes.OK
    if getattr(args, "json", False):
        cli_json.emit_result(
            result, stdout, command=mutation_command, items=result.get("tags") or [],
        )
    else:
        print_lines(error_lines(result["message"], result["errors"]), stderr)
    return exit_code_for_error(result)
