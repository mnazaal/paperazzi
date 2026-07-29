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
        result = load_config_file(config_path, home_dir=home_dir)
        if result["config"] is not None:
            print(f"config valid: {result['path']}", file=stdout)
            return exit_codes.OK
        print_lines(_error_lines("config invalid", result["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    result = doctor_check(config_path=config_path, home_dir=home_dir)
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="doctor", items=result.get("bibs") or [])
    else:
        print_lines(_render_doctor_result(result), stdout)
    # A health check has to fail when the health is bad: reporting an
    # unreachable translation-server and exiting 0 makes it useless as a gate.
    return exit_codes.OK if _doctor_healthy(result) else exit_codes.ENVIRONMENT


def _reinstall_server(*, config_path, home_dir, stdout, stderr) -> int:
    """Reinstall the translation-server with the latest pinned versions."""
    import shutil

    from pzi.node_runtime import ensure_node
    from pzi.ts_backend import ensure_translation_server, is_ts_reachable

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    if config is None:
        print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    ts_url = config.get("translation_server_url")
    if not isinstance(ts_url, str) or not ts_url:
        print("translation_server_url not configured", file=stderr)
        return 1

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
        return 1
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
        return 1
    print("translation-server reinstalled. Run `pzi server` to start.", file=stdout)
    return 0


def _doctor_healthy(result) -> bool:
    """True when every probe doctor ran came back healthy."""
    if not result.get("config_ok"):
        return False
    if any(not bib.get("path_exists") for bib in result.get("bibs") or []):
        return False
    if result.get("translation_server_url") and not result.get("translation_server_reachable"):
        return False
    # A configured secret command that cannot run is a config fault the user
    # must fix, not an advisory: without this the report would name the problem
    # and still exit 0, which is the outcome `doctor` exists to prevent. An
    # unreachable API (`probe_error`) stays advisory — that is not the user's
    # config being wrong.
    if (result.get("semantic_scholar") or {}).get("key_error"):
        return False
    return True
