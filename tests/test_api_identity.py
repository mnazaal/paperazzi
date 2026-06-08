from pzi.capture_context import (
    metadata_user_agent,
    resolve_contact_email,
    resolve_unpaywall_email,
)


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


def test_resolve_unpaywall_email_falls_back_to_contact_email() -> None:
    config = {
        "contact_email": "contact@example.com",
        "contact_email_cmd": None,
        "unpaywall_email": None,
        "unpaywall_email_cmd": None,
    }

    assert resolve_unpaywall_email(config) == "contact@example.com"


def test_metadata_user_agent_includes_contact_email_when_available() -> None:
    assert metadata_user_agent("user@example.com") == "pzi/1.0 (mailto:user@example.com)"
    assert metadata_user_agent(None) == "pzi/1.0"
