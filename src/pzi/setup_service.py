"""Setup helpers for config and browser fallback."""

from __future__ import annotations

import os
import secrets
import shlex
import sys

from pzi.config import escape_toml_string


def render_config(
    *,
    bib_name: str,
    bib_path: str,
    with_browser: bool,
    papers_dir: str | None = None,
    browser: str = "chromium",
) -> str:
    """Render user config TOML from explicit setup options.

    When ``browser`` is ``"firefox"``, auto-detects the Firefox profile
    directory and includes the ``--profile`` flag in the generated command.
    Falls back to a commented-out hint if no profile is found.
    """
    lines = [
        'translation_server_url = "http://127.0.0.1:1969"',
        'api_listen_host = "127.0.0.1"',
        'api_listen_port = 8765',
        f'api_auth_token = "{secrets.token_urlsafe(32)}"',
        'pzi_data_home = "~/.local/share/pzi"',
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
    if with_browser:
        python = shlex.quote(sys.executable)
        cmd = f'{python} -m pzi.browser_pdf_hook --browser {browser}'
        if browser == "firefox":
            profile = _find_firefox_profile()
            if profile:
                cmd += f" --profile {shlex.quote(profile)}"
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
            f'path = "{escape_toml_string(bib_path)}"',
        ]
    )
    if papers_dir:
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
