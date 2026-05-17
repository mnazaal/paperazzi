"""Setup helpers for config, managed services, and browser fallback."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TextIO

from pzi.service_templates import TRANSLATION_SERVER_CONTAINERFILE, render_compose


def render_config(
    *,
    bib_name: str,
    bib_path: str,
    with_browser: bool,
    with_flaresolverr: bool,
) -> str:
    """Render user config TOML from explicit setup options."""
    lines = [
        'translation_server_url = "http://127.0.0.1:1969"',
        'api_listen_host = "127.0.0.1"',
        'api_listen_port = 8765',
        '# unpaywall_email = "your@email.com" # optional OA PDF lookup',
        '# unpaywall_email_cmd = "pass show unpaywall-email"',
        '# semantic_scholar_api_key_cmd = "pass show semantic-scholar"',
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
            "default = true",
            "# papers_dir = \"~/bibs/papers\"  # defaults to <bib-dir>/papers/",
        ]
    )
    return "\n".join(lines) + "\n"


def write_service_files(config_path: str, *, with_flaresolverr: bool) -> list[str]:
    """Write managed compose + Containerfile beside config."""
    config_dir = Path(config_path).parent
    container_dir = config_dir / "containers" / "translation-server"
    container_dir.mkdir(parents=True, exist_ok=True)
    compose_path = config_dir / "compose.yml"
    compose_path.write_text(render_compose(with_flaresolverr=with_flaresolverr), encoding="utf-8")

    (container_dir / "Containerfile").write_text(
        TRANSLATION_SERVER_CONTAINERFILE,
        encoding="utf-8",
    )
    return [str(compose_path), str(container_dir / "Containerfile")]


def service_compose_path(config_path: str) -> Path:
    return Path(config_path).parent / "compose.yml"


def run_services_command(
    action: str,
    *,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
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
    if result.stdout:
        print(result.stdout, end="", file=stdout)
    if result.stderr:
        print(result.stderr, end="", file=stderr)
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


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
