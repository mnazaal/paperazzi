"""CLI runner for `pzi init`."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import TextIO

from pzi import setup_service


def run_init_command(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    dest = Path(config_path)
    if dest.exists() and not args.force:
        print(f"config already exists: {dest} (use --force to overwrite)", file=stderr)
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)

    if args.setup:
        content = setup_service.render_config(
            bib_name=args.name,
            bib_path=args.bib,
            papers_dir=args.papers_dir,
            with_browser=True,
            browser=args.browser,
        )
    else:
        template = importlib.resources.files("pzi").joinpath("config.template.toml")
        with importlib.resources.as_file(template) as src:
            content = Path(src).read_text()
    dest.write_text(content)
    print(f"created {dest}", file=stdout)

    if args.setup:
        print(
            "next: run `pzi server` (or `pzi add <doi|url|pdf>`) — the "
            "translation-server installs and starts on first use.",
            file=stdout,
        )
        print(
            "for the browser PDF fallback, install the optional extra once: "
            "`pip install 'paperazzi[playwright]'` (or `pipx install 'paperazzi[playwright]'`), "
            "then `playwright install chromium` (binaries also install on first use).",
            file=stdout,
        )
    return 0
