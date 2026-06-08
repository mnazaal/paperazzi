"""Setup helpers for config, managed services, and browser fallback."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TextIO


def render_config(
    *,
    bib_name: str,
    bib_path: str,
    with_browser: bool,
    with_flaresolverr: bool,
    papers_dir: str | None = None,
) -> str:
    """Render user config TOML from explicit setup options."""
    lines = [
        'translation_server_url = "http://127.0.0.1:1969"',
        'api_listen_host = "127.0.0.1"',
        'api_listen_port = 8765',
        '# unpaywall_email = "your@email.com" # optional OA PDF lookup',
        '# unpaywall_email_cmd = "pass show unpaywall-email"',
        '# semantic_scholar_api_key_cmd = "pass show semantic-scholar"',
        '# citekey_format = "auth.lower + shorttitle(3,3) + year"',
        "# pdf_filename_format = "
        '"{{ firstCreator suffix=\\" - \\" }}{{ year suffix=\\" - \\" }}'
        '{{ title truncate=\\"100\\" }}"',
    ]
    if with_browser:
        lines.append('browser_pdf_cmd = "pzi-browser-hook --browser chromium"')
    if with_flaresolverr:
        lines.append('flaresolverr_url = "http://127.0.0.1:8191"')
    lines.extend(
        [
            "",
            "[[bibs]]",
            f'name = "{_escape_toml_string(bib_name)}"',
            f'path = "{_escape_toml_string(bib_path)}"',
        ]
    )
    if papers_dir:
        lines.append(f'papers_dir = "{_escape_toml_string(papers_dir)}"')
    else:
        lines.append("# papers_dir = \"~/bibs/papers\"  # defaults to <bib-dir>/papers/")
    lines.append("default = true")
    return "\n".join(lines) + "\n"


def write_service_files(config_path: str, *, with_flaresolverr: bool) -> list[str]:
    """Write managed compose + Containerfile beside config."""
    config_dir = Path(config_path).parent
    container_dir = config_dir / "containers" / "translation-server"
    container_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    compose_path = config_dir / "compose.yml"
    compose_path.write_text(render_compose(with_flaresolverr=with_flaresolverr), encoding="utf-8")

    (container_dir / "Containerfile").write_text(
        TRANSLATION_SERVER_CONTAINERFILE,
        encoding="utf-8",
    )
    (container_dir / "apply-cookie-patch.sh").write_text(
        APPLY_COOKIE_PATCH_SCRIPT,
        encoding="utf-8",
    )
    return [
        str(compose_path),
        str(container_dir / "Containerfile"),
        str(container_dir / "apply-cookie-patch.sh"),
    ]


def service_compose_path(config_path: str) -> Path:
    return Path(config_path).parent / "compose.yml"


def run_services_command(
    action: str,
    *,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    quiet_success: bool = False,
) -> int:
    """Run podman compose for managed pzi services."""
    compose_path = service_compose_path(config_path)
    if not compose_path.exists():
        print(
            f"service files not found: {compose_path} (run: pzi init --setup)",
            file=stderr,
        )
        return 1

    args = ["podman", "compose", "-f", str(compose_path)]
    if action == "up":
        args.extend(["up", "-d"])
    elif action == "down":
        args.append("down")
    elif action == "status":
        args.append("ps")
    else:
        print(f"unknown services command: {action}", file=stderr)
        return 2

    try:
        result = subprocess.run(args, shell=False, text=True, capture_output=True)
    except FileNotFoundError:
        print("podman not found; install podman with compose support", file=stderr)
        return 1
    if not quiet_success or result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="", file=stdout)
        if result.stderr:
            filtered_stderr = _filter_podman_compose_stderr(result.stderr)
            if filtered_stderr:
                print(filtered_stderr, end="", file=stderr)
    return result.returncode


def install_playwright_browser(browser: str, *, stdout: TextIO, stderr: TextIO) -> int:
    """Install Playwright browser binaries for browser PDF fallback."""
    args = [sys.executable, "-m", "playwright", "install", browser]
    result = subprocess.run(args, shell=False, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="", file=stdout)
    if result.stderr:
        print(result.stderr, end="", file=stderr)
    return result.returncode


_PODMAN_COMPOSE_BANNER_PREFIX = ">>>> Executing external compose provider"


def _filter_podman_compose_stderr(stderr_text: str) -> str:
    """Strip podman-compose's banner lines from stderr output."""
    lines = stderr_text.splitlines(keepends=True)
    filtered = [
        line
        for line in lines
        if _PODMAN_COMPOSE_BANNER_PREFIX not in line
        and not line.strip().startswith("\x1b")
    ]
    return "".join(filtered)


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Single-source templates for pzi-managed helper services
# (inlined from service_templates.py)
# ---------------------------------------------------------------------------

