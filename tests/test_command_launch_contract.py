"""The uniform contract every owned `*_cmd` config key must agree on.

pzi's dominant defect shape is a fix that lands at one call site and not its
siblings (item 617). This module is the countermeasure: one parametrized test
crossing every command-valued config key this lane owns with every way a
configured command can fail to launch at all, asserting the same verdict —
``PziError`` with ``reason=REASON_CONFIG`` — for all of them.

`browser_pdf_cmd` is deliberately excluded. It belongs to the hook-command
lane (`browser_pdf.py`) and returns `None` on a launch failure instead of
raising — a considered exception recorded in `page_metadata_cmd.py`'s module
docstring, not an oversight. The parent lane adds its row to this test at
integration.

`test_the_spanning_test_covers_every_cmd_key_pzi_has` is what makes a new
`*_cmd` key have to appear in `_COMMAND_KEYS` or fail this module: it reads
`pzi.config.OPTIONAL_STRING_KEYS`, the actual source of truth for which config
keys exist, rather than trusting this file's own list to stay current.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pzi.capture_context import run_shell_command
from pzi.config import OPTIONAL_STRING_KEYS
from pzi.errors import REASON_CONFIG, PziError
from pzi.page_metadata_cmd import run_page_metadata_cmd

#: Excluded from this test on purpose — see the module docstring.
_EXCLUDED_FROM_THIS_TEST = frozenset({"browser_pdf_cmd"})

_COMMAND_KEYS = [
    "page_metadata_cmd",
    "api_auth_token_cmd",
    "contact_email_cmd",
    "unpaywall_email_cmd",
    "semantic_scholar_api_key_cmd",
]


def test_the_spanning_test_covers_every_cmd_key_pzi_has() -> None:
    all_cmd_keys = {key for key in OPTIONAL_STRING_KEYS if key.endswith("_cmd")}
    assert all_cmd_keys - _EXCLUDED_FROM_THIS_TEST == set(_COMMAND_KEYS)


def _launch(config_key: str, command: str) -> None:
    """Run *command* through the same primitive each owned key actually uses."""
    if config_key == "page_metadata_cmd":
        run_page_metadata_cmd(
            command,
            url="https://example.com/paper",
            html="<html></html>",
            current_metadata={},
        )
    else:
        run_shell_command(command, config_key=config_key)


def _non_executable_file(tmp_path: Path) -> str:
    script = tmp_path / "not-executable"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o644)
    return str(script)


_FAILURE_MODES = {
    "missing binary": lambda tmp_path: "/no/such/pzi-test-binary-3f8a --flag",
    "non-executable file": _non_executable_file,
    "empty string": lambda tmp_path: "   ",
    "unbalanced quote": lambda tmp_path: 'broken "quote',
}


@pytest.mark.parametrize("config_key", _COMMAND_KEYS)
@pytest.mark.parametrize("failure_mode", sorted(_FAILURE_MODES))
def test_every_owned_command_key_agrees_on_a_broken_command(
    failure_mode: str, config_key: str, tmp_path: Path,
) -> None:
    command = _FAILURE_MODES[failure_mode](tmp_path)

    with pytest.raises(PziError) as excinfo:
        _launch(config_key, command)

    assert excinfo.value.reason == REASON_CONFIG, (
        f"{config_key} under {failure_mode!r} raised reason="
        f"{excinfo.value.reason!r}, not REASON_CONFIG"
    )


@pytest.mark.parametrize("failure_mode", sorted(_FAILURE_MODES))
def test_browser_pdf_cmd_differs_on_purpose_and_still_says_why(
    failure_mode: str, tmp_path: Path,
) -> None:
    """The excluded key's row, added by the hook-command lane at integration.

    `browser_pdf_cmd` is the one launcher that does **not** raise. It sits
    mid-way down a fallback chain — server browser, then this hook, then
    FlareSolverr, then the desktop-download watcher — and raising here aborts
    the rungs below it, which is worse than the misconfiguration. So it returns
    `None`, and the chain continues.

    What it owes in exchange is the reason. Reporting a hook that could not
    start as "no PDF returned" is what let a `browser_pdf_cmd` naming a deleted
    interpreter look like a paper that simply has no PDF available, for three
    days across a whole library. The soft return is the exception; staying
    silent never was.
    """
    from pzi.browser_pdf import download_pdf_with_browser

    command = _FAILURE_MODES[failure_mode](tmp_path)
    errors: list[str] = []

    result = download_pdf_with_browser(
        command=command, pdf_url="https://example.com/a.pdf", errors=errors
    )

    assert result is None, "the fallback chain must continue past a broken hook"
    assert errors, (
        f"browser_pdf_cmd under {failure_mode!r} returned None without saying why"
    )
