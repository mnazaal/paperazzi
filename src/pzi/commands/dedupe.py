"""CLI runners for duplicate detection and merge commands."""

from __future__ import annotations

from pzi import cli_json, exit_codes
from pzi.clean_service import plan_pdf_disposal
from pzi.cli_render import describe_pdf_disposal, error_lines, render_dedupe_result
from pzi.commands.common import (
    has_read_warnings,
    print_lines,
    print_read_warnings,
    resolve_target,
)
from pzi.dedupe_service import find_duplicates, merge_duplicates
from pzi.errors import exit_code_for_error


def run_dedupe_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    _config, target = resolve_target(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )

    result = find_duplicates(bib_path=target["path"])
    if result.get("status") not in (None, "ok"):
        # Nothing inspected `status` at all, so a failed detection ran on into
        # `render_dedupe_result`, which indexes `total_entries` directly and
        # landed in `cli.py`'s `KeyError` net — "internal error: result is
        # missing the key" for a condition the service had already named.
        if getattr(args, "json", False):
            cli_json.emit_result(
                result, stdout, command="library dedupe", bib_name=target["name"]
            )
        else:
            print_lines(error_lines("dedupe failed", result.get("errors") or []), stderr)
        return exit_code_for_error(result)
    # `total_clusters` counts exact clusters only, so it cannot stand in for
    # "has something to report" — a library whose sole finding is a fuzzy
    # near-duplicate still owes the caller exit 1. A duplicate citekey never
    # reaches the identity index at all (the parser keeps only the first block),
    # so a partial read is a finding too: without it the command built to find
    # duplicates reported "0 clusters", exit 0, for a file that plainly has one.
    findings = (
        result.get("total_clusters", 0)
        + len(result.get("fuzzy_candidates", []))
        or has_read_warnings(result)
    )
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="library dedupe", bib_name=target["name"])
        return exit_codes.OK if not findings else exit_codes.FINDINGS
    print_lines(render_dedupe_result(result), stdout)
    print_read_warnings(result, stderr)
    return exit_codes.OK if not findings else exit_codes.FINDINGS


def run_merge_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    config, target = resolve_target(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )

    result = merge_duplicates(
        bib_path=target["path"],
        citekey_a=args.citekey_a,
        citekey_b=args.citekey_b,
        dry_run=getattr(args, "dry_run", False),
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )
    # Same disposal step as `delete`: the bib write is the commit point and the
    # dropped entry's PDF moves after it. Shared so the hazard checked in one
    # command cannot go unchecked in the other.
    if result["status"] == "ok":
        pdf_action = plan_pdf_disposal(
            result=result,
            config=config,
            target=target,
            keep_pdf=getattr(args, "keep_pdf", False),
            dry_run=getattr(args, "dry_run", False),
        )
        if pdf_action is not None:
            result["pdf_action"] = pdf_action

    if getattr(args, "json", False):
        cli_json.emit_result(
            result, stdout, command="library merge", items=[], bib_name=target["name"]
        )
        # Through the same verdict helper as the text branch below: returning
        # plain OK here made `--json` exit 0 on a failed quarantine while the
        # identical text invocation exited 1 — under the very comment saying the
        # two branches cannot drift apart.
        if result["status"] != "ok":
            return exit_code_for_error(result)
        return _merge_verdict(result)
    if result["status"] != "ok":
        print_lines(error_lines(result["message"], []), stderr)
        # Both branches go through the same mapper so they cannot drift apart
        # the way `pdf retry --failed-only`'s JSON and text paths did.
        return exit_code_for_error(result)
    print(result["message"], file=stdout)
    # Name what happens to the fields the record model cannot show. In a dry run
    # this is the only place the user can learn what the merge costs.
    carried = result.get("carried_fields") or []
    if carried:
        print(f"  fields carried from {result['citekey_a']}: {', '.join(carried)}",
              file=stdout)
    conflicting = result.get("dropped_fields") or []
    if conflicting:
        print(
            f"  fields kept from {result['citekey_b']} (conflict): "
            f"{', '.join(conflicting)}",
            file=stdout,
        )
    # The survivor's own losses. Merge prefers the longer string for title,
    # venue and abstract, so these were previously printed under "kept from
    # <survivor>" — naming the loss as though it were a preservation.
    overwritten = result.get("overwritten_fields") or []
    if overwritten:
        print(
            f"  fields on {result['citekey_b']} overwritten by "
            f"{result['citekey_a']}: {', '.join(overwritten)}",
            file=stdout,
        )
    # The dropped entry's PDF, which the merge does not keep. It used to be
    # named here and left on disk for a later `library clean --fix`; the clause
    # now reports what this command did with it.
    if result.get("orphaned_pdf"):
        clause = describe_pdf_disposal(result)
        print(f"  PDF orphaned (kept by neither entry), {clause}", file=stdout)
    backup = result.get("backup_path")
    if backup:
        print(f"  backup: {backup}", file=stdout)
    action = result.get("pdf_action")
    if isinstance(action, dict) and action.get("status") == "failed":
        print(
            f"could not quarantine {action['source']}: "
            f"{action.get('error', 'unknown error')}",
            file=stderr,
        )
    return _merge_verdict(result)


def _merge_verdict(result) -> int:
    """OK, or FINDINGS when the merge succeeded but its PDF move did not.

    The entry went; the file did not. That is a report, not a failure to run —
    the merge the user asked for did happen. Shared by the JSON and text
    branches so the two exits cannot disagree about one result.
    """
    action = result.get("pdf_action")
    if isinstance(action, dict) and action.get("status") == "failed":
        return exit_codes.FINDINGS
    return exit_codes.OK
