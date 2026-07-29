"""CLI runner for `pzi doctor`."""

from __future__ import annotations

from pathlib import Path

from pzi import cli_json, exit_codes
from pzi.cli_render import _error_lines, _render_doctor_result
from pzi.commands.common import print_lines
from pzi.config import load_config_file
from pzi.doctor_service import doctor_check


def run_doctor_command(args, *, home_dir, config_path, stdout, stderr) -> int:
    if getattr(args, "reinstall_server", False):
        return _reinstall_server(config_path=config_path, home_dir=home_dir,
                                 stdout=stdout, stderr=stderr)

    if getattr(args, "config_only", False):
        # Offline config check (no live service probes) — formerly `config validate`.
        cfg = load_config_file(config_path, home_dir=home_dir)
        valid = cfg["config"] is not None
        if getattr(args, "json", False):
            # This branch returns before the `--json` check below, so
            # `doctor --config-only --json` emitted prose — and on a *passing*
            # run it wrote `config valid: …` to stdout, breaking `| jq`.
            cli_json.emit_result(
                {
                    "status": "ok" if valid else "error",
                    "config_path": cfg["path"],
                    "config_ok": valid,
                    "errors": list(cfg["errors"]),
                },
                stdout,
                command="doctor --config-only",
                items=[],
            )
            return exit_codes.OK if valid else exit_codes.ENVIRONMENT
        if valid:
            print(f"config valid: {cfg['path']}", file=stdout)
            return exit_codes.OK
        print_lines(_error_lines("config invalid", cfg["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    result = doctor_check(config_path=config_path, home_dir=home_dir)
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="doctor")
    else:
        print_lines(_render_doctor_result(result), stdout)
    # A health check has to fail when the health is bad: reporting an
    # unreachable translation-server and exiting 0 makes it useless as a gate.
    # `status` is computed from the same problem list the report prints, so the
    # envelope and the exit code can no longer disagree.
    return exit_codes.OK if result["status"] == "ok" else exit_codes.ENVIRONMENT


def _reinstall_server(*, config_path, home_dir, stdout, stderr) -> int:
    """Reinstall the translation-server with the latest pinned versions."""
    import shutil

    from pzi.node_runtime import ensure_node
    from pzi.ts_backend import ensure_translation_server, is_ts_reachable

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    if config is None:
        print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    ts_url = config.get("translation_server_url")
    if not isinstance(ts_url, str) or not ts_url:
        print("translation_server_url not configured", file=stderr)
        return exit_codes.ENVIRONMENT

    data_home = Path(config["pzi_data_home"])
    print("reinstalling translation-server …", file=stdout)
    node_path = config.get("node_path")
    node = ensure_node(
        data_home,
        interactive=True,
        node_path=node_path if isinstance(node_path, str) else None,
        stdout=stdout,
        stderr=stderr,
    )
    if node is None:
        return exit_codes.ENVIRONMENT
    ts_dir = data_home / "ts"
    if ts_dir.exists():
        if is_ts_reachable(ts_url):
            print(
                "warning: a translation-server is running; restart `pzi server` "
                "after the update to use the new version.",
                file=stderr,
            )
        shutil.rmtree(ts_dir, ignore_errors=True)
    if ensure_translation_server(data_home, node, stdout=stdout, stderr=stderr) is None:
        return exit_codes.ENVIRONMENT
    print("translation-server reinstalled. Run `pzi server` to start.", file=stdout)
    return exit_codes.OK
