"""CLI runner for `pzi library reindex`."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

from pzi import cli_json, exit_codes
from pzi.bib_repository import _resolve_write_target
from pzi.clean_service import siblings_sharing_papers_dir
from pzi.cli_render import reindex_error_lines, render_reindex_result
from pzi.commands.common import (
    emit_usage_error,
    has_read_warnings,
    print_lines,
    print_read_warnings,
    resolve_target,
)
from pzi.reindex_service import (
    convert_file_paths,
    reindex_library,
    rename_files_to_policy,
)


def _check_the_run_could_lock(bib_path: str) -> None:
    """Raise what the run's exclusive lock would raise, without creating the lock.

    A preview takes only the shared lock, which tolerates a folder where
    `<bib>.lock` cannot be created; the run takes the exclusive lock, which
    does not, and exits 5. So in a read-only folder the preview reported a plan
    for a run that cannot start. Probed with `os.access` rather than by opening
    the file, so the preview still creates nothing.

    The run's lock creates a missing folder (`mkdir(parents=True)`), so a bib
    whose folder does not exist yet is judged by the nearest folder that does.
    """
    lock_path = Path(str(_resolve_write_target(bib_path)) + ".lock")
    if lock_path.exists():
        usable = os.access(lock_path, os.W_OK)
    else:
        folder = lock_path.parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        usable = os.access(folder, os.W_OK | os.X_OK)
    if not usable:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(lock_path))


def _check_the_run_could_write(bib_path: str) -> None:
    """Raise what a run that changes the bib would raise in a read-only folder.

    The lock check above passes once `<bib>.lock` exists, which any earlier pzi
    write leaves behind. The run then takes its lock but cannot create its
    backup or temp file beside the bib, and exits 5. Called only for a preview
    that plans a change: a run with nothing to change writes nothing there.
    """
    folder = Path(str(_resolve_write_target(bib_path))).parent
    if not os.access(folder, os.W_OK | os.X_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(folder))


def _check_the_run_could_rename(changes: list[dict]) -> None:
    """Raise what `--rename-files` would raise in a read-only `papers_dir`.

    The run links each PDF to its new name and unlinks the old one, which
    needs both folders writable and searchable; otherwise it exits 5. The
    preview checked only the bib's folder, so it reported the renames and
    exited 1.
    """
    folders = {
        Path(change[key]).parent for change in changes for key in ("old_pdf", "new_pdf")
    }
    for folder in sorted(folders):
        if not os.access(folder, os.W_OK | os.X_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(folder))


def run_reindex_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    config, target = resolve_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    as_json = getattr(args, "json", False)
    rename = getattr(args, "rename_citekeys", False)
    rename_files = getattr(args, "rename_files", False)
    convert_paths = getattr(args, "convert_file_paths", False)
    passes_requested = sum((rename, rename_files, convert_paths))
    if passes_requested > 1:
        return emit_usage_error(
            args,
            "--rename-citekeys, --rename-files and --convert-file-paths are "
            "separate passes; run one, then the other",
            command_path=("library", "reindex"),
            stdout=stdout,
            stderr=stderr,
        )
    if getattr(args, "force", False) and (rename_files or convert_paths):
        # `--force` confirms the citekey rewrite and nothing else. Accepted and
        # ignored here, it reads as a safety these passes do not have, so the
        # refusal matches the bare audit's.
        return emit_usage_error(
            args,
            "--force applies to --rename-citekeys; "
            + ("--rename-files" if rename_files else "--convert-file-paths")
            + " does not use it",
            command_path=("library", "reindex"),
            stdout=stdout,
            stderr=stderr,
        )
    if getattr(args, "dry_run", False) and passes_requested:
        # Only a preview of a pass that writes; the bare audit previews nothing.
        _check_the_run_could_lock(target["path"])
    if convert_paths:
        # A one-time pass, distinct from the two above: it does not touch
        # citekeys or filenames, only folds an already-written `file =`
        # field to the configured `pdf_file_path_style`. Every other write
        # path (add, import, tag, promote, this command's own citekey pass)
        # already applies the style going forward — this exists only to
        # catch up entries written before the style changed or was set.
        style = config["pdf_file_path_style"]
        result = convert_file_paths(
            bib_path=target["path"],
            file_path_style=style,
            dry_run=getattr(args, "dry_run", False),
        )
        if getattr(args, "dry_run", False) and result["to_convert"]:
            _check_the_run_could_write(target["path"])
        if as_json:
            cli_json.emit_result(
                result, stdout, command="library reindex",
                items=result.get("converted") or [], bib_name=target["name"],
            )
        else:
            print_read_warnings(result, stderr)
            verb = "would convert" if getattr(args, "dry_run", False) else "converted"
            print(
                f"{verb} {result['to_convert']} file field(s) to {style!r}",
                file=stdout,
            )
            for change in result["converted"]:
                print(f"  {change['citekey']}: {change['old']} -> {change['new']}",
                      file=stdout)
            left_alone = result.get("left_alone") or []
            if left_alone:
                print(f"  ({len(left_alone)} field(s) left alone:)", file=stderr)
                for item in left_alone:
                    print(f"    {item['citekey']}: {item['reason']}", file=stderr)
            for error in result["errors"]:
                print(f"  {error}", file=stderr)
            backup = result.get("backup_path")
            if backup:
                print(f"backup saved to {backup}", file=stderr)
        return (
            exit_codes.FINDINGS
            if result["to_convert"] or result["errors"] or has_read_warnings(result)
            else exit_codes.OK
        )
    if rename_files:
        # A distinct pass, deliberately: bare `reindex` keeps reporting citekeys
        # only. Folding filename findings into the default output would have
        # added ~10.7k lines on a real library, for names that differ from the
        # template purely in case and are not defects.
        result = rename_files_to_policy(
            bib_path=target["path"],
            papers_dir=target["papers_dir"],
            pdf_filename_format=config.get("pdf_filename_format"),
            dry_run=getattr(args, "dry_run", False),
            include_all=getattr(args, "all", False),
            file_path_style=config["pdf_file_path_style"],
            sibling_bib_paths=siblings_sharing_papers_dir(config, target),
        )
        if getattr(args, "dry_run", False) and result["changed"]:
            _check_the_run_could_write(target["path"])
            _check_the_run_could_rename(result["changed"])
        if as_json:
            cli_json.emit_result(
                result, stdout, command="library reindex",
                items=result.get("changed") or [], bib_name=target["name"],
            )
        else:
            print_read_warnings(result, stderr)
            verb = "would rename" if getattr(args, "dry_run", False) else "renamed"
            print(f"{verb} {len(result['changed'])} PDF(s)", file=stdout)
            for change in result["changed"]:
                print(f"  {change['citekey']}: {Path(change['new_pdf']).name}",
                      file=stdout)
            skipped_cosmetic = result.get("skipped_cosmetic") or 0
            if skipped_cosmetic and not getattr(args, "all", False):
                print(
                    f"  ({skipped_cosmetic} name(s) differ only "
                    "cosmetically and were left alone; --all includes them)",
                    file=stderr,
                )
            for error in result["errors"]:
                print(f"  {error}", file=stderr)
            backup = result.get("backup_path")
            if backup:
                print(f"backup saved to {backup}", file=stderr)
        return (
            exit_codes.FINDINGS
            if result["changed"] or result["errors"] or has_read_warnings(result)
            else exit_codes.OK
        )

    for flag, given in (
        ("--force", getattr(args, "force", False)),
        ("--dry-run", getattr(args, "dry_run", False)),
    ):
        if given and not rename:
            # Without `--rename-citekeys` the run is already a read-only audit,
            # so both flags are accepted and do nothing — and `--dry-run`
            # especially reads as "I have made this safe" when it changed
            # nothing about what the command was going to do anyway.
            return emit_usage_error(
                args,
                f"{flag} applies to --rename-citekeys; without it the run is "
                f"already a read-only audit",
                command_path=("library", "reindex"),
                stdout=stdout,
                stderr=stderr,
            )
    # Default is a read-only audit: keep citekeys stable unless explicitly asked.
    apply = rename and not args.dry_run
    # No configured format means the *built-in* scheme (author+year+title) will
    # be used. For a library imported from Zotero or Mendeley that rewrites
    # every key to something the user never chose — worth saying before they
    # answer the prompt, not after.
    no_format = not config.get("citekey_format")
    if apply:
        print(
            "warning: rewriting citekeys will break any \\cite{} references that use "
            "the old keys (in LaTeX documents, notes, etc.).",
            file=stderr,
        )
        if no_format:
            print(
                "warning: no citekey_format is configured, so pzi's built-in "
                "scheme (author + year + title) will be used. Set citekey_format "
                "in config.toml first if you want a different one.",
                file=stderr,
            )
    if apply and not getattr(args, "force", False):
        # Same gate as `delete`: this rewrites every citekey in the library and
        # the damage lands outside pzi, in whatever cites them. Never prompt
        # into a pipe — reading the answer would eat a line of the caller's
        # data, and answering "no" for them turns a forgotten --force into a
        # silent no-op reported as success.
        if not sys.stdin.isatty():
            return emit_usage_error(
                args,
                "refusing to prompt for confirmation with stdin not a terminal; "
                "pass --force to rewrite citekeys or --dry-run to preview"
                + (
                    " (note: no citekey_format is configured, so the built-in "
                    "author+year+title scheme would be used)"
                    if no_format
                    else ""
                ),
                command_path=("library", "reindex"),
                stdout=stdout,
                stderr=stderr,
            )
        scheme = "the built-in scheme" if no_format else "citekey_format"
        print(
            f"Rewrite every citekey in {target['path']} using {scheme}? [y/N] ",
            end="",
            file=stderr,
        )
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            if as_json:
                cli_json.emit_result(
                    {"status": "ok", "bib_path": target["path"], "message": "cancelled"},
                    stdout, command="library reindex", items=[], bib_name=target["name"],
                )
            else:
                print("cancelled", file=stderr)
            return exit_codes.OK

    result = reindex_library(
        bib_path=target["path"],
        papers_dir=target["papers_dir"],
        citekey_format=config.get("citekey_format"),
        pdf_filename_format=config.get("pdf_filename_format"),
        dry_run=not apply,
        file_path_style=config["pdf_file_path_style"],
        sibling_bib_paths=siblings_sharing_papers_dir(config, target),
    )
    if rename and not apply and result.get("changed"):
        _check_the_run_could_write(target["path"])

    # Computed once above the format branch, as `library dedupe` does. Keying only
    # off `errors` meant a read-only audit that found renames to make exited 0
    # while simultaneously printing "run with --rename-citekeys to apply" —
    # nothing to report, according to the exit code. Renames only count as a
    # finding for the audit: once `--rename-citekeys` has applied them the work
    # is done and the caller has nothing left to act on.
    # A partial read counts too: the rename plan below was computed from the
    # blocks that parsed, so "no citekey changes needed" at exit 0 described a
    # library this run had only partly seen.
    findings = (
        bool(result.get("errors"))
        or has_read_warnings(result)
        or (not apply and bool(result.get("changed")))
    )

    if as_json:
        # `applied` distinguishes the two runs that otherwise look identical: an
        # audit reported a populated `changed[]` next to `backup_path: null`,
        # which reads as "these renames happened, and nothing was backed up".
        cli_json.emit_result(
            {**result, "applied": apply}, stdout, command="library reindex",
            items=result.get("changed") or [], bib_name=target["name"],
        )
        return exit_codes.FINDINGS if findings else exit_codes.OK

    print_read_warnings(result, stderr)
    print_lines(render_reindex_result(result, dry_run=not apply), stdout)
    print_lines(reindex_error_lines(result), stderr)
    backup = result.get("backup_path")
    if isinstance(backup, str):
        print(f"backup saved to {backup}", file=stderr)
    if not rename and result.get("changed"):
        print(
            "run with --rename-citekeys to apply "
            "(this rewrites citekeys; see 'pzi library reindex --help')",
            file=stdout,
        )
    return exit_codes.FINDINGS if findings else exit_codes.OK
