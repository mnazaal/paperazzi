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
