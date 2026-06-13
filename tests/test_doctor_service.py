"""Edge tests for doctor_service.py uncovered lines (line 80: _probe_translation_server)."""

from pzi.doctor_service import _probe_translation_server, doctor_check


def test_probe_translation_server_success(monkeypatch) -> None:
    """HTTP 200 → True."""
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            pass

    def fake_urlopen(request, *, timeout):
        return FakeResponse()

    monkeypatch.setattr("pzi.doctor_service.urlopen", fake_urlopen)
    assert _probe_translation_server("http://localhost:1969") is True


def test_probe_translation_server_http_error(monkeypatch) -> None:
    """HTTPError (e.g., 500) still returns True (server is reachable)."""
    from urllib.error import HTTPError

    class FakeErrorResponse:
        def read(self):
            return b""

        def close(self) -> None:
            pass

    def fake_urlopen(request, *, timeout):
        raise HTTPError("http://localhost:1969", 500, "Error", {}, FakeErrorResponse())

    monkeypatch.setattr("pzi.doctor_service.urlopen", fake_urlopen)
    assert _probe_translation_server("http://localhost:1969") is True


def test_probe_translation_server_urlerror(monkeypatch) -> None:
    """URLError → False."""
    from urllib.error import URLError

    def fake_urlopen(request, *, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr("pzi.doctor_service.urlopen", fake_urlopen)
    assert _probe_translation_server("http://localhost:1969") is False


def test_doctor_check_with_probe_error(tmp_path, monkeypatch) -> None:
    """When probe raises OSError, it's recorded as probe_error."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'translation_server_url = "http://localhost:1969"\n'
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        '[[bibs]]\n'
        'name = "ml"\n'
        f'path = "{bib_path}"\n'
        'default = true\n'
    )

    def failing_probe(url, *, timeout=2.0):
        raise OSError("no route to host")

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        translation_probe=failing_probe,
    )
    assert result["translation_server_reachable"] is False
    assert "no route to host" in result["translation_probe_error"]


def test_doctor_check_config_error(tmp_path) -> None:
    """Nonexistent config → config_ok=False."""
    result = doctor_check(
        config_path=str(tmp_path / "nonexistent.toml"),
        home_dir=str(tmp_path),
    )
    assert result["config_ok"] is False
    assert len(result["config_errors"]) > 0


# ---------------------------------------------------------------------------
# Semantic Scholar reachability
# ---------------------------------------------------------------------------


def test_doctor_s2_configured_and_reachable(tmp_path) -> None:
    """Key configured + probe passes → key_effective=True."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'semantic_scholar_api_key = "my-key"\n'
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        s2_probe=lambda **kw: True,
    )
    assert result["semantic_scholar"]["configured"] == "plaintext"
    assert result["semantic_scholar"]["reachable"] is True
    assert result["semantic_scholar"]["key_effective"] is True


def test_doctor_s2_not_configured_and_reachable(tmp_path) -> None:
    """No key + probe passes → key_effective=True (public tier works)."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        s2_probe=lambda **kw: True,
    )
    assert result["semantic_scholar"]["configured"] == "not configured"
    assert result["semantic_scholar"]["reachable"] is True
    assert result["semantic_scholar"]["key_effective"] is True


def test_doctor_s2_configured_and_unreachable(tmp_path) -> None:
    """Key configured + probe fails → key_effective=False."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'semantic_scholar_api_key_cmd = "echo my-key"\n'
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        s2_probe=lambda **kw: False,
    )
    assert result["semantic_scholar"]["configured"] == "cmd"
    assert result["semantic_scholar"]["reachable"] is False
    assert result["semantic_scholar"]["key_effective"] is False


def test_doctor_s2_not_configured_and_unreachable(tmp_path) -> None:
    """No key + probe fails → key_effective=None (can't tell)."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        s2_probe=lambda **kw: False,
    )
    assert result["semantic_scholar"]["configured"] == "not configured"
    assert result["semantic_scholar"]["reachable"] is False
    assert result["semantic_scholar"]["key_effective"] is None


def test_doctor_s2_probe_error(tmp_path) -> None:
    """Probe raises OSError → reachable=False, key_effective=None."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    def failing_probe(**kw):
        raise OSError("no route to host")

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        s2_probe=failing_probe,
    )
    assert result["semantic_scholar"]["reachable"] is False
    assert result["semantic_scholar"]["key_effective"] is None
    assert result["semantic_scholar"]["probe_error"] == "no route to host"


def test_doctor_s2_key_cmd_resolution(tmp_path) -> None:
    """semantic_scholar_api_key_cmd resolves via shell → key is detected."""
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        'semantic_scholar_api_key_cmd = "echo test-s2-key"\n'
        'api_listen_host = "127.0.0.1"\n'
        'api_listen_port = 8765\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    seen_api_key = []
    def capturing_probe(*, api_key=None, **kw):
        seen_api_key.append(api_key)
        return True

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        s2_probe=capturing_probe,
    )
    assert result["semantic_scholar"]["configured"] == "cmd"
    assert result["semantic_scholar"]["key_effective"] is True
    assert seen_api_key == ["test-s2-key"]
