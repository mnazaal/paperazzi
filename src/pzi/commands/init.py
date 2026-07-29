"""CLI runner for `pzi init`."""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
from typing import TextIO

from pzi import exit_codes, setup_service
from pzi.config import default_data_home


def run_init_command(
    args, *, home_dir: str, config_path: str, stdout: TextIO, stderr: TextIO
) -> int:
    dest = Path(config_path)
    if dest.exists() and not args.force:
        print(f"config already exists: {dest} (use --force to overwrite)", file=stderr)
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Provisioned on both paths: the template `init` copies states outright that
    # "`pzi init` writes a token to <data-home>/api_token (0600)", so the plain
    # path used to ship a config asserting a file it had not created. The read
    # side already auto-discovers it (`capture_context`), so writing it here is
    # all that was missing.
    data_home = Path(default_data_home(home_dir))
    token_path: Path = setup_service.provision_api_token(data_home)
    if args.setup:
        content = setup_service.render_config(
            bib_name=args.name,
            bib_path=args.bib,
            papers_dir=args.papers_dir,
            with_browser=True,
            browser=args.browser,
            home_dir=home_dir,
        )
    else:
        template = importlib.resources.files("pzi").joinpath("config.template.toml")
        with importlib.resources.as_file(template) as src:
            content = Path(src).read_text()
    # Owner-only from creation: config.toml may carry an api_auth_token in
    # plaintext and *_cmd hooks that pzi executes, so it should never be briefly
    # group/world-readable. chmod after the write also tightens a pre-existing
    # file being overwritten with --force.
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(dest, 0o600)
    print(f"created {dest} (mode 0600)", file=stdout)

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
    return exit_codes.OK
