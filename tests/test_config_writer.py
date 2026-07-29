import tomllib

from pzi.config import dump_app_config, validate_app_config


def test_dump_app_config_roundtrips_through_tomllib() -> None:
    config = {
        "translation_server_url": "http://127.0.0.1:1969",
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
        "browser_pdf_cmd": "python /tmp/browser_hook.py",
        "bibs": [
            {
                "name": "ml",
                "path": "/tmp/ml.bib",
                "papers_dir": "/tmp/papers",
                "default": True,
            },
            {
                "name": "sys",
                "path": "/tmp/sys.bib",
                "papers_dir": "/tmp/sys-papers",
                "default": False,
            },
        ],
    }
    text = dump_app_config(config)
    parsed = tomllib.loads(text)

    validated, errors = validate_app_config(parsed, home_dir="/home/user")
    assert errors == []
    assert validated is not None
    assert {b["name"] for b in validated["bibs"]} == {"ml", "sys"}
    assert validated["api_listen_port"] == 8765
    assert validated["browser_pdf_cmd"] == "python /tmp/browser_hook.py"


def test_dump_app_config_escapes_double_quotes_and_backslashes() -> None:
    config = {
        "translation_server_url": "http://127.0.0.1:1969",
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
        "browser_pdf_cmd": None,
        "bibs": [
            {
                "name": 'weird"name',
                "path": "/tmp/with\\back.bib",
                "papers_dir": "/tmp/papers",
                "default": True,
            }
        ],
    }
    text = dump_app_config(config)
    parsed = tomllib.loads(text)
    assert parsed["bibs"][0]["name"] == 'weird"name'
    assert parsed["bibs"][0]["path"] == "/tmp/with\\back.bib"


def test_dump_app_config_roundtrips_an_explicit_empty_desktop_fallback_hosts() -> None:
    """`desktop_fallback_hosts = []` means "no host needs the desktop fallback".

    The writer used to emit the key only when it was truthy *and* differed from
    the default, so an explicit empty list vanished and a round-trip silently
    restored all five default hosts — reversing the setting. Nothing covered
    this: the read side is tested in test_config.py, the writer was not.
    """
    config = {
        "translation_server_url": "http://127.0.0.1:1969",
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
        "desktop_fallback_hosts": [],
        "bibs": [
            {"name": "ml", "path": "/tmp/ml.bib", "papers_dir": "/tmp/p", "default": True}
        ],
    }

    parsed = tomllib.loads(dump_app_config(config))

    assert parsed["desktop_fallback_hosts"] == []
    validated, errors = validate_app_config(parsed, home_dir="/home/user")
    assert errors == []
    assert validated is not None
    assert validated["desktop_fallback_hosts"] == []


def test_dump_app_config_omits_desktop_fallback_hosts_when_left_at_the_default() -> None:
    """The defaults stay implicit, so the file does not pin them."""
    from pzi.config import DEFAULT_DESKTOP_FALLBACK_HOSTS

    config = {
        "translation_server_url": "http://127.0.0.1:1969",
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
        "desktop_fallback_hosts": list(DEFAULT_DESKTOP_FALLBACK_HOSTS),
        "bibs": [
            {"name": "ml", "path": "/tmp/ml.bib", "papers_dir": "/tmp/p", "default": True}
        ],
    }

    parsed = tomllib.loads(dump_app_config(config))

    assert "desktop_fallback_hosts" not in parsed
    validated, _ = validate_app_config(parsed, home_dir="/home/user")
    assert validated is not None
    assert validated["desktop_fallback_hosts"] == list(DEFAULT_DESKTOP_FALLBACK_HOSTS)
