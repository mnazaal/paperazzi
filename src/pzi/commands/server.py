"""CLI runner for `pzi server`."""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager

from pzi.capture_context import resolve_optional_value
from pzi.cli_server import build_server_plan
from pzi.config import load_config_file
from pzi.http_api import run_server


def run_server_command(args, *, home_dir, config_path, stdout, stderr) -> int:
    host = args.host
    port = args.port
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    # Resolve the effective auth token here (running api_auth_token_cmd is I/O,
    # which build_server_plan must stay free of) and pass it into the plan.
    auth_token = None
    if config is not None:
        try:
            auth_token = resolve_optional_value(
                command=config.get("api_auth_token_cmd"),
                fallback=config.get("api_auth_token"),
            )
        except (RuntimeError, ValueError) as exc:
            print(f"failed to resolve api_auth_token_cmd: {exc}", file=stderr)
            return 1
    plan = build_server_plan(host=host, port=port, config=config, auth_token=auth_token)
    if plan["status"] == "error":
        print(plan["message"], file=stderr)
        for error in cfg["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    host = plan["host"]
    port = plan["port"]
    stop_after = getattr(args, "stop_after", None)

    def _serve() -> None:
        print(f"serving on {host}:{port}", file=stdout)
        stdout.flush()
        run_server(
            config_path=config_path,
            home_dir=home_dir,
            host=host,
            port=port,
            security=plan["security"],
            idle_minutes=stop_after,
            browser_profile_path=config.get("browser_profile_path") if config else None,
            browser_engine=config.get("browser_engine", "chromium") if config else "chromium",
        )

    if config is None:
        with _sigterm_as_keyboard_interrupt():
            _serve()
        return 0

    from pzi.ts_backend import backend_session

    with _sigterm_as_keyboard_interrupt(), backend_session(
        config, config_path, home_dir,
        interactive=True, stdout=stdout, stderr=stderr,
    ) as backend:
        if not backend["ready"]:
            print(
                "warning: translation server is not running — "
                "capture requests will fail until it is ready",
                file=stderr,
            )
        # For a long-lived server we own the translation-server child; guard it
        # so a crash mid-session is detected and restarted instead of silently
        # failing every capture until a human notices.
        watchdog = _maybe_start_watchdog(backend, stdout=stdout, stderr=stderr)
        try:
            _serve()
        finally:
            if watchdog is not None:
                watchdog.stop()
    return 0


def _maybe_start_watchdog(backend, *, stdout, stderr):
    """Start a TS watchdog for an owned, ready backend; else return None."""
    from pzi.ts_backend import TranslationServerWatchdog

    proc = backend.get("proc")
    if not (backend.get("owned") and backend.get("ready") and proc is not None):
        return None
    ts_url = backend.get("url")
    node_bin = backend.get("node_bin")
    ts_dir = backend.get("ts_dir")
    port = backend.get("port")
    if not (ts_url and node_bin and ts_dir and port):
        return None
    watchdog = TranslationServerWatchdog(
        ts_url=ts_url, proc=proc, node_bin=node_bin, ts_dir=ts_dir, port=port,
        stderr_log=backend.get("stderr_log"), stdout=stdout, stderr=stderr,
    )
    watchdog.start()
    return watchdog


@contextmanager
def _sigterm_as_keyboard_interrupt() -> Iterator[None]:
    def _raise(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError, AttributeError):
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
