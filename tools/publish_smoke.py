#!/usr/bin/env python3
"""Publish smoke helpers for pzi.

Local mode is deterministic. Auth mode is a repeatable harness around browser
profiles/institutional cookies. Secrets come from environment only and are never
printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

SECRET_ENV_KEYS = (
    "PZI_CONTACT_EMAIL",
    "PZI_UNPAYWALL_EMAIL",
    "PZI_S2_API_KEY",
    "PZI_S2_API_KEY_CMD",
    "PZI_CHROMIUM_PROFILE",
    "PZI_FIREFOX_PROFILE",
)

RunFn = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SmokeResult:
    case: str
    browser: str
    metadata: str
    pdf: str
    status: str
    reason: str = ""


def masked_env_status(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Return set/unset state for smoke env vars without exposing values."""
    return {key: "set" if env.get(key) else "unset" for key in SECRET_ENV_KEYS}


def browser_profiles(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Map browser names to their dedicated profile env vars."""
    return {
        "chromium": env.get("PZI_CHROMIUM_PROFILE", "").strip(),
        "firefox": env.get("PZI_FIREFOX_PROFILE", "").strip(),
    }


def browser_pdf_cmd_for(browser: str, profiles: Mapping[str, str]) -> str | None:
    """Return browser hook command for browser, or None when profile missing."""
    profile = profiles.get(browser, "").strip()
    if not profile:
        return None
    return f"{sys.executable} -m pzi.browser_pdf_hook --profile {profile} --browser {browser}"


def envrc_guard(repo_root: Path, *, runner: RunFn = subprocess.run) -> str | None:
    """Refuse to run secrets-backed smoke if .envrc is tracked or unignored."""
    gitignore = repo_root / ".gitignore"
    ignored = False
    if gitignore.exists():
        ignored = any(line.strip() == ".envrc" for line in gitignore.read_text().splitlines())
    if not ignored:
        return ".envrc is not ignored; add it to .gitignore before storing secrets"

    result = runner(
        ["git", "ls-files", ".envrc"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return ".envrc is tracked by git; remove secrets from git before smoke tests"
    return None


def render_result_table(rows: Sequence[SmokeResult]) -> str:
    headers = ("case", "browser", "metadata", "pdf", "status", "reason")
    data = [headers, *[(r.case, r.browser, r.metadata, r.pdf, r.status, r.reason) for r in rows]]
    widths = [max(len(str(row[i])) for row in data) for i in range(len(headers))]
    lines = []
    for index, row in enumerate(data):
        lines.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)


def run_cmd(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{result.stderr or result.stdout}")
    return result.stdout


def http_json(
    url: str,
    *,
    body: object | None = None,
    token: str | None = None,
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Pzi-Token"] = token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    method = "POST" if body is not None else "GET"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(
    base_url: str,
    *,
    token: str | None = None,
    timeout_seconds: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            http_json(f"{base_url}/health", token=token)
            return
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError):
            time.sleep(0.2)
    raise RuntimeError("pzi server did not become healthy")


def run_local_smoke(repo_root: Path) -> list[SmokeResult]:
    rows: list[SmokeResult] = []
    with tempfile.TemporaryDirectory(prefix="pzi-smoke-") as tmp:
        tmp_path = Path(tmp)
        config = tmp_path / "config.toml"
        bib = tmp_path / "main.bib"
        token = "local-smoke-token"
        run_cmd(["pzi", "init", "--setup", "--bib", str(bib), "--config", str(config), "--force"])
        config.write_text(config.read_text() + f'api_auth_token = "{token}"\n', encoding="utf-8")
        run_cmd(["pzi", "doctor", "--config", str(config)])

        server = subprocess.Popen(
            ["pzi", "server", "--config", str(config), "--port", "8765"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            wait_for_health("http://127.0.0.1:8765", token=token)
            http_json("http://127.0.0.1:8765/bibs", token=token)
            _expect_http_error("http://127.0.0.1:8765/bibs", expected=401)
            _expect_http_error("http://127.0.0.1:8765/bibs", expected=401, token="bad")

            run_cmd([
                "pzi",
                "add",
                "10.1234/smoke",
                "--citekey",
                "smoke2026",
                "--title",
                "Smoke Test",
                "--config",
                str(config),
            ])
            pdf = base64.b64encode(b"%PDF-1.4 smoke").decode("ascii")
            result = http_json(
                "http://127.0.0.1:8765/attach-pdf-bytes",
                token=token,
                body={
                    "citekey": "smoke2026",
                    "pdf_base64": pdf,
                    "source_url": "https://example.com/smoke.pdf",
                },
            )
            pdf_path = Path(str(result.get("pdf_path") or ""))
            ok = "file = {" in bib.read_text(encoding="utf-8") and pdf_path.exists()
            rows.append(
                SmokeResult(
                    "local-api",
                    "none",
                    "ok",
                    "saved" if ok else "missing",
                    "pass" if ok else "fail",
                )
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()

    for browser in ("chromium", "firefox"):
        rows.append(
            SmokeResult(
                "extension-build",
                browser,
                "n/a",
                "n/a",
                "pending",
                "checked by build command",
            )
        )
    run_cmd([sys.executable, "tools/build_extension.py"], cwd=repo_root)
    for path, browser in (
        ("dist/chrome/manifest.json", "chromium"),
        ("dist/firefox/manifest.json", "firefox"),
    ):
        exists = (repo_root / path).exists()
        rows.append(
            SmokeResult(
                "extension-artifact",
                browser,
                "n/a",
                "n/a",
                "pass" if exists else "fail",
                path,
            )
        )
    run_cmd([sys.executable, "-m", "build"], cwd=repo_root)
    return rows


def _expect_http_error(url: str, *, expected: int, token: str | None = None) -> None:
    try:
        http_json(url, token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == expected:
            return
        raise RuntimeError(f"expected HTTP {expected}, got {exc.code}") from exc
    raise RuntimeError(f"expected HTTP {expected}, got success")


def run_auth_smoke(profiles: Mapping[str, str]) -> list[SmokeResult]:
    rows: list[SmokeResult] = []
    for browser in ("chromium", "firefox"):
        cmd = browser_pdf_cmd_for(browser, profiles)
        if cmd is None:
            env_name = "PZI_CHROMIUM_PROFILE" if browser == "chromium" else "PZI_FIREFOX_PROFILE"
            rows.append(
                SmokeResult(
                    "auth-profile",
                    browser,
                    "skip",
                    "skip",
                    "skip",
                    f"{env_name} unset",
                )
            )
        else:
            rows.append(
                SmokeResult(
                    "auth-profile",
                    browser,
                    "ready",
                    "ready",
                    "pass",
                    "profile configured",
                )
            )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pzi publish smoke runner")
    parser.add_argument("--mode", choices=("local", "auth", "all"), default="local")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    guard = envrc_guard(repo_root)
    if guard is not None:
        print(guard, file=sys.stderr)
        return 2

    print("Environment:")
    for key, state in masked_env_status().items():
        print(f"  {key}={state}")

    rows: list[SmokeResult] = []
    if args.mode in {"local", "all"}:
        rows.extend(run_local_smoke(repo_root))
    if args.mode in {"auth", "all"}:
        rows.extend(run_auth_smoke(browser_profiles()))
    print(render_result_table(rows))
    return 1 if any(row.status == "fail" for row in rows) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
