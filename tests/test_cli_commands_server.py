"""`pzi server`'s runner, which nothing ran.

The module sat at 29%: every test addressed `build_server_plan`, the pure
planning function, so the runner around it — the auth banner, the token
resolution failure, the plan-refusal exit code — was executed by nothing. The
`auth: DISABLED` line in particular is the *only* signal that an API anyone
local can use is running unauthenticated.
"""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path

import pytest

from pzi import exit_codes
from pzi.commands import server as server_command
from pzi.errors import REASON_CONFIG, PziError

MINIMAL_CONFIG = """
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
"""


def _args(**kw) -> Namespace:
    base = dict(host="127.0.0.1", port=8765, no_auth=False, stop_after=None,
                log_requests=False)
    base.update(kw)
    return Namespace(**base)


def _run(args: Namespace, tmp_path: Path, config_path: Path) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    code = server_command.run_server_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(config_path),
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _no_config(tmp_path: Path) -> Path:
    """A config path that does not exist, so `config` is None and no backend starts."""
    return tmp_path / "absent.toml"


def test_an_unauthenticated_server_says_so_on_every_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token resolved under a different data home comes back as None silently.

    "It started fine" is therefore not evidence that auth is on, and this line
    is the only place the operator can find out that it is not.
    """
    served = []
    monkeypatch.setattr(
        server_command, "run_server", lambda **kw: served.append(kw)
    )

    code, stdout, stderr = _run(_args(no_auth=True), tmp_path, _no_config(tmp_path))

    assert code == exit_codes.OK, stderr
    assert "serving on 127.0.0.1:8765" in stdout
    assert "auth: DISABLED" in stderr
    assert "any local process can use this API" in stderr
    assert len(served) == 1


def test_an_authenticated_server_says_that_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'api_auth_token = "s3cret"\n' + MINIMAL_CONFIG.format(bib_path=bib_path),
        encoding="utf-8",
    )
    monkeypatch.setattr(server_command, "run_server", lambda **_kw: None)
    # The backend session is the translation-server bootstrap; it is not what
    # this test is about, and starting it would clone a repository.
    import contextlib

    import pzi.ts_backend

    @contextlib.contextmanager
    def _ready(*_a, **_kw):
        yield {"ready": True, "owned": False}

    monkeypatch.setattr(pzi.ts_backend, "backend_session", _ready)

    code, _stdout, stderr = _run(_args(), tmp_path, config_path)

    assert code == exit_codes.OK, stderr
    assert "auth: enabled (token required)" in stderr
    assert "auth: DISABLED" not in stderr


def test_an_unresolvable_auth_token_command_stops_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting anyway would serve unauthenticated under a config that asked for auth.

    `resolve_api_auth_token` raises `PziError` (`capture_context.py`), which
    subclasses `Exception`, not `RuntimeError`/`ValueError` — a mock that
    raised a bare `RuntimeError` here would pass while the real failure mode
    fell through to the generic CLI handler instead of this message.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")

    def _boom(_config):
        raise PziError(
            "the api_auth_token_cmd command exited with code 127", reason=REASON_CONFIG
        )

    monkeypatch.setattr(server_command, "resolve_api_auth_token", _boom)
    monkeypatch.setattr(
        server_command,
        "run_server",
        lambda **_kw: pytest.fail("the server started with no token"),
    )

    code, stdout, stderr = _run(_args(), tmp_path, config_path)

    assert code == exit_codes.ENVIRONMENT
    assert "failed to resolve api_auth_token_cmd" in stderr
    assert "exited with code 127" in stderr
    assert stdout == ""


def test_a_real_broken_auth_token_cmd_stops_the_server_end_to_end(
    tmp_path: Path,
) -> None:
    """No mocking: a missing binary in `api_auth_token_cmd` walks the real
    `resolve_api_auth_token` -> `run_shell_command` chain and must still be
    caught here, not escape as an unhandled `PziError`."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    # The `api_auth_token_cmd` line must precede `[[bibs]]` — TOML puts any
    # bare key after a table header inside that table, not at the top level.
    config_path.write_text(
        f'api_auth_token_cmd = "{tmp_path / "no-such-binary"}"\n'
        + MINIMAL_CONFIG.format(bib_path=bib_path),
        encoding="utf-8",
    )

    code, stdout, stderr = _run(_args(), tmp_path, config_path)

    assert code == exit_codes.ENVIRONMENT
    assert "failed to resolve api_auth_token_cmd" in stderr
    assert stdout == ""


def test_a_refused_plan_reports_the_config_errors_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--host 0.0.0.0` is refused; the runner has to say why and exit 5."""
    monkeypatch.setattr(
        server_command,
        "run_server",
        lambda **_kw: pytest.fail("the server started on a refused plan"),
    )

    code, _stdout, stderr = _run(
        _args(host="0.0.0.0"), tmp_path, _no_config(tmp_path)
    )

    assert code == exit_codes.ENVIRONMENT
    assert "wildcard" in stderr
    # The config errors ride along: a refused plan is often a config fault, and
    # the plan message alone does not name the file.
    assert "config file not found" in stderr
