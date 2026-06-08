"""CLI entrypoints for pzi."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time as _time
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TextIO, TypedDict, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pzi import setup_service
from pzi.add_service import add_input_to_bib
from pzi.bib_service import bib_stats, delete_entry, list_bibs, set_default_bib
from pzi.cli_parser import build_parser, build_record_overrides_from_add_args
from pzi.cli_render import (
    error_lines,
    render_add_success,
    render_bib_list,
    render_bib_promote_items,
    render_bib_stats,
    render_bib_update_items,
    render_delete_success,
    render_pdf_success,
    render_search_matches,
    render_tag_mutation_success,
)
from pzi.config import default_config_path
from pzi.doctor_service import doctor_check
from pzi.http_security import (
    DEFAULT_MAX_BODY_BYTES,
    HttpSecurityConfig,
    build_http_security_config,
    loopback_bind_host,
)
from pzi.pdf_service import attach_pdf, retry_pdf
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib
from pzi.tag_service import add_tags, list_tags, parse_tag_csv, remove_tags
from pzi.update_service import update_bib
from pzi import cli_version_text


def run_cli(
    argv: Sequence[str],
    *,
    home_dir: str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    parser = build_parser()
    try:
        import argcomplete  # noqa: F811

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    out = stdout or sys.stdout
    err = stderr or sys.stderr

    if not argv:
        parser.print_help(file=out)
        return 0

    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        # argparse raises SystemExit(2) on unknown subcommand or bad args.
        # Let the original error message stand — do not print extra help.
        return exc.code if isinstance(exc.code, int) else 1

    effective_home = home_dir or os.path.expanduser("~")
    config_path = getattr(args, "config", None) or default_config_path(effective_home)

    if args.command is None:
        parser.print_help(file=out)
        return 0

    if args.command == "add":
        return _run_add(args, home_dir=effective_home, config_path=config_path,
                        stdout=out, stderr=err, fetch_web=fetch_web, fetch_search=fetch_search,
                        bib_selector=args.target)
    if args.command == "pdf":
        return _run_pdf_retry(args, home_dir=effective_home, config_path=config_path,
                              stdout=out, stderr=err, bib_selector=args.target)
    if args.command == "tag":
        return _run_tag(args, home_dir=effective_home, config_path=config_path,
                        stdout=out, stderr=err, bib_selector=args.target)
    if args.command == "search":
        return _run_search(args, home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err, bib_selector=args.target)
    if args.command == "update":
        return _run_update(args, home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err)
    if args.command == "promote":
        return _run_promote(args, home_dir=effective_home, config_path=config_path,
                            stdout=out, stderr=err)
    if args.command == "list":
        result = list_bibs(config_path=config_path, home_dir=effective_home)
        if result["status"] == "ok":
            _print_lines(render_bib_list(result), out)
            return 0
        _print_lines(error_lines("failed to list bibs", result["errors"]), err)
        return 1
    if args.command == "set-default":
        result = set_default_bib(config_path=config_path, home_dir=effective_home, name=args.name)
        if result["status"] == "ok":
            print(result["message"], file=out)
            return 0
        _print_lines(error_lines(result["message"], result["errors"]), err)
        return 1
    if args.command == "doctor":
        return _run_doctor(home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err)
    if args.command == "server":
        return _run_server(args, home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err)
    if args.command == "init":
        return _run_init(args, config_path=config_path, stdout=out, stderr=err)
    if args.command == "services":
        return _run_services(args, config_path=config_path, stdout=out, stderr=err)
    if args.command == "browser":
        return _run_browser(args, stdout=out, stderr=err)
    if args.command == "version":
        print(cli_version_text(), file=out)
        return 0
    if args.command == "config":
        return _run_config(args, home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err)
    if args.command == "bib-stats":
        return _run_bib_stats(args, home_dir=effective_home, config_path=config_path,
                              stdout=out, stderr=err, bib_selector=args.target)
    if args.command == "delete":
        return _run_delete(args, home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err, bib_selector=args.target)
    print(f"unknown command: {args.command}", file=err)
    return 2


def _run_init(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    import importlib.resources
    from pathlib import Path

    dest = Path(config_path)
    if dest.exists() and not args.force:
        print(f"config already exists: {dest} (use --force to overwrite)", file=stderr)
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    setup_mode = args.setup or args.with_services or args.with_browser or args.with_flaresolverr
    with_services = args.setup or args.with_services or args.with_flaresolverr
    with_browser = args.setup or args.with_browser

    if setup_mode:
        content = setup_service.render_config(
            bib_name=args.name,
            bib_path=args.bib,
            papers_dir=args.papers_dir,
            with_browser=with_browser,
            with_flaresolverr=args.with_flaresolverr,
        )
    else:
        template = importlib.resources.files("pzi").joinpath("config.template.toml")
        with importlib.resources.as_file(template) as src:
            content = Path(src).read_text()
    dest.write_text(content)
    print(f"created {dest}", file=stdout)

    if with_services:
        for path in setup_service.write_service_files(
            str(dest), with_flaresolverr=args.with_flaresolverr
        ):
            print(f"created {path}", file=stdout)

    if with_browser:
        code = setup_service.install_playwright_browser(
            "chromium", stdout=stdout, stderr=stderr
        )
        if code != 0:
            print("browser install failed", file=stderr)
            return code
    return 0


def _run_services(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    return setup_service.run_services_command(
        args.services_command, config_path=config_path, stdout=stdout, stderr=stderr
    )


def _run_browser(args, *, stdout: TextIO, stderr: TextIO) -> int:
    if args.browser_command == "install":
        return setup_service.install_playwright_browser(
            args.browser,
            stdout=stdout,
            stderr=stderr,
        )
    print(f"unknown browser command: {args.browser_command}", file=stderr)
    return 2


def _run_config(args, *, home_dir, config_path, stdout, stderr) -> int:
    if args.config_command == "validate":
        from pzi.config import load_config_file
        result = load_config_file(config_path, home_dir=home_dir)
        if result["config"] is not None:
            print(f"config valid: {result['path']}", file=stdout)
            return 0
        _print_lines(error_lines("config invalid", result["errors"]), stderr)
        return 1
    print(f"unknown config command: {args.config_command}", file=stderr)
    return 2


def _run_bib_stats(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(error_lines("bib not found", []), stderr)
        return 1

    result = bib_stats(bib_path=target["path"], papers_dir=target["papers_dir"])
    if result["status"] == "ok":
        _print_lines(render_bib_stats(result), stdout)
        return 0
    _print_lines(error_lines("bib-stats failed", result["errors"]), stderr)
    return 1


def _run_delete(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(error_lines("bib not found", []), stderr)
        return 1

    if not args.force and not args.dry_run:
        # Safety confirmation for destructive operation
        print(
            f"Delete entry '{args.citekey}' from {target['path']}? [y/N] ",
            end="",
            file=stderr,
        )
        response = sys.stdin.readline().strip().lower()
        if response not in ("y", "yes"):
            print("cancelled", file=stdout)
            return 0

    result = delete_entry(
        bib_path=target["path"],
        citekey=args.citekey,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        print(render_delete_success(result), file=stdout)
        backup = result.get("backup_path")
        if isinstance(backup, str):
            print(f"backup saved to {backup}", file=stderr)
        return 0
    return _render_errors(result["message"], result["errors"], stderr)


def main() -> int:
    return run_cli(sys.argv[1:])


def _run_add(
    args: argparse.Namespace,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    from pzi.config import load_config_file

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is not None:
        if not ensure_translation_server(cfg["config"], config_path, stdout, stderr):
            print(
                "translation server is not running — cannot add paper.\n"
                "  Run 'pzi services up' and wait for the container to be ready, then retry.",
                file=stderr,
            )
            return 1

    record_overrides = build_record_overrides_from_add_args(args)
    kwargs = {}
    if fetch_web is not None:
        kwargs["fetch_web"] = fetch_web
    if fetch_search is not None:
        kwargs["fetch_search"] = fetch_search
    result = add_input_to_bib(
        config_path=config_path,
        home_dir=home_dir,
        value=args.value,
        record_overrides=record_overrides,
        bib_selector=bib_selector,
        dry_run=args.dry_run,
        **kwargs,
    )

    if result["status"] == "error":
        _print_lines(error_lines(result["message"], result["errors"]), stderr)
        return 1

    print(render_add_success(result), file=stdout)
    if args.dry_run and result.get("diff"):
        print(result["diff"], file=stdout, end="" if result["diff"].endswith("\n") else "\n")
    if args.verbose:
        _print_metadata_diagnostics(result, stdout)
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=stderr)
    return 0


def _run_pdf_retry(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if args.pdf_command == "attach":
        result = attach_pdf(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
            source=args.source,
        )
        if result["status"] == "ok":
            print(render_pdf_success("attached", result), file=stdout)
            return 0
        _print_lines(error_lines(result["message"], result["errors"]), stderr)
        return 1

    result = retry_pdf(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
    )
    if result["status"] == "ok":
        print(render_pdf_success("fetched", result), file=stdout)
        return 0
    _print_lines(error_lines(result["message"], result["errors"]), stderr)
    return 1


def _run_tag(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if args.tag_command == "list":
        result = list_tags(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
        )
        if result["status"] == "ok":
            for tag in result["tags"]:
                print(tag, file=stdout)
            return 0
        _print_lines(error_lines("failed to list tags", result["errors"]), stderr)
        return 1

    flat_tags: list[str] = []
    for raw in args.tags:
        flat_tags.extend(parse_tag_csv(raw))

    fn = add_tags if args.tag_command == "add" else remove_tags
    result = fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
        tags=flat_tags,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        print(render_tag_mutation_success(result), file=stdout)
        return 0
    return _render_errors(result["message"], result["errors"], stderr)


def _render_errors(message: str, errors: list[str], stderr: TextIO) -> int:
    _print_lines(error_lines(message, errors), stderr)
    return 1


def _print_lines(lines: Sequence[str], stream: TextIO) -> None:
    for line in lines:
        print(line, file=stream)


def _run_search(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if not any((args.query, args.author, args.year, args.tag)):
        print("error: at least one of --query, --author, --year, --tag is required", file=stderr)
        return 1

    ok = True
    for target in _target_list(bib_selector):
        result = search_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            query=args.query,
            author=args.author,
            year=args.year,
            tag=args.tag,
        )
        if result["status"] == "ok":
            _print_lines(render_search_matches(result), stdout)
        else:
            ok = False
            _print_lines(error_lines("search failed", result["errors"]), stderr)
    return 0 if ok else 1


def _target_list(value: list[str] | None) -> list[str | None]:
    return list(value) if value else [None]


def _run_update(args, *, home_dir, config_path, stdout, stderr) -> int:
    ok = True
    for target in _target_list(args.target):
        result = update_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            dry_run=args.dry_run,
        )
        if result["status"] == "ok":
            _print_lines(render_bib_update_items(result), stdout)
            if args.dry_run:
                _print_result_item_diffs(result, stdout)
            if args.verbose:
                _print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            _print_lines(error_lines("update failed", result["errors"]), stderr)
    return 0 if ok else 1


def _run_promote(args, *, home_dir, config_path, stdout, stderr) -> int:
    ok = True
    for target in _target_list(args.target):
        result = promote_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            dry_run=args.dry_run,
            keep_preprint=not args.replace,
        )
        if result["status"] == "ok":
            _print_lines(render_bib_promote_items(result), stdout)
            if args.dry_run:
                _print_result_item_diffs(result, stdout)
            if args.verbose:
                _print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            _print_lines(error_lines("promote failed", result["errors"]), stderr)
    return 0 if ok else 1


def _print_result_item_diffs(result: Mapping[str, object], stdout: TextIO) -> None:
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        diff = item.get("diff")
        if not isinstance(diff, str) or not diff:
            continue
        print(diff, file=stdout, end="" if diff.endswith("\n") else "\n")


def _print_metadata_diagnostics(result: Mapping[str, object], stdout: TextIO) -> None:
    lines = _metadata_diagnostic_lines(result)
    if not lines:
        return
    print("metadata diagnostics:", file=stdout)
    for line in lines:
        print(f"  {line}", file=stdout)


def _metadata_diagnostic_lines(result: Mapping[str, object]) -> list[str]:
    direct = result.get("metadata_diagnostics")
    if isinstance(direct, list):
        return [line for line in direct if isinstance(line, str)]
    lines: list[str] = []
    items = result.get("items")
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, Mapping):
            continue
        diagnostics = item.get("metadata_diagnostics")
        if not isinstance(diagnostics, list):
            continue
        lines.extend(line for line in diagnostics if isinstance(line, str))
    return lines


def _run_doctor(*, home_dir, config_path, stdout, stderr) -> int:
    result = doctor_check(config_path=config_path, home_dir=home_dir)
    print(json.dumps(result, indent=2, default=str), file=stdout)
    return 0 if result["config_ok"] else 1


def _run_server(args, *, home_dir, config_path, stdout, stderr) -> int:
    from pzi.config import load_config_file
    from pzi.http_api import run_server

    host = args.host
    port = args.port
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    plan = build_server_plan(host=host, port=port, config=config)
    if plan["status"] == "error":
        print(plan["message"], file=stderr)
        for error in cfg["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    host = plan["host"]
    port = plan["port"]

    if config is not None:
        if not ensure_translation_server(config, config_path, stdout, stderr):
            print(
                "warning: translation server is not running — "
                "capture requests will fail until it is ready",
                file=stderr,
            )

    stop_after = getattr(args, "stop_after", None)
    on_shutdown = None
    if stop_after is not None and config is not None:
        def _on_shutdown() -> None:
            print("stopping helpers (idle timeout) …", file=stdout)
            setup_service.run_services_command(
                "down", config_path=config_path, stdout=stdout, stderr=stderr,
        )
        on_shutdown = _on_shutdown

    print(f"serving on {host}:{port}", file=stdout)
    stdout.flush()
    run_server(
        config_path=config_path,
        home_dir=home_dir,
        host=host,
        port=port,
        security=plan["security"],
        idle_minutes=stop_after,
        on_shutdown=on_shutdown,
    )
    return 0


# ---------------------------------------------------------------------------
# Server plan types and function (merged from cli_server.py)
# ---------------------------------------------------------------------------


class ServerPlanError(TypedDict):
    status: Literal["error"]
    message: str


class ServerPlanOk(TypedDict):
    status: Literal["ok"]
    host: str
    port: int
    security: HttpSecurityConfig


ServerPlan: TypeAlias = ServerPlanOk | ServerPlanError


def build_server_plan(
    *,
    host: str | None,
    port: int | None,
    config: dict[str, Any] | None,
) -> ServerPlan:
    """Resolve server host/port/security without I/O."""
    if config is None and (host is None or port is None):
        return {"status": "error", "message": "failed to load config"}

    resolved_host = host
    resolved_port = port
    if config is not None:
        resolved_host = resolved_host or config["api_listen_host"]
        resolved_port = resolved_port or config["api_listen_port"]

    if resolved_host is None or resolved_port is None:
        return {"status": "error", "message": "failed to load config"}

    auth_token = config.get("api_auth_token") if config is not None else None
    if not auth_token and not loopback_bind_host(resolved_host):
        return {
            "status": "error",
            "message": (
                "refusing to serve unauthenticated API on a non-loopback host; "
                "set api_auth_token or bind to 127.0.0.1/localhost"
            ),
        }

    security = build_http_security_config(
        auth_token=auth_token,
        allowed_origins=config.get("api_allowed_origins") if config is not None else None,
        max_body_bytes=config.get("api_max_body_bytes", DEFAULT_MAX_BODY_BYTES)
        if config is not None
        else DEFAULT_MAX_BODY_BYTES,
    )
    return {
        "status": "ok",
        "host": resolved_host,
        "port": resolved_port,
        "security": security,
    }


# ---------------------------------------------------------------------------
# Translation server helpers (merged from cli_services.py)
# ---------------------------------------------------------------------------


class TranslationServerSkipPlan(TypedDict):
    status: Literal["skip"]


class TranslationServerCheckPlan(TypedDict):
    status: Literal["check"]
    translation_server_url: str


TranslationServerPlan = TranslationServerSkipPlan | TranslationServerCheckPlan


def translation_server_plan(
    config: dict[str, object], *, skip_auto_start: bool
) -> TranslationServerPlan:
    """Plan whether CLI should check/start translation-server."""
    if skip_auto_start:
        return {"status": "skip"}
    ts_url = config.get("translation_server_url")
    if not isinstance(ts_url, str) or not ts_url:
        return {"status": "skip"}
    return {"status": "check", "translation_server_url": ts_url}


def ensure_translation_server(
    config: dict[str, object], config_path: str, stdout: TextIO, stderr: TextIO
) -> bool:
    """Ensure translation-server is reachable. Returns True if ready."""
    plan = translation_server_plan(
        config,
        skip_auto_start=bool(os.environ.get("PZI_SKIP_AUTO_START")),
    )
    if plan["status"] == "skip":
        return True

    ts_url = plan["translation_server_url"]
    try:
        req = Request(ts_url.rstrip("/"), method="GET")
        urlopen(req, timeout=2)
        return True  # already reachable (2xx)
    except HTTPError:
        return True  # reachable (server responded, even with 4xx/5xx)
    except (URLError, OSError, ValueError):
        pass

    compose_path = setup_service.service_compose_path(config_path)
    if not compose_path.exists():
        print(
            f"translation-server not reachable at {ts_url}, "
            f"bootstrapping service files \u2026",
            file=stdout,
        )
        try:
            paths = setup_service.write_service_files(
                config_path, with_flaresolverr=False
            )
            for path in paths:
                print(f"  created {path}", file=stdout)
        except OSError as exc:
            print(
                f"could not create service files: {exc}\n"
                f"  Run 'pzi init --setup' to set up managed services, then\n"
                f"  run 'pzi services up' to start translation-server.",
                file=stderr,
            )
            return False

    print(
        f"translation-server not reachable at {ts_url}, starting helpers \u2026",
        file=stdout,
    )
    # Build first synchronously (can take minutes on first run), then start detached.
    if not run_podman_compose_build(config_path, stdout=stdout, stderr=stderr):
        return False
    rc = setup_service.run_services_command(
        "up",
        config_path=config_path,
        stdout=stdout,
        stderr=stderr,
        quiet_success=True,
    )
    if rc != 0:
        hint = (
            "Run 'pzi services down' to clean up stale containers, then retry.\n"
            "  Or run 'pzi services up' manually to see full output."
        )
        print(
            f"podman compose exited with code {rc} \u2014 "
            f"translation-server may not be running.\n"
            f"  {hint}",
            file=stderr,
        )
        return False
    # Wait for the container health check to pass (up to 90s)
    return wait_for_translation_server(ts_url, stdout=stdout, stderr=stderr)


def run_podman_compose_build(
    config_path: str, *, stdout: TextIO, stderr: TextIO
) -> bool:
    """Run podman compose build synchronously (first build can take minutes)."""
    compose_path = setup_service.service_compose_path(config_path)
    print(
        "building translation-server image (first run may take a few minutes) \u2026",
        file=stdout,
    )
    try:
        result = subprocess.run(
            ["podman", "compose", "-f", str(compose_path), "build"],
            shell=False,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print(
            "podman is not installed or not on PATH; install Podman, then run "
            "'pzi services up' manually.",
            file=stderr,
        )
        return False
    if result.returncode == 0:
        print("translation-server image ready", file=stdout)
        return True
    if result.stdout:
        print(result.stdout, end="", file=stdout)
    if result.stderr:
        print(result.stderr, end="", file=stderr)
    return False


def wait_for_translation_server(
    ts_url: str, *, stdout: TextIO, stderr: TextIO
) -> bool:
    """Poll translation-server until reachable or timeout. Returns True if ready."""
    health_url = ts_url.rstrip("/")
    started_at = _time.monotonic()
    deadline = started_at + 90.0
    attempt = 0
    while _time.monotonic() < deadline:
        attempt += 1
        try:
            urlopen(Request(health_url, method="GET"), timeout=2)
            print(f"translation-server ready (attempt {attempt})", file=stdout)
            return True
        except HTTPError:
            print(f"translation-server ready (attempt {attempt})", file=stdout)
            return True
        except (URLError, OSError, ValueError):
            pass
        _time.sleep(2)
    print(
        "translation-server did not become ready within 90s \u2014 "
        "check 'pzi services status' or run 'pzi services up' manually",
        file=stderr,
    )
    return False


if __name__ == "__main__":
    raise SystemExit(main())