APPLY_COOKIE_PATCH_SCRIPT = r"""#!/bin/sh
# Apply cookie-bridge patches to translation-server source.
# Called from Containerfile after git clone.

set -eu

WEB_SESSION="src/webSession.js"
WEB_ENDPOINT="src/webEndpoint.js"

# --- Patch webSession.js: inject cookies into _cookieSandbox ---
# Anchor: the line "this._cookieSandbox = cookieJar();"
# Insert the cookie injection block right after it.
if ! grep -q "_pziCookies" "$WEB_SESSION"; then
    sed -i '/this\._cookieSandbox = cookieJar();/a\
\
                        // --- pzi cookie bridge: inject browser cookies ---\
                        if (this._cookies) {\
                                var _pziCookies = this._cookies.split(/;\\s*/);\
                                for (var _i = 0; _i < _pziCookies.length; _i++) {\
                                        var _c = _pziCookies[_i].trim();\
                                        if (_c) {\
                                                this._cookieSandbox.setCookie(_c, url);\
                                        }\
                                }\
                        }\
                        // --- end pzi patch ---' \
        "$WEB_SESSION"
    echo "  pzi: patched $WEB_SESSION for cookie injection"
else
    echo "  pzi: $WEB_SESSION already patched, skipping"
fi


# --- Patch webEndpoint.js: forward cookies from request body to session ---
# Anchor: the line containing "await session.handleURL();"
# Insert the cookie-forwarding block right before it.
if ! grep -q "_pziCookies" "$WEB_ENDPOINT"; then
    sed -i '/await session\.handleURL();/i\
\
                // --- pzi cookie bridge: forward cookies to session ---\
                if (data \&\& typeof data.cookies === "string" \&\& data.cookies) {\
                        session._cookies = data.cookies;\
                }\
                // --- end pzi patch ---' \
        "$WEB_ENDPOINT"
    echo "  pzi: patched $WEB_ENDPOINT for cookie forwarding"
else
    echo "  pzi: $WEB_ENDPOINT already patched, skipping"
fi
"""

TRANSLATION_SERVER_CONTAINERFILE = """# Stage 1: build — git, npm, patching (discarded after build)
FROM node:22-alpine AS build

RUN apk add --no-cache git

WORKDIR /app

RUN git clone --depth=1 https://github.com/zotero/translation-server.git . && \\
    git clone --depth=1 https://github.com/zotero/translators.git modules/translators/ && \\
    git clone --depth=1 https://github.com/zotero/utilities.git modules/utilities/ && \\
    git clone --depth=1 https://github.com/zotero/translate.git modules/translate/ && \\
    git clone --depth=1 https://github.com/zotero/zotero-schema.git modules/zotero-schema/

COPY apply-cookie-patch.sh /tmp/
RUN sh /tmp/apply-cookie-patch.sh

RUN npm install --production && \\
    npm cache clean --force && \\
    find modules -name ".git" -type d -exec rm -rf {} + 2>/dev/null || true

# Stage 2: runtime — minimal, no git, no devDeps, no .git dirs
FROM node:22-alpine

RUN apk add --no-cache netcat-openbsd && \\
    addgroup -S pzi && adduser -S pzi -G pzi

COPY --from=build --chown=pzi:pzi /app /app

WORKDIR /app
USER pzi
EXPOSE 1969
CMD ["npm", "start"]
"""

_BASE_COMPOSE = """services:
  translation-server:
    build:
      context: ./containers/translation-server
      dockerfile: Containerfile
    ports:
      - "1969:1969"
    restart: unless-stopped
    mem_limit: 512m
    read_only: true
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:exec
    healthcheck:
      test: ["CMD", "nc", "-z", "127.0.0.1", "1969"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
"""

_FLARESOLVERR_COMPOSE = """
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:v3.3.25
    ports:
      - "8191:8191"
    restart: unless-stopped
    mem_limit: 512m
    security_opt:
      - no-new-privileges:true
    environment:
      - LOG_LEVEL=info
      - LOG_HTML=false
      - CAPTCHA_SOLVER=none
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8191/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
"""


def render_compose(*, with_flaresolverr: bool) -> str:
    """Render managed compose YAML for local helper services."""
    text = _BASE_COMPOSE
    if with_flaresolverr:
        text += _FLARESOLVERR_COMPOSE
    return text
