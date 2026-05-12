"""Minimal deterministic TOML emitter for the pzi AppConfig shape."""

from __future__ import annotations

from pzi.config import AppConfig


def dump_app_config(config: AppConfig) -> str:
    lines: list[str] = [
        f'translation_server_url = "{_escape(config["translation_server_url"])}"',
        f'api_listen_host = "{_escape(config["api_listen_host"])}"',
        f'api_listen_port = {config["api_listen_port"]}',
    ]
    email = config.get("unpaywall_email")
    if email is not None:
        lines.append(f'unpaywall_email = "{_escape(email)}"')
    email_cmd = config.get("unpaywall_email_cmd")
    if email_cmd is not None:
        lines.append(f'unpaywall_email_cmd = "{_escape(email_cmd)}"')
    s2_key = config.get("semantic_scholar_api_key")
    if s2_key is not None:
        lines.append(f'semantic_scholar_api_key = "{_escape(s2_key)}"')
    s2_key_cmd = config.get("semantic_scholar_api_key_cmd")
    if s2_key_cmd is not None:
        lines.append(f'semantic_scholar_api_key_cmd = "{_escape(s2_key_cmd)}"')
    flaresolverr_url = config.get("flaresolverr_url")
    if flaresolverr_url is not None:
        lines.append(f'flaresolverr_url = "{_escape(flaresolverr_url)}"')
    browser_pdf_cmd = config.get("browser_pdf_cmd")
    if browser_pdf_cmd is not None:
        lines.append(f'browser_pdf_cmd = "{_escape(browser_pdf_cmd)}"')

    for bib in config["bibs"]:
        lines.append("")
        lines.append("[[bibs]]")
        lines.append(f'name = "{_escape(bib["name"])}"')
        lines.append(f'path = "{_escape(bib["path"])}"')
        lines.append(f'papers_dir = "{_escape(bib["papers_dir"])}"')
        lines.append(f"default = {'true' if bib['default'] else 'false'}")

    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
