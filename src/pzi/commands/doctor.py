"""CLI runner for `pzi doctor`."""

from __future__ import annotations

import json
from pathlib import Path

from pzi.cli_render import _error_lines
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
            return 0
        print_lines(_error_lines("config invalid", result["errors"]), stderr)
        return 1

    result = doctor_check(config_path=config_path, home_dir=home_dir)
    print(json.dumps(result, indent=2, default=str), file=stdout)
    return 0 if result["config_ok"] else 1


def _reinstall_server(*, config_path, home_dir, stdout, stderr) -> int:
    """Reinstall the translation-server with the latest pinned versions."""
    import shutil

    from pzi.ts_backend import ensure_node, ensure_translation_server, is_ts_reachable

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    if config is None:
        print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    ts_url = config.get("translation_server_url")
    if not isinstance(ts_url, str) or not ts_url:
        print("translation_server_url not configured", file=stderr)
        return 1

    data_home = Path(config.get("pzi_data_home", "~/.local/share/pzi")).expanduser()
    print("reinstalling translation-server …", file=stdout)
    node = ensure_node(data_home, interactive=True, stdout=stdout, stderr=stderr)
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
