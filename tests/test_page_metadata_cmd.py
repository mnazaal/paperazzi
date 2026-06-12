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
