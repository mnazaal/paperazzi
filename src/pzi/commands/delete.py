"""CLI runner for `pzi delete`."""

from __future__ import annotations

import sys

from pzi import cli_json, exit_codes
from pzi.bib_service import delete_entry
from pzi.clean_service import plan_pdf_disposal
from pzi.cli_render import error_lines, render_delete_success
from pzi.commands.common import (
    emit_usage_error,
    print_lines,
    resolve_target,
)
from pzi.errors import exit_code_for_error


def run_delete_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    as_json = getattr(args, "json", False)
    config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    # `nargs="+"` always yields a list; a caller building `Namespace` by hand
    # may still pass one string.
    raw_citekeys = [args.citekey] if isinstance(args.citekey, str) else list(args.citekey)
    # First occurrence wins: `pzi delete a a` previewed two successes and then
    # the real run deleted once and exited 4 claiming "1 not found" — for an
    # entry it had just deleted. A repeat is the same request, not a second one.
    citekeys = list(dict.fromkeys(raw_citekeys))

    if not args.force and not args.dry_run:
        # Never prompt into a pipe: reading the confirmation would eat a line of
        # the caller's data, and answering "no" for them turns a forgotten
        # --force into a silent no-op that reports success.
        if not sys.stdin.isatty():
            return emit_usage_error(
                args,
                "refusing to prompt for confirmation with stdin not a terminal; "
                "pass --force to delete or --dry-run to preview",
                command_path=("delete",),
                stdout=stdout,
                stderr=stderr,
            )
        listed = ", ".join(f"'{key}'" for key in citekeys)
        noun = "entry" if len(citekeys) == 1 else f"{len(citekeys)} entries"
        print(
            f"Delete {noun} {listed} from {target['path']}? [y/N] ",
            end="",
            file=stderr,
        )
        response = sys.stdin.readline().strip().lower()
        if response not in ("y", "yes"):
            # Declining is a result the caller has to see; emitting nothing left
            # `--json` unable to distinguish it from a successful delete.
            if as_json:
                cli_json.emit_result(
                    # `deleted` is a count on every other path; `False` here
                    # read as a fifth value to any consumer typing the field.
                    {"status": "ok", "citekeys": citekeys, "deleted": 0,
                     "message": "cancelled"},
                    stdout, command="delete", items=[], bib_name=target["name"],
                )
            else:
                print("cancelled", file=stderr)
            return exit_codes.OK

    # One entry at a time, each with its own `.bak` and its own PDF disposal.
    # Deleting them in one rewrite would make a single unparseable block cost
    # the whole batch, and would leave one backup standing for several removals.
    items: list[dict] = []
    for citekey in citekeys:
        result = dict(delete_entry(
            bib_path=target["path"],
            citekey=citekey,
            dry_run=args.dry_run,
        ))
        if result["status"] == "ok":
            # The bib write is the commit point; the PDF moves after it. A move
            # that fails therefore degrades to exactly the old behaviour — an
            # orphan the sweep still catches — rather than half-deleting.
            pdf_action = plan_pdf_disposal(
                result=result,
                config=config,
                target=target,
                keep_pdf=getattr(args, "keep_pdf", False),
                dry_run=args.dry_run,
                removed_citekeys=citekeys,
            )
            if pdf_action is not None:
                result["pdf_action"] = pdf_action
        items.append(result)

    succeeded = [item for item in items if item["status"] == "ok"]
    failed = [item for item in items if item["status"] != "ok"]

    if as_json:
        if succeeded:
            # `ok` even when some citekeys missed, as every other batch command
            # does: the run happened, `items` carries which entries went, and
            # the exit code is what separates a whole batch from a partial one.
            # `partial` is not an envelope status here — `_assert_envelope`
            # allows `ok` and `error`, and inventing a third forks the
            # classification that `.status` and `$?` are two renderings of.
            envelope: dict = {
                "status": "ok",
                "citekeys": citekeys,
                "deleted": len(succeeded),
                "message": _batch_message(succeeded, failed, dry_run=args.dry_run),
            }
        else:
            # Nothing was removed, so the envelope *is* the failure, carrying the
            # service's `reason`. A single missing citekey has to look exactly as
            # it did before `delete` learned to take several.
            envelope = {**failed[0], "citekeys": citekeys, "deleted": 0}
        cli_json.emit_result(
            envelope, stdout, command="delete", items=items, bib_name=target["name"],
        )
        return _batch_exit_code(succeeded, failed, items)

    for item in succeeded:
        print(render_delete_success(item), file=stdout)
        backup = item.get("backup_path")
        if isinstance(backup, str):
            print(f"backup saved to {backup}", file=stderr)
    for item in failed:
        print_lines(error_lines(item["message"], item.get("errors") or []), stderr)
    for item in succeeded:
        action = item.get("pdf_action")
        # The entry went; the file did not. That is a report, not a failure to
        # run — the delete the user asked for did happen.
        if isinstance(action, dict) and action.get("status") == "failed":
            print(
                f"could not quarantine {action['source']}: "
                f"{action.get('error', 'unknown error')}",
                file=stderr,
            )
    return _batch_exit_code(succeeded, failed, items)


def _batch_message(succeeded, failed, *, dry_run: bool) -> str:
    if len(succeeded) + len(failed) == 1:
        return (succeeded or failed)[0]["message"]
    verb = "would delete" if dry_run else "deleted"
    message = f"{verb} {len(succeeded)} of {len(succeeded) + len(failed)} entries"
    return message if not failed else f"{message}; {len(failed)} not found"


def _batch_exit_code(succeeded, failed, items) -> int:
    """OK, PARTIAL, or the single-entry code the old scalar form returned.

    A batch that matched nothing keeps NOT_FOUND rather than becoming PARTIAL's
    "some worked": the whole point of 3 is that a script can branch on a citekey
    that is not there, and that is exactly what happened.
    """
    if not failed:
        if any(
            isinstance(item.get("pdf_action"), dict)
            and item["pdf_action"].get("status") == "failed"
            for item in items
        ):
            return exit_codes.FINDINGS
        return exit_codes.OK
    if not succeeded:
        return exit_code_for_error(failed[0])
    return exit_codes.PARTIAL
