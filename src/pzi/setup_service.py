"""Setup helpers for config and browser fallback."""

from __future__ import annotations

import os
import secrets
import shlex
import sys
from pathlib import Path

from pzi.config import escape_toml_string, tildify_path


def _quote_token(token: str) -> str:
    """Quote *token* for the browser command only when needed to round-trip.

    ``shlex.quote`` is shell-safety oriented and always single-quotes a leading
    ``~``, but the browser command is split with ``shlex.split`` and run with
    ``shell=False`` (never via a shell), and pzi expands ``~`` itself. So emit a
    bare ``~/...`` token when it survives ``shlex.split`` intact, and only fall
    back to quoting for tokens that otherwise would not (e.g. embedded spaces).
    """
    return token if shlex.split(token) == [token] else shlex.quote(token)


def provision_api_token(data_home: Path) -> Path:
    """Generate an API auth token, write it to a ``0600`` file under
    *data_home*, and return that file's path.

    Keeping the token in its own file (not ``config.toml``) is what lets the
    config be committed to dotfiles safely: pzi auto-reads this file at runtime
    from the resolved data home, so the config references neither the secret nor
    a path. Users who prefer a manager can set ``api_auth_token_cmd`` instead.
    """
    data_home.mkdir(parents=True, exist_ok=True)
    token_path = data_home / "api_token"
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
    return token_path


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
        '# pdf_file_path_style = "absolute" # or "relative" for paths relative to .bib',
        '# page_metadata_cmd = "paper-meta --json" # optional page HTML metadata hook',
        '# page_metadata_timeout_seconds = 5',
    ]
    home = home_dir if home_dir is not None else os.path.expanduser("~")
    if with_browser:
        python = _quote_token(tildify_path(sys.executable, home_dir=home))
        cmd = f'{python} -m pzi.browser_pdf_hook --browser {browser}'
        if browser == "firefox":
            profile = _find_firefox_profile()
            if profile:
                cmd += f" --profile {_quote_token(tildify_path(profile, home_dir=home))}"
                lines.append(
                    "# browser_pdf_cmd uses your Firefox profile for authenticated"
                    " PDF access"
                )
            else:
                lines.append(
                    "# no Firefox profile auto-detected — add --profile <path> below"
                    " if needed"
                )
                lines.append(
                    "# find your profile: ls ~/.mozilla/firefox/*.default-release"
                )
        lines.append(f'browser_pdf_cmd = "{cmd}"')
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
