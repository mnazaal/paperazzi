import pytest

from pzi.capture_context import (
    build_capture_context,
    metadata_user_agent,
    resolve_api_auth_token,
    resolve_contact_email,
    resolve_optional_value,
    run_shell_command,
)
from pzi.errors import REASON_CONFIG, REASON_UNAVAILABLE, PziError


def test_resolve_api_auth_token_auto_reads_default_file(tmp_path) -> None:
    (tmp_path / "api_token").write_text("filetoken\n")
    config = {"pzi_data_home": str(tmp_path)}
    assert resolve_api_auth_token(config) == "filetoken"


def test_resolve_api_auth_token_none_when_no_sources(tmp_path) -> None:
    config = {"pzi_data_home": str(tmp_path)}  # no api_token file present
    assert resolve_api_auth_token(config) is None


def test_resolve_api_auth_token_plaintext_overrides_file(tmp_path) -> None:
    (tmp_path / "api_token").write_text("filetoken")
    config = {"pzi_data_home": str(tmp_path), "api_auth_token": "plain"}
    assert resolve_api_auth_token(config) == "plain"


def test_resolve_api_auth_token_cmd_overrides_file(tmp_path) -> None:
    (tmp_path / "api_token").write_text("filetoken")
    config = {"pzi_data_home": str(tmp_path), "api_auth_token_cmd": "get-token"}
    result = resolve_api_auth_token(config, run_command=lambda c: "cmdtoken\n")
    assert result == "cmdtoken"


def test_resolve_optional_value_prefers_command_result() -> None:
    result = resolve_optional_value(
        command="secret-cmd",
        fallback="fallback",
        run_command=lambda command: f"value from {command}\n",
    )

    assert result == "value from secret-cmd"


def test_resolve_optional_value_falls_back_without_command() -> None:
    result = resolve_optional_value(
        command=None,
        fallback="fallback",
        run_command=lambda command: f"value from {command}",
    )

    assert result == "fallback"


def test_resolve_optional_value_returns_none_for_blank_command_output() -> None:
    result = resolve_optional_value(
        command="secret-cmd",
        fallback="fallback",
        run_command=lambda command: "  \n",
    )

    assert result is None


def test_build_capture_context_resolves_runtime_options() -> None:
    config = {
        "unpaywall_email_cmd": "email-cmd",
        "unpaywall_email": "fallback@example.com",
        "contact_email_cmd": "contact-cmd",
        "contact_email": "contact@example.com",
        "semantic_scholar_api_key_cmd": "s2-cmd",
        "semantic_scholar_api_key": "fallback-key",
        "api_auth_token_cmd": "token-cmd",
        "api_auth_token": "fallback-token",
        "browser_pdf_cmd": "browser hook",
        "citekey_format": "{{ authors }}{{ year }}",
        "pdf_filename_format": "{{ citekey }}-{{ year }}",
    }
    bib = {"name": "main", "path": "/tmp/lib.bib", "papers_dir": "/tmp/papers"}

    context = build_capture_context(
        config=config,
        bib=bib,
        browser_pdf_cmd_override="override hook",
        browser="firefox",
        resolve_secret=lambda command, fallback: f"resolved:{command}:{fallback}",
    )

    assert context.config == config
    assert context.bib == bib
    assert context.unpaywall_email == "resolved:email-cmd:fallback@example.com"
    assert context.contact_email == "resolved:contact-cmd:contact@example.com"
    assert context.s2_api_key == "resolved:s2-cmd:fallback-key"
    assert context.browser_pdf_cmd == "override hook"
    assert context.browser == "firefox"
    assert context.citekey_format == "{{ authors }}{{ year }}"
    assert context.pdf_filename_format == "{{ citekey }}-{{ year }}"
    assert context.api_url == "http://127.0.0.1:8765"
    assert context.api_auth_token == "resolved:token-cmd:fallback-token"
    assert context.desktop_fallback_hosts == set()
    assert context.pdf_discovery_parallel is False
    assert context.ezproxy_host is None


