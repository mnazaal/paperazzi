"""CLI runner for `pzi server`."""

from __future__ import annotations

import signal
from collections.abc import Iterator
from contextlib import contextmanager

from pzi.cli_server import build_server_plan
from pzi.config import load_config_file
from pzi.http_api import run_server


def run_server_command(args, *, home_dir, config_path, stdout, stderr) -> int:
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
        _serve()
    return 0


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
