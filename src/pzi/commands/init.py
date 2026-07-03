"""CLI runner for `pzi init`."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import TextIO

from pzi import setup_service
from pzi.config import default_data_home


def run_init_command(
    args, *, home_dir: str, config_path: str, stdout: TextIO, stderr: TextIO
) -> int:
    dest = Path(config_path)
    if dest.exists() and not args.force:
        print(f"config already exists: {dest} (use --force to overwrite)", file=stderr)
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)

    token_path: Path | None = None
    if args.setup:
        data_home = Path(default_data_home(home_dir))
        token_path = setup_service.provision_api_token(data_home)
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

    if args.setup and token_path is not None:
        print(
            f"API auth token written to {token_path} (mode 0600). pzi auto-reads "
            "it at runtime, so config.toml holds no secret or path and is safe to "
            "commit. To use a manager instead, set `api_auth_token_cmd`.",
            file=stdout,
        )

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
