"""Doctor/health services."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.capture_context import resolve_optional_value
from pzi.config import load_config_file
from pzi.errors import REASON_CONFIG, PziError
from pzi.metadata_sources import probe_s2_api

#: Config keys that name a shell-style command, checked for a resolvable
#: `argv[0]` without ever running them (`doctor` is a diagnostic, not a
#: trigger). `semantic_scholar_api_key_cmd` is also executed elsewhere in this
#: module to resolve the key itself — this check is additional, not a
#: replacement.
CMD_CONFIG_KEYS = (
    "browser_pdf_cmd",
    "page_metadata_cmd",
    "api_auth_token_cmd",
    "contact_email_cmd",
    "unpaywall_email_cmd",
    "semantic_scholar_api_key_cmd",
)


class DoctorBibStatus(TypedDict):
    name: str
    path: str
    path_exists: bool
    papers_dir: str
    papers_dir_exists: bool
    default: bool


class DoctorCmdCheck(TypedDict):
    key: str
    command: str
    resolved: str | None
    error: NotRequired[str]


class DoctorNodeStatus(TypedDict):
    configured: bool
    source: str
    value: str
    ok: bool
    resolved: NotRequired[str]
    error: NotRequired[str]


class DoctorResult(TypedDict):
    status: str
    #: Health problems, one per line. The envelope's documented failure channel
    #: is `errors`, and doctor only ever populated `config_errors`, so a
    #: `--json` consumer following the contract saw an empty list on every
    #: failure — including the hard config-load one.
    errors: list[str]
    config_path: str
    config_ok: bool
    config_errors: list[str]
    config_warnings: NotRequired[list[str]]
    bibs: list[DoctorBibStatus]
    translation_server_url: str | None
    translation_server_reachable: bool
    translation_probe_error: str | None
    credentials: dict[str, str]
    semantic_scholar: dict[str, Any]
    config_permissions_warning: str | None
    #: Whether `argv[0]` of every command-valued config key resolves. Never
    #: executes any of them.
    cmd_checks: list[DoctorCmdCheck]
    #: `node_path` / `PZI_NODE`, resolved the way `node_runtime.
    #: _resolve_node_override` resolves it. `None` when no override is set.
    node: DoctorNodeStatus | None
    #: `git` and `npm` presence on PATH — needed only to install the
    #: translation-server, and checked at install time and never again.
    dev_tools: dict[str, bool]
    #: The advisory (FINDINGS, exit 1) tier: optional-path degradations that
    #: leave every core operation working. Kept separate from `errors` (the
    #: ENVIRONMENT, exit 5, tier) — `doctor --json` carries both as distinct
    #: fields rather than merging them into one list.
    findings: list[str]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
def doctor_health_problems(result: DoctorResult) -> list[str]:
    """Every reason this library is not healthy; empty means it is.

    Lives here rather than in the runner so `status` and the exit code are two
    readings of one value. They used to be computed separately, and `status` was
    hardcoded `"ok"` whenever the config merely loaded — so a missing bib, an
    unreachable translation-server or a broken key command produced
    `"status": "ok"` on a run that exited 5.
    """
    problems: list[str] = []
    if not result.get("config_ok"):
        problems.extend(result.get("config_errors") or ["config could not be loaded"])
    for bib in result.get("bibs") or []:
        if not bib.get("path_exists"):
            problems.append(f"bib file not found: {bib.get('path')}")
    if result.get("translation_server_url") and not result.get(
        "translation_server_reachable"
    ):
        detail = result.get("translation_probe_error")
        problems.append(
            f"translation server unreachable at {result['translation_server_url']}"
            + (f" ({detail})" if detail else "")
        )
    key_error = (result.get("semantic_scholar") or {}).get("key_error")
    if key_error:
        # A configured secret command that cannot run is a config fault the user
        # must fix. An unreachable API (`probe_error`) stays advisory — that is
        # not the user's config being wrong.
        problems.append(f"semantic_scholar_api_key_cmd failed: {key_error}")
    return problems


def doctor_advisory_problems(result: Mapping[str, Any]) -> list[str]:
    """Every optional-path degradation worth reporting without failing the run.

    Sibling to :func:`doctor_health_problems`: that one is the fatal
    (ENVIRONMENT, exit 5) tier; this is the advisory (FINDINGS, exit 1) tier —
    a `*_cmd` whose `argv[0]` does not resolve, a broken `node_path`/`PZI_NODE`
    override, a missing `git`/`npm`, a missing `papers_dir`, or a
    `semantic_scholar_api_key` the API rejected. Every one of these leaves
    every core operation working, which is what keeps it out of the other
    tier. Kept as its own list rather than folded into `doctor_health_problems`
    so `doctor --json` can carry the two tiers as distinct fields instead of
    merging them into one.
    """
    problems: list[str] = []
    for check in result.get("cmd_checks") or []:
        error = check.get("error")
        if error:
            problems.append(f"{check['key']}: {error} ({check.get('command')})")
    node = result.get("node")
    if node is not None and not node.get("ok", True):
        problems.append(str(node.get("error") or "node override is broken"))
    dev_tools = result.get("dev_tools") or {}
    for tool in ("git", "npm"):
        if dev_tools.get(tool) is False:
            problems.append(
                f"{tool} is not installed (needed to install the translation server)"
            )
    for bib in result.get("bibs") or []:
        if bib.get("path_exists") and not bib.get("papers_dir_exists"):
            problems.append(
                f"papers_dir not found: {bib.get('papers_dir')} (bib {bib.get('name')})"
            )
    if (result.get("semantic_scholar") or {}).get("key_effective") is False:
        problems.append(
            "semantic_scholar_api_key is configured but the API rejected it"
        )
    return problems


def _resolve_cmd_argv0(command: str) -> tuple[str | None, str | None]:
    """Resolve `argv[0]` of a config `*_cmd` string without executing it.

    Splits with `shlex.split`, expands `~` on the first token, then checks it
    resolves and is executable: `shutil.which` for a bare name, `os.access`
    for a path (anything containing a path separator). Returns
    `(resolved_path, None)` on success or `(None, error_message)` — an
    unparsable or empty command is itself a reportable problem, not a crash.
    """
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return None, f"unparsable command: {exc}"
    if not tokens or not tokens[0]:
        return None, "empty command"
    raw_argv0 = tokens[0]
    argv0 = os.path.expanduser(raw_argv0)
    if os.sep in raw_argv0 or (os.altsep and os.altsep in raw_argv0):
        if os.path.isfile(argv0) and os.access(argv0, os.X_OK):
            return argv0, None
        return None, f"not found or not executable: {argv0}"
    resolved = shutil.which(argv0)
    if resolved is not None:
        return resolved, None
    return None, f"not found on PATH: {argv0}"


def check_cmd_resolutions(config: dict[str, Any]) -> list[DoctorCmdCheck]:
    """Whether `argv[0]` resolves for every configured command-valued key.

    Covers `CMD_CONFIG_KEYS`. A key that is unset or blank is skipped rather
    than reported — that is what "not configured" means everywhere else in
    `doctor`. Never runs any of the commands.
    """
    checks: list[DoctorCmdCheck] = []
    for key in CMD_CONFIG_KEYS:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        resolved, error = _resolve_cmd_argv0(value)
        entry: DoctorCmdCheck = {"key": key, "command": value, "resolved": resolved}
        if error:
            entry["error"] = error
        checks.append(entry)
    return checks


def check_node_override(node_path: str | None) -> DoctorNodeStatus | None:
    """Report a configured-but-broken `node_path` / `PZI_NODE` override.

    Resolves it exactly the way `node_runtime._resolve_node_override` does
    (env wins), so this can never disagree with what a real run would pick.
    That function runs `node --version` locally to check the minimum
    version — no network, no install, and this never calls `ensure_node`.
    Returns `None` when no override is set at all.
    """
    override = os.environ.get("PZI_NODE") or node_path
    if not override:
        return None
    source = "PZI_NODE" if os.environ.get("PZI_NODE") else "node_path"
    from pzi.node_runtime import _resolve_node_override

    try:
        resolved = _resolve_node_override(node_path)
    except RuntimeError as exc:
        return {
            "configured": True,
            "source": source,
            "value": override,
            "ok": False,
            "error": str(exc),
        }
    return {
        "configured": True,
        "source": source,
        "value": override,
        "ok": True,
        "resolved": resolved or "",
    }


def check_dev_tools() -> dict[str, bool]:
    """Whether `git` and `npm` are on PATH.

    Both are needed only to install the translation-server (`ts_backend.py`)
    and are checked once at install time and never again — this is the first
    time `doctor` reports their absence ahead of that.
    """
    return {"git": shutil.which("git") is not None, "npm": shutil.which("npm") is not None}


def config_permissions_warning(config_path: str) -> str | None:
    """Warn when the config file is readable/writable beyond its owner.

    The config can carry secrets and executable ``*_cmd`` / ``browser_pdf_cmd``
    / ``page_metadata_cmd`` hooks, so group/other access is a real exposure
    (read = secret leak, write = arbitrary command execution as the user).
    Returns a recommendation string, or ``None`` when perms are fine or cannot
    be determined (e.g. on a platform without POSIX modes).
    """
    try:
        mode = stat.S_IMODE(os.stat(config_path).st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return (
            f"config file is accessible to group/other (mode {mode:#o}); "
            f"it may hold secrets and executes *_cmd hooks — run "
            f"`chmod 600 {config_path}`"
        )
    return None


def doctor_check(
    *,
    config_path: str,
    home_dir: str,
    translation_probe=None,
    s2_probe=None,
    probe_network: bool = True,
) -> DoctorResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {
            "status": "error",
            "errors": list(config_result["errors"]),
            "reason": REASON_CONFIG,
            "config_path": config_result["path"],
            "config_ok": False,
            "config_errors": config_result["errors"],
            "bibs": [],
            "translation_server_url": None,
            "translation_server_reachable": False,
            "translation_probe_error": None,
            "credentials": {},
            "semantic_scholar": {},
            "cmd_checks": [],
            "node": None,
            "dev_tools": {},
            "findings": [],
            "config_permissions_warning": config_permissions_warning(
                config_result["path"]
            ),
        }
    config = config_result["config"]

    bibs: list[DoctorBibStatus] = []

    for bib in config["bibs"]:
        bibs.append(
            {
                "name": bib["name"],
                "path": bib["path"],
                "path_exists": Path(bib["path"]).exists(),
                "papers_dir": bib["papers_dir"],
                "papers_dir_exists": Path(bib["papers_dir"]).exists(),
                "default": bib["default"],
            }
        )

    translation_server_url = config["translation_server_url"]
    reachable = False
    probe_error: str | None = None
    try:
        if translation_probe is None:
            # Straight to the shared probe rather than through the bool-only
            # seam, so `doctor` can report what the port actually answered.
            from pzi.ts_backend import probe_translation_server

            observed = probe_translation_server(translation_server_url)
            reachable = observed.ok
            if not reachable:
                probe_error = observed.detail
        else:
            reachable = bool(translation_probe(translation_server_url))
    except OSError as exc:
        probe_error = str(exc)
        reachable = False

    # Semantic Scholar reachability.
    # A `semantic_scholar_api_key_cmd` that cannot run is a config fault, and
    # reporting config faults is this command's entire job — so record it and
    # carry on with the remaining diagnostics rather than aborting the report.
    # Kept separate from `probe_error` so "your key command is broken" stays
    # distinguishable from "the API is unreachable".
    s2_key_error: str | None = None
    try:
        s2_key = resolve_optional_value(
            command=config.get("semantic_scholar_api_key_cmd"),
            fallback=config.get("semantic_scholar_api_key"),
        )
    except PziError as exc:
        s2_key_error = exc.message
        s2_key = None
    s2_reachable = False
    s2_key_effective: bool | None = None
    s2_probe_error: str | None = None
    # `GET /health` sets `probe_network=False`. It is a liveness check for the
    # local server, it never surfaced this result — the payload carries neither
    # `reachable` nor `key_effective` — and the outbound call was measured at
    # 93 s when Semantic Scholar stalls, on an endpoint the extension's "Test
    # connection" calls with no timeout of its own. `pzi doctor` still probes:
    # there the user asked about their credentials.
    probe_s2 = s2_probe or (probe_s2_api if probe_network else None)
    if probe_s2 is not None:
        try:
            s2_reachable = bool(probe_s2(api_key=s2_key))
            if s2_reachable:
                s2_key_effective = True
            elif s2_key:
                s2_key_effective = False
            else:
                s2_key_effective = None
        except OSError as exc:
            s2_probe_error = str(exc)

    cmd_checks = check_cmd_resolutions(config)
    node_status = check_node_override(
        config.get("node_path") if isinstance(config.get("node_path"), str) else None
    )
    dev_tools = check_dev_tools()

    result: DoctorResult = {
        "status": "ok",
        "errors": [],
        "config_path": config_result["path"],
        "config_ok": True,
        "config_errors": [],
        #: Keys pzi does not recognize. Non-fatal, but a typo'd key silently
        #: reverts to the default — reporting them is what `doctor` is for.
        "config_warnings": list(config_result.get("warnings") or []),
        "bibs": bibs,
        "translation_server_url": translation_server_url,
        "translation_server_reachable": reachable,
        "translation_probe_error": probe_error,
        "credentials": _credential_status(config),
        "semantic_scholar": {
            "configured": _configured_status(
                cmd=config.get("semantic_scholar_api_key_cmd"),
                value=config.get("semantic_scholar_api_key"),
            ),
            "reachable": s2_reachable,
            "key_effective": s2_key_effective,
            "probe_error": s2_probe_error,
            "key_error": s2_key_error,
        },
        "cmd_checks": cmd_checks,
        "node": node_status,
        "dev_tools": dev_tools,
        "findings": [],
        "config_permissions_warning": config_permissions_warning(
            config_result["path"]
        ),
    }

    # One source of truth: `status` and the runner's exit code are now two
    # readings of the same list. `findings` is the sibling reading for the
    # advisory tier — see `doctor_advisory_problems`.
    result["errors"] = doctor_health_problems(result)
    result["status"] = "ok" if not result["errors"] else "error"
    result["findings"] = doctor_advisory_problems(result)
    return result


def _credential_status(config: dict[str, Any]) -> dict[str, str]:
    return {
        "contact_email": _configured_status(
            cmd=config.get("contact_email_cmd"), value=config.get("contact_email")
        ),
        "unpaywall_email": _configured_status(
            cmd=config.get("unpaywall_email_cmd"), value=config.get("unpaywall_email")
        ),
        "semantic_scholar_api_key": _configured_status(
            cmd=config.get("semantic_scholar_api_key_cmd"),
            value=config.get("semantic_scholar_api_key"),
        ),
    }


def _configured_status(*, cmd: object, value: object) -> str:
    if isinstance(cmd, str) and cmd.strip():
        return "cmd"
    if isinstance(value, str) and value.strip():
        return "plaintext"
    return "not configured"


def _probe_translation_server(url: str, *, timeout: float = 2.0) -> bool:
    """Whether translation-server — not merely *something* — answers at ``url``.

    Delegates to the one probe in `ts_backend`, so `doctor`, `is_ts_reachable`
    and the startup wait cannot disagree about what "reachable" means. They
    used to: each sent a bare `GET /` and counted any HTTP response as success,
    including an error response, so a 404 from an unrelated application holding
    the port passed all three while every capture failed.
    """
    from pzi.ts_backend import probe_translation_server

    return probe_translation_server(url, timeout=timeout).ok
