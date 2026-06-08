from pzi.capture_context import build_capture_context, resolve_optional_value


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

    assert context == {
        "config": config,
        "bib": bib,
        "unpaywall_email": "resolved:email-cmd:fallback@example.com",
        "contact_email": "resolved:contact-cmd:contact@example.com",
        "s2_api_key": "resolved:s2-cmd:fallback-key",
        "browser_pdf_cmd": "override hook",
        "browser": "firefox",
        "citekey_format": "{{ authors }}{{ year }}",
        "pdf_filename_format": "{{ citekey }}-{{ year }}",
    }


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

    assert context["contact_email"] == "resolved:contact-cmd:contact@example.com"
    assert context["unpaywall_email"] == "resolved:contact-cmd:contact@example.com"
