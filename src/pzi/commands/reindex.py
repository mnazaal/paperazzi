"""CLI runner for `pzi reindex`."""

from __future__ import annotations

from pzi.cli_render import _error_lines, _render_reindex_result
from pzi.commands.common import print_lines, resolve_target_or_error
from pzi.reindex_service import reindex_library


def run_reindex_command(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    resolved = resolve_target_or_error(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector, stderr=stderr,
    )
    if resolved is None:
        return 1
    config, target = resolved

    rename = getattr(args, "rename_citekeys", False)
    # Default is a read-only audit: keep citekeys stable unless explicitly asked.
    apply = rename and not args.dry_run
    if rename and apply:
        print(
            "warning: rewriting citekeys will break any \\cite{} references that use "
            "the old keys (in LaTeX documents, notes, etc.).",
            file=stderr,
        )

    result = reindex_library(
        bib_path=target["path"],
        papers_dir=target["papers_dir"],
        citekey_format=config.get("citekey_format"),
        pdf_filename_format=config.get("pdf_filename_format"),
        dry_run=not apply,
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )

    if result["status"] != "ok":
        print_lines(_error_lines("reindex failed", result.get("errors", [])), stderr)
        return 1

    print_lines(_render_reindex_result(result, dry_run=not apply), stdout)
    if not rename and result.get("changed"):
        print(
            "run with --rename-citekeys to apply "
            "(this rewrites citekeys; see 'pzi reindex --help')",
            file=stdout,
        )
    return 0 if not result.get("errors") else 1