def test_build_capture_context_uses_contact_email_as_unpaywall_fallback() -> None:
    config = {
        "unpaywall_email_cmd": None,
        "unpaywall_email": None,
        "contact_email_cmd": "contact-cmd",
        "contact_email": "contact@example.com",
        "semantic_scholar_api_key_cmd": None,
        "semantic_scholar_api_key": None,
        "browser_pdf_cmd": None,
        "citekey_format": None,
        "pdf_filename_format": None,
    }

    context = build_capture_context(
        config=config,
        bib={"name": "main", "path": "/tmp/main.bib", "papers_dir": "/tmp/papers"},
        browser_pdf_cmd_override=None,
        browser=None,
        resolve_secret=lambda command, fallback: f"resolved:{command}:{fallback}" if command else fallback,
    )

    assert context.contact_email == "resolved:contact-cmd:contact@example.com"
    assert context.unpaywall_email == "resolved:contact-cmd:contact@example.com"


def test_resolve_contact_email_prefers_cmd_over_plaintext() -> None:
    config = {
        "contact_email": "plain@example.com",
        "contact_email_cmd": "pass show email",
    }

    result = resolve_contact_email(
        config,
        run_command=lambda command: "cmd@example.com\n",
    )

    assert result == "cmd@example.com"


def test_metadata_user_agent_includes_contact_email_when_available() -> None:
    assert metadata_user_agent("user@example.com") == "pzi/1.0 (mailto:user@example.com)"
    assert metadata_user_agent(None) == "pzi/1.0"


# ---------------------------------------------------------------------------
# `run_shell_command` — the uniform launch-failure contract (item 617)
# ---------------------------------------------------------------------------


def test_run_shell_command_expands_a_tilde_in_the_executable(tmp_path, monkeypatch) -> None:
    """`shell=False` means the shell never expands `~` — `browser_pdf.py`
    already does this expansion itself; this call site did not, so
    `contact_email_cmd = "~/bin/hook"` worked for the browser hook and failed
    here with the same string."""
    monkeypatch.setenv("HOME", str(tmp_path))
    script = tmp_path / "bin"
    script.mkdir()
    hook = script / "hook.sh"
    hook.write_text("#!/bin/sh\necho tilde-worked\n", encoding="utf-8")
    hook.chmod(0o755)

    result = run_shell_command("~/bin/hook.sh", config_key="contact_email_cmd")

    assert result.strip() == "tilde-worked"


@pytest.mark.parametrize(
    "config_key,expected_name",
    [
        ("api_auth_token_cmd", "the api_auth_token_cmd command"),
        (None, "a configured secret command"),
    ],
)
def test_run_shell_command_empty_command_names_the_key(config_key, expected_name) -> None:
    with pytest.raises(PziError) as excinfo:
        run_shell_command("   ", config_key=config_key)

    assert str(excinfo.value) == f"{expected_name} is empty; remove it or give it a command"
    assert excinfo.value.reason == REASON_CONFIG


def test_run_shell_command_unparseable_command_names_the_key() -> None:
    with pytest.raises(PziError) as excinfo:
        run_shell_command('broken "quote', config_key="unpaywall_email_cmd")

    message = str(excinfo.value)
    assert message.startswith("the unpaywall_email_cmd command could not be parsed: ")
    assert excinfo.value.reason == REASON_CONFIG


def test_run_shell_command_missing_binary_is_reason_config(tmp_path) -> None:
    with pytest.raises(PziError) as excinfo:
        run_shell_command(
            str(tmp_path / "no-such-binary"), config_key="semantic_scholar_api_key_cmd"
        )

    assert excinfo.value.reason == REASON_CONFIG


def test_run_shell_command_nonzero_exit_is_reason_unavailable_not_config(tmp_path) -> None:
    """The command ran; this invocation failed. That is not the same fault as
    a command that could never run at all, so it gets a different `reason` —
    a caller (e.g. `pzi doctor`) can tell "fix your config" apart from "retry
    later"."""
    script = tmp_path / "fails.sh"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)

    with pytest.raises(PziError) as excinfo:
        run_shell_command(str(script), config_key="contact_email_cmd")

    assert excinfo.value.reason == REASON_UNAVAILABLE
