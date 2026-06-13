"""Tag CLI command runner."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TextIO

from pzi.cli_render import _error_lines, _render_tag_mutation_success
from pzi.commands.common import print_lines
from pzi.tag_service import add_tags, list_tags, parse_tag_csv, remove_tags

TagService = Callable[..., dict[str, Any]]
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
            print(json.dumps(result, indent=2, default=str), file=stdout)
            return 0 if result["status"] == "ok" else 1
        if result["status"] == "ok":
            for tag in result["tags"]:
                print(tag, file=stdout)
            return 0
        print_lines(_error_lines("failed to list tags", result["errors"]), stderr)
        return 1

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
    if result["status"] == "ok":
        print(_render_tag_mutation_success(result), file=stdout)
        return 0
    print_lines(_error_lines(result["message"], result["errors"]), stderr)
    return 1
