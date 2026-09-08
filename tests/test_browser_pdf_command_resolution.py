"""How a browser hook command is resolved, and what it says when it cannot be.

The bug these pin down: `pzi init --setup` wrote the *then-current*
`sys.executable` into `browser_pdf_cmd`. Renaming the distribution deleted that
interpreter, so every hook invocation failed with `[Errno 2]` — and the failure
reached the user as "no PDF returned", indistinguishable from a paper that
genuinely has no PDF available.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from pzi.browser_pdf import (
    discover_pdf_url_with_browser,
    download_pdf_with_browser,
    healed_tokens,
    resolve_browser_command,
)

_DEAD = "/nonexistent/uv/tools/paperazzi/bin/python3"
_HOOK_ARGS = ["-m", "pzi.browser_pdf_hook", "--browser", "chromium"]


# --- resolve_browser_command: splitting only, no filesystem opinions ---


def test_resolve_expands_tilde_in_every_token() -> None:
    profile = os.path.join("~", "somewhere")
    tokens = resolve_browser_command(f"{sys.executable} -m pzi.x --profile {profile}")
    assert tokens[-1] == os.path.expanduser(profile)


def test_resolve_refuses_an_empty_command() -> None:
    with pytest.raises(ValueError, match="empty browser command"):
        resolve_browser_command("   ")


def test_resolve_refuses_an_unbalanced_quote() -> None:
    with pytest.raises(ValueError):
        resolve_browser_command('python -m pzi.browser_pdf_hook --profile "oops')


# --- healed_tokens: when substituting the interpreter is safe ---


def test_heals_a_dead_interpreter_in_front_of_our_own_hook() -> None:
    healed = healed_tokens([_DEAD, *_HOOK_ARGS])
    assert healed == [sys.executable, *_HOOK_ARGS]


def test_does_not_heal_someone_elses_program() -> None:
    """Substitution is safe only because the module is pzi's own. A third-party
    hook that has gone missing is a config fault to report, not a command to
    rewrite."""
    assert healed_tokens(["/nonexistent/bin/not-a-real-hook", "--flag"]) is None


def test_does_not_heal_a_different_module() -> None:
    assert healed_tokens([_DEAD, "-m", "something.else"]) is None


def test_does_not_heal_when_argv0_is_already_us() -> None:
    """If the running interpreter itself failed to start the hook, the fault is
    something other than a stale path, and retrying it identically would loop."""
    assert healed_tokens([sys.executable, *_HOOK_ARGS]) is None


# --- the retry, driven through the real call sites ---


def _enoent(*args, **kwargs):
    raise FileNotFoundError(2, "No such file or directory", _DEAD)


def test_download_retries_with_the_running_interpreter() -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[0] == _DEAD:
            raise FileNotFoundError(2, "No such file or directory", _DEAD)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    with patch("pzi.browser_pdf.subprocess.run", side_effect=fake_run):
        with patch("pzi.browser_pdf._notified", set()):
            download_pdf_with_browser(
                command=f"{_DEAD} -m pzi.browser_pdf_hook --browser chromium",
                pdf_url="https://example.com/a.pdf",
            )

    assert len(calls) == 2, "the hook should be retried exactly once"
    assert calls[0][0] == _DEAD
    assert calls[1][0] == sys.executable
    assert calls[1][1:] == _HOOK_ARGS


def test_the_substitution_is_announced_once_per_process(capsys) -> None:
    """A per-entry notice on a `--failed-only` sweep buries the summary it is
    trying to explain."""

    def fake_run(argv, **kwargs):
        if argv[0] == _DEAD:
            raise FileNotFoundError(2, "No such file or directory", _DEAD)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    command = f"{_DEAD} -m pzi.browser_pdf_hook --browser chromium"
    with patch("pzi.browser_pdf.subprocess.run", side_effect=fake_run):
        with patch("pzi.browser_pdf._notified", set()):
            download_pdf_with_browser(command=command, pdf_url="https://e.com/a.pdf")
            download_pdf_with_browser(command=command, pdf_url="https://e.com/b.pdf")
            printed = capsys.readouterr().err

    assert printed.count(_DEAD) == 1


# --- the reason reaches the caller, not just stderr ---


def test_download_records_why_the_hook_could_not_run() -> None:
    """`pdf.py` reported every hook failure as "no PDF returned". A hook that
    could not start is a different fact, and the retry sweep needs to see it."""
    errors: list[str] = []
    with patch("pzi.browser_pdf.subprocess.run", side_effect=_enoent):
        result = download_pdf_with_browser(
            command="/nonexistent/bin/not-a-real-hook",
            pdf_url="https://example.com/a.pdf",
            errors=errors,
        )
    assert result is None
    assert errors, "a hook that cannot launch must say so"
    assert "could not run" in errors[0]


def test_discover_records_why_the_hook_could_not_run() -> None:
    errors: list[str] = []
    with patch("pzi.browser_pdf.subprocess.run", side_effect=_enoent):
        result = discover_pdf_url_with_browser(
            command="/nonexistent/bin/not-a-real-hook",
            page_url="https://example.com",
            errors=errors,
        )
    assert result is None
    assert errors
    assert "could not run" in errors[0]


def test_download_records_an_empty_command_as_its_own_fault() -> None:
    errors: list[str] = []
    assert (
        download_pdf_with_browser(
            command="  ", pdf_url="https://e.com/a.pdf", errors=errors
        )
        is None
    )
    assert any("empty browser command" in e for e in errors)


def test_the_fallback_chain_reports_a_launch_failure_as_one(tmp_path) -> None:
    """End of the wire: the reason has to survive as far as the caller that
    prints it. `pdf.py` flattened every hook outcome into "no PDF returned",
    which is what made a config pointing at a deleted interpreter read as a
    paper that simply has no PDF."""
    from pzi.pdf import fetch_and_store_pdf_with_fallbacks

    def blocked(url: str) -> tuple[bytes, str | None]:
        return (b"<html>blocked</html>", "text/html")

    with patch("pzi.browser_pdf.subprocess.run", side_effect=_enoent):
        _local, _warning, error = fetch_and_store_pdf_with_fallbacks(
            url="https://example.com/paper.pdf",
            papers_dir=str(tmp_path / "papers"),
            citekey="stale2026",
            fetch_binary=blocked,
            browser_pdf_cmd="/nonexistent/bin/not-a-real-hook",
        )

    assert error is not None
    assert "could not run" in error
    assert "no PDF returned" not in error


def test_download_records_a_timeout_distinctly_from_a_launch_failure() -> None:
    errors: list[str] = []
    with patch(
        "pzi.browser_pdf.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1),
    ):
        assert (
            download_pdf_with_browser(
                command=f"{sys.executable} -m pzi.browser_pdf_hook",
                pdf_url="https://e.com/a.pdf",
                errors=errors,
            )
            is None
        )
    assert any("timed out" in e for e in errors)
