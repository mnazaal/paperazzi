"""CLI runner for `pzi init`."""

from __future__ import annotations

import importlib.resources
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import TextIO

from pzi import exit_codes, setup_service
from pzi.config import default_data_home, load_config_file
from pzi.fileio import fsync_parent_dir


def _configured_data_home(config_path: Path, home_dir: str) -> str:
    """The data home the config being (over)written points at.

    On a fresh `init` there is no config yet and the XDG default is right. With
    `--force` over a config that sets `pzi_data_home`, that key is where the
    runtime will look for the token, so that is where it has to go.
    """
    if config_path.exists():
        loaded = load_config_file(str(config_path), home_dir=home_dir)
        config = loaded.get("config")
        if config is not None:
            configured = config.get("pzi_data_home")
            if isinstance(configured, str) and configured.strip():
                return configured
    return default_data_home(home_dir)


def _rotate_token_only(dest: Path, *, home_dir: str, stdout: TextIO) -> int:
    """Replace the API token, leaving an existing config file untouched."""
    data_home = Path(_configured_data_home(dest, home_dir))
    token_path, _created = setup_service.provision_api_token(data_home, rotate=True)
    print(
        f"API auth token replaced at {token_path} (mode 0600). Any paired "
        "browser extension is now unpaired — paste the new token into it. "
        f"{dest} was not modified.",
        file=stdout,
    )
    return exit_codes.OK


def _backup_config(dest: Path, data_home: Path) -> str:
    """Copy the config being replaced somewhere it cannot be clobbered.

    Two properties the previous ``{dest}.bak`` had neither of. It was a fixed
    name, so a second ``--force`` overwrote the backup with the first run's
    template output and the original became unrecoverable — the one scenario a
    backup exists for. And it sat beside the config, which is commonly a symlink
    into a dotfiles repository, so the recovery copy landed in a directory the
    user tracks (or, for a symlinked config, in a different directory from the
    file actually being replaced).

    The data home is neither: pzi's own, never tracked, and the timestamp means
    a rotation history rather than a single overwritable slot.

    *data_home* is passed in already resolved rather than re-derived here. This
    resolved it with ``default_data_home`` while the caller used
    ``_configured_data_home``, so with a ``pzi_data_home`` set the token went to
    the configured directory and the backup to the XDG default — the undo copy
    landing outside the only directory the README tells you to keep.
    """
    backups = data_home / "config-backups"
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    backup = backups / f"{dest.name}.{stamp}"
    suffix = 1
    while backup.exists():
        # Two runs in the same second must not collide either.
        backup = backups / f"{dest.name}.{stamp}.{suffix}"
        suffix += 1
    shutil.copy2(dest, backup)
    os.chmod(backup, 0o600)
    return str(backup)


def _write_config_atomically(dest: Path, content: str) -> None:
    """Replace *dest*'s contents in one step, following a symlink to its target.

    The write was a direct ``O_TRUNC``, so an interrupt left a half-written
    config — and a config is exactly the file that must not be half-written,
    since the next run reads it to find the library. Resolving the symlink first
    keeps the link itself intact: a config symlinked into a dotfiles repository
    stays symlinked, and it is the tracked file that gets the new content.
    """
    target = dest.resolve() if dest.is_symlink() else dest
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    fsync_parent_dir(str(target))


def run_init_command(
    args, *, home_dir: str, config_path: str, stdout: TextIO, stderr: TextIO
) -> int:
    dest = Path(config_path)
    rotate_token = bool(getattr(args, "rotate_token", False))
    if dest.exists() and not args.force:
        if rotate_token:
            # Rotation is about the token, not the config, and this refusal
            # fired first — so `--rotate-token` exited 2 on every real
            # installation and rotated nothing. The only way through was
            # `--force`, which also replaces the config with the shipped
            # template: the flag documented as replacing a token could only be
            # used by discarding the user's configuration.
            return _rotate_token_only(
                dest, home_dir=home_dir, stdout=stdout,
            )
        print(f"config already exists: {dest} (use --force to overwrite)", file=stderr)
        # USAGE: refused before doing anything, not a finding.
        return exit_codes.USAGE

    setup_only_flags = [
        name
        for name, value in (
            ("--bib", args.bib),
            ("--name", args.name),
            ("--papers-dir", args.papers_dir),
            ("--browser", args.browser),
        )
        if value is not None
    ]
    if setup_only_flags and not args.setup:
        # These describe a library, and only the `--setup` template has anywhere
        # to put one — the plain path copies the shipped template verbatim. They
        # used to be accepted and dropped, and `pzi init --bib ...` is exactly
        # what the docs recommend.
        verb = "require" if len(setup_only_flags) > 1 else "requires"
        print(
            f"error: {', '.join(setup_only_flags)} {verb} --setup "
            "(plain `pzi init` writes the commented template unchanged)",
            file=stderr,
        )
        return exit_codes.USAGE

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Provisioned on both paths: the template `init` copies states outright that
    # "`pzi init` writes a token to <data-home>/api_token (0600)", so the plain
    # path used to ship a config asserting a file it had not created. The read
    # side already auto-discovers it (`capture_context`), so writing it here is
    # all that was missing.
    #
    # Written where the *reader* will look. `resolve_api_auth_token` reads
    # `<pzi_data_home>/api_token`, so writing to the XDG default regardless left
    # the token orphaned for anyone who sets that key — the server then fell
    # back to `auth: DISABLED` while `init` reported a token had been written.
    data_home = Path(_configured_data_home(dest, home_dir))
    token_path, token_created = setup_service.provision_api_token(
        data_home, rotate=getattr(args, "rotate_token", False)
    )
    if args.setup:
        content = setup_service.render_config(
            bib_name=args.name or "main",
            bib_path=args.bib or "~/bibs/main.bib",
            papers_dir=args.papers_dir,
            with_browser=True,
            browser=args.browser or "chromium",
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
    # Back up first when overwriting. `--force` opened `O_TRUNC` and printed
    # only "created …", so a config declaring a bib named `mine` had zero
    # occurrences of `mine` afterwards and nothing to recover it from — for a
    # file that also carries `api_auth_token` and `*_cmd` hooks.
    backup: str | None = None
    if os.path.exists(dest):
        backup = _backup_config(dest, data_home)
    _write_config_atomically(dest, content)
    if backup is not None:
        print(f"overwrote {dest} (mode 0600); previous config saved to {backup}", file=stdout)
    else:
        print(f"created {dest} (mode 0600)", file=stdout)

    if token_created:
        print(
            f"API auth token written to {token_path} (mode 0600). pzi auto-reads "
            "it at runtime, so config.toml holds no secret or path and is safe to "
            "commit. To use a manager instead, set `api_auth_token_cmd`.",
            file=stdout,
        )
    else:
        print(
            f"reusing the existing API auth token at {token_path} — your browser "
            "extension stays paired. Pass --rotate-token to replace it.",
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
