"""Setup helpers for config and browser fallback."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from pzi import exit_codes
from pzi.config import escape_toml_string, tildify_path
from pzi.errors import REASON_UNAVAILABLE, PziError


def provision_api_token(data_home: Path, *, rotate: bool = False) -> tuple[Path, bool]:
    """Ensure an API auth token exists under *data_home*; return ``(path, created)``.

    Keeping the token in its own file (not ``config.toml``) is what lets the
    config be committed to dotfiles safely: pzi auto-reads this file at runtime
    from the resolved data home, so the config references neither the secret nor
    a path. Users who prefer a manager can set ``api_auth_token_cmd`` instead.

    **An existing token is reused unless *rotate* is set.** This function used
    to mint a fresh token on every call, and ``pzi init`` calls it
    unconditionally — so running ``pzi init`` for any reason (including against
    a throwaway ``--config``, which the browser-extension README prescribes as a
    smoke test) silently de-paired the user's browser extension from their
    server.
    """
    data_home.mkdir(parents=True, exist_ok=True)
    token_path = data_home / "api_token"
    if not rotate and _existing_token(token_path) is not None:
        return token_path, False
    token = secrets.token_urlsafe(32)
    # Create owner-only from the start so the token is never briefly
    # world-readable; O_CREAT's mode only applies on creation, so chmod after
    # to also tighten a pre-existing file.
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(token_path, 0o600)
    return token_path, True


def _existing_token(token_path: Path) -> str | None:
    """The token already on disk, or None if there is none to reuse.

    An *unreadable* file is not "no token": overwriting it would rotate a secret
    this process cannot even see. That case raises rather than silently minting
    a replacement.
    """
    if not token_path.exists():
        return None
    try:
        existing = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PziError(
            f"cannot read the existing API token at {token_path}: {exc.strerror}"
            " — fix its permissions, or pass --rotate-token to replace it",
            code=exit_codes.ENVIRONMENT,
            # `unavailable`, not `config`: the token file is exactly where the
            # config says, and the process cannot read it. Without a reason the
            # HTTP API answers its 400 fallback for what is a 503.
            reason=REASON_UNAVAILABLE,
        ) from exc
    return existing or None


def render_config(
    *,
    bib_name: str,
    bib_path: str,
    with_browser: bool,
    papers_dir: str | None = None,
    browser: str = "chromium",
    home_dir: str | None = None,
) -> str:
    """Render user config TOML from explicit setup options.

    Absolute paths under the home directory (the interpreter, a browser
    profile, the bib/papers paths) are folded to ``~/...`` so the generated
    config — routinely committed to dotfiles — does not expose the home layout,
    matching the ``~``-relative style of the commented example lines. pzi
    expands ``~`` on read (``_normalize_path`` for paths, ``_validate_browser_
    command`` for the browser hook).

    No API auth token is written here. ``pzi init`` writes the token to a
    ``0600`` file under the data home, and pzi auto-reads it at runtime from the
    running user's resolved data home — so this file (routinely committed to
    dotfiles) carries neither the secret nor an absolute home path. Users who
    prefer a password manager can add ``api_auth_token_cmd = "pass show ..."``.

    When ``browser`` is ``"firefox"``, auto-detects the Firefox profile
    directory and includes the ``--profile`` flag in the generated command.
    Falls back to a commented-out hint if no profile is found.
    """
    lines = [
        'translation_server_url = "http://127.0.0.1:1969"',
        'api_listen_host = "127.0.0.1"',
        'api_listen_port = 8765',
        "# API auth token is auto-read from <data-home>/api_token (written by "
        "`pzi init`); nothing secret is stored here. To use a manager instead: "
        '# api_auth_token_cmd = "pass show pzi-token"',
        '# pzi_data_home = "~/.local/share/pzi"  '
        "# defaults to $XDG_DATA_HOME/pzi (~/.local/share/pzi)",
        '# unpaywall_email = "your@email.com" # optional OA PDF lookup',
        '# unpaywall_email_cmd = "pass show unpaywall-email"',
        '# semantic_scholar_api_key_cmd = "pass show semantic-scholar"',
        '# citekey_format = "auth.lower + shorttitle(3,3) + year"',
        "# pdf_filename_format = "
        '"{{ firstCreator suffix=\\" - \\" }}{{ year suffix=\\" - \\" }}'
        '{{ title truncate=\\"100\\" }}"',
        '# pdf_file_path_style = "home" # or "relative" / "absolute"',
        '# page_metadata_cmd = "paper-meta --json" # optional page HTML metadata hook',
        '# page_metadata_timeout_seconds = 5',
    ]
    home = home_dir if home_dir is not None else os.path.expanduser("~")
    if with_browser:
        lines.extend(_browser_lines(browser=browser, home=home))
    lines.extend(
        [
            "",
            "[[bibs]]",
            f'name = "{escape_toml_string(bib_name)}"',
            f'path = "{escape_toml_string(tildify_path(bib_path, home_dir=home))}"',
        ]
    )
    if papers_dir:
        papers_dir = tildify_path(papers_dir, home_dir=home)
        lines.append(f'papers_dir = "{escape_toml_string(papers_dir)}"')
    else:
        lines.append("# papers_dir = \"~/bibs/papers\"  # defaults to <bib-dir>/papers/")
    lines.append("default = true")
    return "\n".join(lines) + "\n"


def _browser_lines(*, browser: str, home: str) -> list[str]:
    """Config lines recording a browser choice, without pinning an interpreter.

    **This function used to write `browser_pdf_cmd`, and that was the bug.** It
    interpolated the *then-current* `sys.executable`, producing a line like
    ``~/.local/share/uv/tools/<dist>/bin/python3 -m pzi.browser_pdf_hook``. That
    path is an install-time snapshot of the environment, written into a file the
    docstring above tells the user to commit to their dotfiles — so it went
    stale on a reinstall under a different tool directory, a Python upgrade, a
    distribution rename, or simply the second machine. Worse, a configured
    command *wins* over the one pzi builds at run time (`pdf.py`), so the stale
    value shadowed a mechanism that would have worked.

    Nothing here is derived from the running process any more. `pdf_planning.
    build_browser_pdf_command` synthesizes the command from `sys.executable`
    at the moment it is needed, which cannot be stale by construction, and is
    now the only place that builds one.

    The profile path *is* still recorded, because auto-detection cannot know
    which of several profiles the user meant. That one is validated on read
    rather than trusted — `pzi doctor` reports a `browser_profile_path` that has
    gone missing, instead of Playwright silently creating an empty profile and
    every authenticated fetch failing as "no PDF".
    """
    lines = ["# browser hook: pzi builds the command at run time from the",
             "# interpreter it is running under, so nothing here pins one."]
    profile = (
        _find_firefox_profile() if browser == "firefox" else _find_chrome_profile(home)
    )
    if profile:
        lines.append(
            f"# {browser} profile below is used for authenticated PDF access"
        )
        # Escaped, like `bib_name` and `bib_path` further down. A profile path
        # containing a backslash (every Windows path, and any profile directory
        # with one) produced a config.toml that TOML cannot parse — `pzi init
        # --setup --browser firefox` exited 0 and every later command failed to
        # load the config it had just written.
        folded = escape_toml_string(tildify_path(profile, home_dir=home))
        lines.append(f'browser_profile_path = "{folded}"')
    else:
        lines.append(
            f"# no {browser} profile auto-detected — set browser_profile_path"
            " below if you need an authenticated session"
        )
        lines.append('# browser_profile_path = "~/.config/google-chrome"')
    return lines


def _find_chrome_profile(home: str) -> str | None:
    """The Chrome/Chromium user-data directory under *home*, or None.

    Chrome honors ``$XDG_CONFIG_HOME``, so resolve it the same way rather than
    hardcoding ``~/.config`` — matching `pdf._default_chrome_profile`, which
    answers the same question at run time.

    *home* is threaded in rather than read from the environment so the path this
    writes and the path `tildify_path` folds are the same one; reading `$HOME`
    here is how an absolute home path leaks into a config meant to be committed.
    """
    from pzi.config import xdg_config_home

    base = Path(xdg_config_home(home)) / "google-chrome"
    return str(base) if base.is_dir() else None


def _find_firefox_profile() -> str | None:
    """Return the most recently modified Firefox profile, or None.

    Looks for ``*.default-release`` and ``*.default`` directories under
    ``~/.mozilla/firefox/``.  Picks the one with the most recent modification
    time so that custom profiles (e.g. BetterFox) are preferred when they
    were used more recently than the stock default.
    """
    ff_dir = os.path.expanduser("~/.mozilla/firefox")
    if not os.path.isdir(ff_dir):
        return None
    try:
        entries = os.listdir(ff_dir)
    except OSError:
        return None

    # Collect candidate profile dirs with their mtimes
    candidates: list[tuple[str, float]] = []
    for entry in entries:
        if not entry.endswith(".default-release") and not entry.endswith(
            ".default"
        ):
            continue
        full = os.path.join(ff_dir, entry)
        if not os.path.isdir(full):
            continue
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            mtime = 0
        candidates.append((full, mtime))

    if not candidates:
        return None

    # Sort by mtime descending (most recent first), then alphabetically
    candidates.sort(key=lambda x: (-x[1], x[0]))
    return candidates[0][0]
