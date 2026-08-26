"""CLI runner for `pzi doctor`."""

from __future__ import annotations

from pathlib import Path

from pzi import cli_json, exit_codes
from pzi.cli_render import error_lines, render_doctor_result
from pzi.commands.common import emit_usage_error, print_lines
from pzi.config import load_config_file
from pzi.doctor_service import doctor_check
from pzi.errors import REASON_CONFIG, REASON_UNAVAILABLE


def run_doctor_command(args, *, home_dir, config_path, stdout, stderr) -> int:
    if getattr(args, "reinstall_server", False):
        if getattr(args, "config_only", False):
            # `--config-only` is documented as the offline check. Running a
            # network reinstall under it — which is what used to happen, since
            # this branch was tested first — is the opposite of what was asked.
            return emit_usage_error(
                args,
                "--config-only and --reinstall-server are mutually exclusive "
                "(--config-only is offline; --reinstall-server clones and "
                "installs over the network)",
                command_path=("doctor",),
                stdout=stdout,
                stderr=stderr,
            )
        return _reinstall_server(config_path=config_path, home_dir=home_dir,
                                 stdout=stdout, stderr=stderr, args=args)

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
                    # Keys pzi does not know: not fatal (a config written for a
                    # newer pzi still loads) but a typo'd key silently did
                    # nothing, which is exactly what `doctor` is for.
                    "warnings": list(cfg.get("warnings") or []),
                },
                stdout,
                command="doctor --config-only",
                items=[],
            )
            return exit_codes.OK if valid else exit_codes.ENVIRONMENT
        for warning in cfg.get("warnings") or []:
            print(f"warning: {warning}", file=stderr)
        if valid:
            print(f"config valid: {cfg['path']}", file=stdout)
            return exit_codes.OK
        print_lines(error_lines("config invalid", cfg["errors"]), stderr)
        return exit_codes.ENVIRONMENT

    result = doctor_check(config_path=config_path, home_dir=home_dir)
    if getattr(args, "json", False):
        cli_json.emit_result(result, stdout, command="doctor")
    else:
        print_lines(render_doctor_result(result), stdout)
    # A health check has to fail when the health is bad: reporting an
    # unreachable translation-server and exiting 0 makes it useless as a gate.
    # `status` is computed from the same problem list the report prints, so the
    # envelope and the exit code can no longer disagree.
    return exit_codes.OK if result["status"] == "ok" else exit_codes.ENVIRONMENT


def _reinstall_server(*, config_path, home_dir, stdout, stderr, args=None) -> int:
    """Reinstall the translation-server with the latest pinned versions.

    This branch returns before the `--json` check in the caller, so it used
    to answer a documented `--json` invocation with prose on stdout. Progress
    goes to stderr under `--json` for the same reason: stdout carries exactly
    one document.
    """
    as_json = bool(getattr(args, "json", False))
    progress = stderr if as_json else stdout

    def _finish(
        code: int, message: str, errors: list[str], *, reason: str = REASON_UNAVAILABLE
    ) -> int:
        if code == exit_codes.OK:
            if as_json:
                cli_json.emit_result(
                    {"status": "ok", "message": message, "errors": errors},
                    stdout,
                    command="doctor --reinstall-server",
                    items=[],
                )
            else:
                print(message, file=stdout)
            return code
        # Collapsed onto the shared failure emitter rather than kept as its
        # own hand-rolled envelope: this used to print nothing at all on the
        # non-JSON path for two of its three failure call sites (only the
        # `config is None` one worked around the gap with its own manual
        # `print_lines`, which is deleted below now that `_finish` covers it).
        # `--reinstall-server` with no config and no `--json` used to fail
        # silent at exit 5.
        return cli_json.emit_failure(
            message,
            command="doctor --reinstall-server",
            reason=reason,
            as_json=as_json,
            stdout=stdout,
            stderr=stderr,
            errors=errors,
            extra={"message": message},
            stderr_lines=error_lines(message, errors),
        )
    from pzi.node_runtime import ensure_node
    from pzi.ts_backend import ensure_translation_server, is_ts_reachable

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    if config is None:
        return _finish(
            exit_codes.ENVIRONMENT, "failed to load config", list(cfg["errors"]),
            reason=REASON_CONFIG,
        )

    # Subscript, not `.get` + guard: `AppConfig` is a total TypedDict and
    # `validate_app_config` rejects the config outright unless this is an
    # http(s) URL, so the guard here could never fire.
    ts_url = config["translation_server_url"]

    data_home = Path(config["pzi_data_home"])
    print("reinstalling translation-server …", file=progress)
    node_path = config.get("node_path")
    node = ensure_node(
        data_home,
        interactive=True,
        node_path=node_path if isinstance(node_path, str) else None,
        stdout=progress,
        stderr=stderr,
    )
    if node is None:
        return _finish(
            exit_codes.ENVIRONMENT, "Node.js is not available", ["Node.js is not available"]
        )
    ts_dir = data_home / "ts"
    if ts_dir.exists() and is_ts_reachable(ts_url):
        print(
            "warning: a translation-server is running; restart `pzi server` "
            "after the update to use the new version.",
            file=stderr,
        )
    # The old install is left in place: `ensure_translation_server` stages the
    # replacement and swaps it in only on success. Deleting it here first meant
    # a failed clone left the user with no translation-server at all — strictly
    # worse than before, on the command they ran to repair something.
    installed = ensure_translation_server(
        data_home, node, stdout=progress, stderr=stderr, force=True
    )
    if installed is None:
        return _finish(
            exit_codes.ENVIRONMENT,
            "translation-server reinstall failed",
            ["translation-server reinstall failed (see messages above)"],
        )
    return _finish(
        exit_codes.OK,
        "translation-server reinstalled. Run `pzi server` to start.",
        [],
    )
