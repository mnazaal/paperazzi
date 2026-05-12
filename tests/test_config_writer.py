import tomllib

from pzi.config import validate_app_config
from pzi.config_writer import dump_app_config


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
