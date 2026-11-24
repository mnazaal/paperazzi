"""CLI runner for `pzi init`."""

from __future__ import annotations

import importlib.resources
import os
import shutil
import tempfile
import time
import tomllib
from pathlib import Path
from typing import NamedTuple, TextIO

from pzi import exit_codes, setup_service
from pzi.config import _normalize_path, default_data_home, escape_toml_string
from pzi.fileio import fsync_parent_dir


class _ConfiguredHome(NamedTuple):
    """What the config being replaced says about its data home.

    *spelled* is the value as the user wrote it (``~/x`` stays ``~/x``), or
    ``None`` when the key is absent or not a usable string. *unreadable* is the
    parse error when the file is not TOML at all. *token_settings* holds the
    token source keys the file sets (`api_auth_token_cmd`, `api_auth_token`),
    which decide the token the server resolves ahead of the data home's file.
    """

    spelled: str | None
    unreadable: str | None
    token_settings: tuple[tuple[str, str], ...] = ()


#: Config keys that name the API token, in `resolve_api_auth_token` precedence order.
_TOKEN_SOURCE_KEYS = ("api_auth_token_cmd", "api_auth_token")


def _existing_data_home(config_path: Path) -> _ConfiguredHome:
    """Read ``pzi_data_home`` from the raw TOML of the config being replaced.

    Read raw rather than through the validating loader: a config that fails
    validation is a common reason for `--force`, and the loader returns no
    config for it, which dropped a custom data home the token still lived under.
    The loader also fills an unset key with the XDG default, which is not
    something the user configured and so is not carried over.
    """
    if not config_path.exists():
        return _ConfiguredHome(None, None)
    try:
        raw = tomllib.loads(config_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return _ConfiguredHome(None, str(exc))
    configured = raw.get("pzi_data_home")
    tokens = tuple(
        (key, raw[key])
        for key in _TOKEN_SOURCE_KEYS
        if isinstance(raw.get(key), str) and raw[key].strip()
    )
    spelled = configured if isinstance(configured, str) and configured.strip() else None
    return _ConfiguredHome(spelled, None, tokens)


def _data_home_path(config_path: Path, home: _ConfiguredHome, home_dir: str) -> Path:
    """Where the runtime will look for the token under *config_path*."""
    if home.spelled is None:
        return Path(default_data_home(home_dir))
    return Path(
        _normalize_path(home.spelled, home_dir=home_dir, base_dir=str(config_path.parent))
    )


def _carry_over(content: str, existing: _ConfiguredHome) -> str:
    """Prefix *content* with the settings that decide which API token is read.

    A replacement config that omits `pzi_data_home` makes the reader look in the
    XDG default, where the token provisioned from the old config is not. One that
    omits `api_auth_token_cmd` or `api_auth_token` swaps the token the server
    resolves for the data home's file, un-pairing the extension.
    """
    lines: list[str] = []
    if existing.spelled is not None:
        lines.append(f'pzi_data_home = "{escape_toml_string(existing.spelled)}"')
    lines.extend(f'{key} = "{escape_toml_string(value)}"' for key, value in existing.token_settings)
    if not lines:
        return content
    note = "# kept from the replaced config: where the API token comes from"
    return f"{note}\n" + "\n".join(lines) + f"\n\n{content}"


def _rotate_token_only(
    dest: Path, *, home_dir: str, stdout: TextIO, stderr: TextIO
) -> int:
    """Replace the API token, leaving an existing config file untouched."""
    existing = _existing_data_home(dest)
    if existing.unreadable is not None:
        # The token lives under the config's `pzi_data_home`, which cannot be
        # read. Rotating the default home instead would retire nothing the
        # server reads and still report success.
        print(
            f"error: {dest} is not valid TOML ({existing.unreadable}); cannot tell "
            "where the API token lives, so nothing was rotated. Fix the config, "
            "then rerun `pzi init --rotate-token`.",
            file=stderr,
        )
        return exit_codes.USAGE
    data_home = _data_home_path(dest, existing, home_dir)
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


#: Where the optional browser extra installs from. pzi is not on PyPI yet (the
#: `pzi` endpoint returned 404 when checked), so guidance names the GitHub
#: distribution; switch to `pzi[playwright]` only once PyPI publishing is confirmed.
PZI_GITHUB_SOURCE = "git+https://github.com/mnazaal/paperazzi.git"
PLAYWRIGHT_SETUP_GUIDANCE = (
    "for the browser PDF fallback, install the optional extra once: "
    f"`uv tool install 'pzi[playwright] @ {PZI_GITHUB_SOURCE}'` "
    f"(or `pipx install 'pzi[playwright] @ {PZI_GITHUB_SOURCE}'`), "
    "then `playwright install chromium` (binaries also install on first use)."
)


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
                dest, home_dir=home_dir, stdout=stdout, stderr=stderr,
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
    existing = _existing_data_home(dest)
    data_home = _data_home_path(dest, existing, home_dir)
    if existing.unreadable is not None:
        print(
            f"warning: {dest} is not valid TOML ({existing.unreadable}); its "
            "pzi_data_home, if any, could not be read, so the API token is looked "
            f"up under the default data home {data_home}",
            file=stderr,
        )
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
    content = _carry_over(content, existing)
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
    elif existing.unreadable is not None:
        print(f"found an API auth token at {token_path} (mode 0600).", file=stdout)
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
        print(PLAYWRIGHT_SETUP_GUIDANCE, file=stdout)
    return exit_codes.OK
