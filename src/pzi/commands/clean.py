"""CLI runner for `pzi clean`."""

from __future__ import annotations

import os

from pzi import cli_json, exit_codes
from pzi.clean_service import clean_library, validate_library
from pzi.cli_render import error_lines, render_clean_result
from pzi.commands.common import (
    emit_usage_error,
    has_read_warnings,
    print_lines,
    print_read_warnings,
    resolve_target,
)


def _siblings_sharing_papers_dir(config, target) -> list[str]:
    """The other configured libraries storing PDFs in *target*'s papers directory.

    The default layout gives every bib the same `papers_dir`, so without this a
    check of one library reported the others' PDFs as orphans — and `--fix`
    quarantined them, breaking `file =` fields in a library the user never named.
    """
    shared = os.path.realpath(target["papers_dir"])
    return [
        bib["path"]
        for bib in config.get("bibs", [])
        if bib["path"] != target["path"]
        and os.path.realpath(bib["papers_dir"]) == shared
    ]


def run_clean_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    if getattr(args, "dry_run", False) and not args.fix:
        # `--dry-run` previews what `--fix` would do. Without `--fix` the
        # command is already read-only, so the flag was accepted and ignored.
        return emit_usage_error(
            args,
            "--dry-run previews --fix; without it the run is already read-only",
            command_path=("library", "clean"),
            stdout=stdout,
            stderr=stderr,
        )

    siblings = _siblings_sharing_papers_dir(_config, target)
    if args.fix:
        result = clean_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
            dry_run=args.dry_run, sibling_bib_paths=siblings,
        )
    else:
        result = validate_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
            sibling_bib_paths=siblings,
        )

    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="library clean", bib_name=target["name"])
        return _verdict(result)

    if result["status"] != "ok":
        # A finding, not a failure to run: the audit *ran*, and what it found is
        # that part of the file could not be read. 5 said "could not run", which
        # was false, and it suppressed the reason — a `--json` caller was told
        # the block and the line number while a terminal user got `clean failed`
        # and nothing else, from the same invocation.
        #
        # Both facts are printed, because only the first is a report. The PDF,
        # orphan and duplicate checks did **not** run: `validate_library` returns
        # early on a parse failure, deliberately, since a dropped entry
        # contributes no referenced path and its PDF would then look orphaned to
        # `--fix`. So their empty lists mean "not checked", and saying so is the
        # difference between a finding and a clean bill of health for a library
        # this run never read.
        print_lines(
            error_lines(
                "library only partly read", result.get("errors") or ["unparseable library"]
            ),
            stderr,
        )
        print(
            "the PDF, orphan and duplicate checks did not run — fix the block "
            "above and re-run to get them",
            file=stderr,
        )
        return _verdict(result)

    print_read_warnings(result, stderr)
    print_lines(render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return _verdict(result)


def _verdict(result) -> int:
    """Findings, a partial read, or a clean library — one answer for both formats.

    A read the parser could only partly complete is a finding: the issue list
    below it was computed from the blocks that *did* parse, so "no issues found"
    at exit 0 was a clean bill of health for a library this run had not read.

    Since 2026-08-23 this is also the answer for an *unparseable block*, which
    used to return 5. `status != "ok"` there means the audit ran and could not
    read all of the file — a finding. 5 is reserved for a run that could not
    happen at all (no config, no library, unreadable target), which
    `resolve_target` raises before this point.
    """
    if result.get("issues") or has_read_warnings(result):
        return exit_codes.FINDINGS
    return exit_codes.OK
