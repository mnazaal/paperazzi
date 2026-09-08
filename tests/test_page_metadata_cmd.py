import pytest

from pzi.errors import REASON_CONFIG, PziError
from pzi.page_metadata_cmd import run_page_metadata_cmd


def test_run_page_metadata_cmd_sends_page_payload_and_parses_json() -> None:
    calls = []

    def fake_run(argv, *, input, text, capture_output, timeout, check):
        calls.append(
            {
                "argv": argv,
                "input": input,
                "text": text,
                "capture_output": capture_output,
                "timeout": timeout,
                "check": check,
            }
        )

        class Result:
            stdout = '{"title":"External Title","year":2024}'
            stderr = ""
            returncode = 0

        return Result()

    result = run_page_metadata_cmd(
        "metadata-tool --fast",
        url="https://example.com/paper",
        html="<html></html>",
        current_metadata={"title": "Page Title"},
        timeout_seconds=3,
        run=fake_run,
    )

    assert result == {"title": "External Title", "year": 2024}
    assert calls[0]["argv"] == ["metadata-tool", "--fast"]
    assert '"url": "https://example.com/paper"' in calls[0]["input"]
    assert '"html": "<html></html>"' in calls[0]["input"]
    assert calls[0]["timeout"] == 3


def test_run_page_metadata_cmd_rejects_non_object_json() -> None:
    def fake_run(*_args, **_kwargs):
        class Result:
            stdout = '["not", "object"]'
            stderr = ""
            returncode = 0

        return Result()

    result = run_page_metadata_cmd(
        "metadata-tool",
        url="https://example.com/paper",
        html="<html></html>",
        current_metadata={},
        run=fake_run,
    )

    assert result == {}


def test_run_page_metadata_cmd_returns_empty_on_timeout() -> None:
    import subprocess

    def fake_run(*_args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["slow-tool"], timeout=kwargs.get("timeout", 5)
        )

    result = run_page_metadata_cmd(
        "slow-tool",
        url="https://example.com",
        html="<html></html>",
        current_metadata={},
        timeout_seconds=3,
        run=fake_run,
    )

    assert result == {}


def test_run_page_metadata_cmd_expands_a_tilde_in_the_executable(tmp_path, monkeypatch) -> None:
    """`browser_pdf.py` already expands `~` in every token before exec (it
    documents why: `shell=False` means the shell never does it). This hook did
    not, so the same `~/bin/hook` string worked for one and not the other."""
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)

        class Result:
            stdout = "{}"
            stderr = ""
            returncode = 0

        return Result()

    run_page_metadata_cmd(
        "~/bin/hook.sh",
        url="https://example.com",
        html="<html></html>",
        current_metadata={},
        run=fake_run,
    )

    assert calls[0] == [str(tmp_path / "bin" / "hook.sh")]


def test_run_page_metadata_cmd_forwards_stderr_on_nonzero_exit(capsys) -> None:
    """A hook that ran and failed used to discard its stderr and return `{}`
    silently; the browser hook forwards it (control-stripped) so the user
    learns why. This makes the two agree."""

    def fake_run(*_args, **_kwargs):
        class Result:
            stdout = ""
            stderr = "hook exploded: missing API key\n"
            returncode = 1

        return Result()

    result = run_page_metadata_cmd(
        "metadata-tool",
        url="https://example.com",
        html="<html></html>",
        current_metadata={},
        run=fake_run,
    )

    assert result == {}
    assert "hook exploded: missing API key" in capsys.readouterr().err


def test_run_page_metadata_cmd_strips_control_characters_from_stderr(capsys) -> None:
    def fake_run(*_args, **_kwargs):
        class Result:
            stdout = ""
            stderr = "before\x1bafter"
            returncode = 1

        return Result()

    run_page_metadata_cmd(
        "metadata-tool",
        url="https://example.com",
        html="<html></html>",
        current_metadata={},
        run=fake_run,
    )

    assert "\x1b" not in capsys.readouterr().err


def test_run_page_metadata_cmd_missing_binary_message_matches_the_sibling_hooks() -> None:
    """`browser_pdf.py` and `capture_context.run_shell_command` both say
    "could not run"; this said "could not be run" — one word off, for the same
    failure."""

    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    with pytest.raises(PziError) as excinfo:
        run_page_metadata_cmd(
            "no-such-tool",
            url="https://example.com",
            html="<html></html>",
            current_metadata={},
            run=fake_run,
        )

    assert "page_metadata_cmd could not run:" in str(excinfo.value)
    assert excinfo.value.reason == REASON_CONFIG
