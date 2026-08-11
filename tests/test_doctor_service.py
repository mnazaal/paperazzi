"""Edge tests for doctor_service.py uncovered lines (line 80: _probe_translation_server)."""

import os

from pzi.doctor_service import (
    _probe_translation_server,
    config_permissions_warning,
    doctor_check,
)


def test_config_permissions_warning_flags_group_other_access(tmp_path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("contact_email = 'x@y.z'\n")
    os.chmod(cfg, 0o644)
    warning = config_permissions_warning(str(cfg))
    assert warning is not None
    assert "chmod 600" in warning


def test_config_permissions_warning_clean_for_owner_only(tmp_path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("contact_email = 'x@y.z'\n")
    os.chmod(cfg, 0o600)
    assert config_permissions_warning(str(cfg)) is None


def test_config_permissions_warning_none_for_missing_file(tmp_path) -> None:
    assert config_permissions_warning(str(tmp_path / "nope.toml")) is None


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


def test_doctor_reports_a_failing_key_command_instead_of_crashing(tmp_path) -> None:
    """doctor is the command whose job is to report this misconfiguration.

    `run_shell_command` raised RuntimeError on a nonzero exit, which the CLI
    boundary deliberately does not catch — so the diagnostic command died on the
    very config fault it exists to diagnose.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'semantic_scholar_api_key_cmd = "false"\n\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        translation_probe=lambda _url: True,
        s2_probe=lambda **_kw: False,
    )

    # `status` now tracks health, so a broken key command makes the run unhealthy
    # — it used to say "ok" while the command exited 5.
    assert result["status"] == "error"
    assert any("semantic_scholar_api_key_cmd" in e for e in result["errors"])
    assert result["semantic_scholar"]["key_error"]
    # The rest of the report still ran.
    assert result["translation_server_reachable"] is True
    assert result["bibs"]


def test_doctor_reports_a_metacharacter_key_command(tmp_path) -> None:
    """`pass show x | head -1` is rejected by the injection guard, not a bug."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'semantic_scholar_api_key_cmd = "pass show s2 | head -1"\n\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    result = doctor_check(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        translation_probe=lambda _url: True,
        s2_probe=lambda **_kw: False,
    )

    assert "metacharacter" in result["semantic_scholar"]["key_error"]


def test_doctor_reports_a_config_key_it_does_not_recognize() -> None:
    """A typo'd key loads fine and does nothing — the exact failure `doctor` is
    for. `--config-only` reported it; the plain run computed the warning and
    then dropped it on the floor.
    """
    from pzi.cli_render import _render_doctor_result

    lines = _render_doctor_result({
        "config_ok": True,
        "config_path": "/tmp/config.toml",
        "config_errors": [],
        "config_warnings": ["unknown config key 'inbox_paths' (ignored)"],
        "bibs": [],
    })

    text = "\n".join(lines)
    assert "inbox_paths" in text


def test_health_does_not_reach_out_to_semantic_scholar(tmp_path) -> None:
    """`GET /health` is a liveness check for the local server.

    It probed Semantic Scholar on every call and returned none of the result —
    the payload carries neither `reachable` nor `key_effective`. Measured at
    93 s when S2 stalls, on the endpoint the browser extension's "Test
    connection" calls with no timeout of its own.
    """
    from pzi.http_get_routes import _health_payload

    probes: list[object] = []

    def _exploding_probe(*, api_key=None):
        probes.append(api_key)
        raise AssertionError("/health must not make an outbound request")

    import pzi.doctor_service as doctor

    # A real config: a missing one short-circuits `doctor_check` before the
    # probe, so pointing at one made this test pass against the unfixed code.
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path}/ml.bib"\ndefault = true\n'
    )

    original = doctor.probe_s2_api
    doctor.probe_s2_api = _exploding_probe
    try:
        payload = _health_payload(str(config), str(tmp_path))
    finally:
        doctor.probe_s2_api = original

    assert probes == [], "an outbound probe was attempted"
    assert "status" in payload


def test_doctor_itself_still_probes(tmp_path) -> None:
    """The switch must not silently disarm `pzi doctor`, where the user asked
    about their credentials."""
    from pzi.doctor_service import doctor_check

    calls: list[object] = []

    def _probe(*, api_key=None):
        calls.append(api_key)
        return True

    doctor_check(
        config_path=str(tmp_path / "missing.toml"),
        home_dir=str(tmp_path),
        s2_probe=_probe,
    )
    # A missing config short-circuits before the probe, so use a real one.
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path}/ml.bib"\ndefault = true\n'
    )
    doctor_check(config_path=str(config), home_dir=str(tmp_path), s2_probe=_probe)

    assert calls, "doctor stopped probing Semantic Scholar"
