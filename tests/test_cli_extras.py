import json
from io import StringIO
from pathlib import Path

from pzi.add_service import add_record_to_bib
from pzi.cli import run_cli


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
            "tags": ["ml"],
            "pdf_url": "https://example.com/a.pdf",
        },
        bib_selector=None,
        dry_run=False,
    )
    return config_path, bib_path


def test_bib_list(tmp_path: Path) -> None:
    config_path, bib_path = _seed(tmp_path)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["bib", "list", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert stdout.getvalue() == f"ml\t{bib_path} (default)\n"


def test_tag_list_all(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stdout = StringIO()
    exit_code = run_cli(
        ["tag", "list", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert stdout.getvalue().strip() == "ml"


def test_tag_add_and_list_for_citekey(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "tag",
            "add",
            "smith2024graph",
            "graphs",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert "added tags" in stdout.getvalue()

    list_stdout = StringIO()
    run_cli(
        ["tag", "list", "smith2024graph", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=list_stdout,
        stderr=StringIO(),
    )
    assert list_stdout.getvalue().splitlines() == ["graphs", "ml"]


def test_tag_remove(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stdout = StringIO()
    exit_code = run_cli(
        [
            "tag",
            "remove",
            "smith2024graph",
            "ml",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "removed tags" in stdout.getvalue()


def test_search_by_tag(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stdout = StringIO()
    exit_code = run_cli(
        ["search", "--tag", "ml", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "smith2024graph" in stdout.getvalue()


def test_bib_set_default_changes_config(tmp_path: Path) -> None:
    ml_path = tmp_path / "ml.bib"
    sys_path = tmp_path / "sys.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{ml_path}"
default = true

[[bibs]]
name = "sys"
path = "{sys_path}"
default = false
""".strip()
    )
    exit_code = run_cli(
        ["bib", "set-default", "sys", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert exit_code == 0
    list_out = StringIO()
    run_cli(
        ["bib", "list", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=list_out,
        stderr=StringIO(),
    )
    lines = list_out.getvalue().splitlines()
    assert any(line.startswith("sys\t") and "(default)" in line for line in lines)
    assert any(line.startswith("ml\t") and "(default)" not in line for line in lines)


def test_bib_set_default_preserves_optional_config_fields(tmp_path: Path) -> None:
    ml_path = tmp_path / "ml.bib"
    sys_path = tmp_path / "sys.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765
unpaywall_email = "reader@example.com"
unpaywall_email_cmd = "pass show unpaywall"
semantic_scholar_api_key = "s2-key"
semantic_scholar_api_key_cmd = "pass show s2"
flaresolverr_url = "http://127.0.0.1:8191"
browser_pdf_cmd = "/bin/echo"

[[bibs]]
name = "ml"
path = "{ml_path}"
default = true

[[bibs]]
name = "sys"
path = "{sys_path}"
default = false
""".strip()
    )

    exit_code = run_cli(
        ["bib", "set-default", "sys", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert exit_code == 0
    text = config_path.read_text()
    assert 'unpaywall_email = "reader@example.com"' in text
    assert 'unpaywall_email_cmd = "pass show unpaywall"' in text


def test_doctor_emits_json(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stdout = StringIO()
    exit_code = run_cli(
        ["doctor", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["config_ok"] is True


def test_pdf_retry_missing_citekey_errors(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["pdf", "retry", "missing", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 1
    assert "citekey not found" in stderr.getvalue()


def test_pdf_attach_local_file(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    local_pdf = tmp_path / "paper.pdf"
    local_pdf.write_bytes(b"%PDF-1.4 local")
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "pdf",
            "attach",
            "smith2024graph",
            str(local_pdf),
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert "attached PDF smith2024graph" in stdout.getvalue()


# --- init command tests ---


def test_init_creates_config(tmp_path: Path) -> None:
    config_path = tmp_path / "pzi" / "config.toml"
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["init", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert config_path.exists()
    assert "created" in stdout.getvalue()


def test_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("existing = true")
    stderr = StringIO()
    exit_code = run_cli(
        ["init", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "config already exists" in stderr.getvalue()


def test_init_force_overwrites(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("existing = true")
    stdout = StringIO()
    exit_code = run_cli(
        ["init", "--config", str(config_path), "--force"],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "created" in stdout.getvalue()


def test_init_setup_writes_services_and_browser(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "pzi" / "config.toml"
    stdout = StringIO()
    stderr = StringIO()

    monkeypatch.setattr(
        "pzi.setup_service.render_config",
        lambda *, bib_name, bib_path, with_browser, with_flaresolverr: "[setup]\n",
    )
    monkeypatch.setattr(
        "pzi.setup_service.write_service_files",
        lambda config_path, with_flaresolverr: ["/fake/service.yml"],
    )
    monkeypatch.setattr(
        "pzi.setup_service.install_playwright_browser",
        lambda browser, stdout, stderr: 0,
    )

    exit_code = run_cli(
        ["init", "--config", str(config_path), "--setup"],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    assert exit_code == 0
    assert "created" in stdout.getvalue()
    assert "/fake/service.yml" in stdout.getvalue()


def test_init_browser_install_failure(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "pzi" / "config.toml"
    stderr = StringIO()

    monkeypatch.setattr(
        "pzi.setup_service.render_config",
        lambda *, bib_name, bib_path, with_browser, with_flaresolverr: "[setup]\n",
    )
    monkeypatch.setattr(
        "pzi.setup_service.write_service_files",
        lambda config_path, with_flaresolverr: [],
    )
    monkeypatch.setattr(
        "pzi.setup_service.install_playwright_browser",
        lambda browser, stdout, stderr: 1,
    )

    exit_code = run_cli(
        ["init", "--config", str(config_path), "--setup"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "browser install failed" in stderr.getvalue()


# --- search command tests ---


def test_search_requires_at_least_one_filter(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    stderr = StringIO()
    exit_code = run_cli(
        ["search", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "at least one of --query, --author, --year, --tag" in stderr.getvalue()


def test_search_no_matches(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.search_bib",
        lambda **kwargs: {"status": "ok", "matches": []},
    )
    stdout = StringIO()
    exit_code = run_cli(
        ["search", "--query", "xyz", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "no matches" in stdout.getvalue()


def test_search_error_path(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.search_bib",
        lambda **kwargs: {"status": "error", "message": "bad", "errors": ["e1"]},
    )
    stderr = StringIO()
    exit_code = run_cli(
        ["search", "--query", "q", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "search failed" in stderr.getvalue()
    assert "e1" in stderr.getvalue()


# --- bib update / promote tests ---


def test_bib_update_dry_run(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.update_bib",
        lambda **kwargs: {
            "status": "ok",
            "dry_run": True,
            "items": [
                {"citekey": "a", "changed_fields": ["title"], "note": "updated"}
            ],
        },
    )
    stdout = StringIO()
    exit_code = run_cli(
        ["bib", "update", "ml", "--dry-run", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "DRY RUN:" in stdout.getvalue()
    assert "a: title [updated]" in stdout.getvalue()


def test_bib_update_no_updates(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.update_bib",
        lambda **kwargs: {"status": "ok", "dry_run": False, "items": []},
    )
    stdout = StringIO()
    exit_code = run_cli(
        ["bib", "update", "ml", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "no updates" in stdout.getvalue()


def test_bib_update_error(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.update_bib",
        lambda **kwargs: {"status": "error", "message": "fail", "errors": ["e1"]},
    )
    stderr = StringIO()
    exit_code = run_cli(
        ["bib", "update", "ml", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "update failed" in stderr.getvalue()


def test_bib_promote_dry_run(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.promote_bib",
        lambda **kwargs: {
            "status": "ok",
            "dry_run": True,
            "items": [
                {
                    "preprint_citekey": "pre",
                    "published_citekey": "pub",
                    "changed_fields": ["doi"],
                    "pdf_attached": True,
                    "note": "found",
                }
            ],
        },
    )
    stdout = StringIO()
    exit_code = run_cli(
        ["bib", "promote", "ml", "--dry-run", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "DRY RUN:" in stdout.getvalue()
    assert "pre -> pub: doi [PDF] [found]" in stdout.getvalue()


def test_bib_promote_no_preprints(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.promote_bib",
        lambda **kwargs: {"status": "ok", "dry_run": False, "items": []},
    )
    stdout = StringIO()
    exit_code = run_cli(
        ["bib", "promote", "ml", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "no preprints to promote" in stdout.getvalue()


def test_bib_promote_error(tmp_path: Path, monkeypatch) -> None:
    config_path, _ = _seed(tmp_path)
    monkeypatch.setattr(
        "pzi.cli.promote_bib",
        lambda **kwargs: {"status": "error", "message": "fail", "errors": ["e1"]},
    )
    stderr = StringIO()
    exit_code = run_cli(
        ["bib", "promote", "ml", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "promote failed" in stderr.getvalue()


# --- serve / services / browser tests ---


def test_serve_prints_and_runs(monkeypatch, tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    run_calls = []

    def mock_run_server(*, config_path, home_dir, host, port, security) -> None:
        run_calls.append((host, port, security))

    monkeypatch.setattr("pzi.http_api.run_server", mock_run_server)
    stdout = StringIO()
    exit_code = run_cli(
        ["serve", "--config", str(config_path), "--host", "0.0.0.0", "--port", "9999"],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "serving on 0.0.0.0:9999" in stdout.getvalue()
    assert run_calls == [("0.0.0.0", 9999, run_calls[0][2])]


def test_services_command(monkeypatch, tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    calls = []

    def mock_run_services(command, *, config_path, stdout, stderr) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr("pzi.cli.setup_service.run_services_command", mock_run_services)
    for sub in ("up", "down", "status"):
        exit_code = run_cli(
            ["services", sub, "--config", str(config_path)],
            home_dir=str(tmp_path),
            stdout=StringIO(),
            stderr=StringIO(),
        )
        assert exit_code == 0
    assert calls == ["up", "down", "status"]


def test_browser_install(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def mock_install(browser, *, stdout, stderr) -> int:
        calls.append(browser)
        return 0

    monkeypatch.setattr("pzi.cli.setup_service.install_playwright_browser", mock_install)
    exit_code = run_cli(
        ["browser", "install", "firefox"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert calls == ["firefox"]


def test_browser_install_default_browser(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def mock_install(browser, *, stdout, stderr) -> int:
        calls.append(browser)
        return 0

    monkeypatch.setattr("pzi.cli.setup_service.install_playwright_browser", mock_install)
    exit_code = run_cli(
        ["browser", "install"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert calls == ["chromium"]
