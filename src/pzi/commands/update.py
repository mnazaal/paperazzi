"""Metadata update CLI command runner (with optional preprint promotion)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TextIO

from pzi import cli_json, exit_codes
from pzi.cli_render import (
    error_lines,
    render_bib_promote_items,
    render_bib_update_items,
)
from pzi.commands.common import (
    batch_exit_code,
    emit_usage_error,
    print_lines,
    print_metadata_diagnostics,
    print_metadata_warnings,
    print_result_item_diffs,
    target_list,
)
from pzi.promote_service import promote_bib
from pzi.update_service import update_bib

Result = Mapping[str, Any]
Service = Callable[..., Result]


#: Below this many candidates a run finishes fast enough that progress would be
#: noise — and this CLI's convention, asserted across a dozen tests, is that a
#: successful command prints nothing to stderr. Above it, silence is the problem:
#: `promote` spends ~6 s per candidate waiting out the providers' polite
#: intervals, so a 100-candidate run is ten minutes.
_PROGRESS_MIN_CANDIDATES = 10


def _progress_printer(stderr: TextIO):
    """Report each verdict as it is reached, for a run long enough to need it.

    One line per candidate rather than `check`'s every-25: `check` audits an
    entry in well under a second, `promote` takes seconds on each, so a line per
    candidate is about a line every six seconds — a readable pace, and the only
    thing that shows a long sweep is alive rather than hung.
    """

    def report(item: Mapping[str, Any], done: int, total: int) -> None:
        if total < _PROGRESS_MIN_CANDIDATES:
            return
        note = item.get("note") or item.get("action") or ""
        citekey = item.get("preprint_citekey") or "?"
        print(f"  [{done}/{total}] {citekey}: {note}", file=stderr)

    return report


def run_update_command(
    args,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    update_bib_fn: Service = update_bib,
    promote_bib_fn: Service = promote_bib,
) -> int:
    """Run `pzi update`, dispatching to promotion when --promote is given.

    Without --promote, conservatively fills missing metadata.  With --promote,
    replaces preprints with their published versions (keeping both by default,
    or in place with --replace).
    """
    promote = getattr(args, "promote", False)
    if getattr(args, "replace", False) and not promote:
        return emit_usage_error(
            args, "--replace only applies with --promote",
            command_path=("update",), stdout=stdout, stderr=stderr,
        )
    mark_resolved = getattr(args, "mark_resolved", False)
    if mark_resolved and not promote:
        return emit_usage_error(
            args, "--mark-resolved only applies with --promote",
            command_path=("update",), stdout=stdout, stderr=stderr,
        )

    limit: int | None = getattr(args, "limit", None)
    if limit is not None and not promote:
        return emit_usage_error(
            args, "--limit only applies with --promote",
            command_path=("update",), stdout=stdout, stderr=stderr,
        )
    if limit is not None and limit < 1:
        return emit_usage_error(
            args, "--limit must be at least 1",
            command_path=("update",), stdout=stdout, stderr=stderr,
        )

    as_json = getattr(args, "json", False)
    ok = True
    items_succeeded = 0
    items_failed = 0
    collected: list[tuple[str, Mapping[str, Any]]] = []
    for target in target_list(args.target):
        if promote:
            result = promote_bib_fn(
                config_path=config_path,
                home_dir=home_dir,
                bib_selector=target,
                dry_run=args.dry_run,
                keep_preprint=not args.replace,
                mark_resolved=mark_resolved,
                limit=limit,
                on_item=None if as_json else _progress_printer(stderr),
            )
            render = render_bib_promote_items
            failure = "promote failed"
        else:
            result = update_bib_fn(
                config_path=config_path,
                home_dir=home_dir,
                bib_selector=target,
                dry_run=args.dry_run,
            )
            render = render_bib_update_items
            failure = "update failed"

        if result["status"] != "ok":
            ok = False
        # A record the run could not update is a partly-failed batch. Failures
        # used to survive only as free text in each item's `note`, which nothing
        # read, so a run where every record failed still exited 0.
        for item in result.get("items") or []:
            if item.get("failed"):
                items_failed += 1
            else:
                items_succeeded += 1

        collected.append((target or "default", dict(result)))
        if as_json:
            continue

        if result["status"] == "ok":
            print_lines(render(result), stdout)
            remaining = (result.get("summary") or {}).get("remaining") or 0
            if remaining:
                # A bounded pass that did not say this is indistinguishable from
                # a full one that found nothing more to do.
                print(
                    f"note: {remaining} preprints not checked in this run — "
                    f"re-run to continue",
                    file=stderr,
                )
            if args.dry_run:
                print_result_item_diffs(result, stdout)
            print_metadata_warnings(result, stderr)
            if args.verbose:
                print_metadata_diagnostics(result, stdout)
        else:
            # Name the failing target, as `search` does.
            label = result.get("bib_name") or target or "default"
            print_lines(error_lines(f"{failure} ({label})", result["errors"]), stderr)

    if as_json:
        # One document for the whole run, the same shape whether or not
        # --promote was passed, built by the shared merge so nothing the
        # service reported is dropped — the hand-built envelope here never
        # copied `summary`, which is where promotion's provider_errors live.
        merged = cli_json.merge_target_results(
            collected, command="update --promote" if promote else "update"
        )
        merged["dry_run"] = bool(args.dry_run)
        merged["promote"] = bool(promote)
        cli_json.emit_result(
            merged,
            stdout,
            command="update --promote" if promote else "update",
            items=merged["items"],
        )
    if not ok:
        return exit_codes.ENVIRONMENT
    # The shared batch contract — see `batch_exit_code`.
    return batch_exit_code(succeeded=items_succeeded, failed=items_failed)
