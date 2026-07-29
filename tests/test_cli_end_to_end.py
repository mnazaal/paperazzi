"""End-to-end CLI checks that previously could only be run by hand.

Three behaviours were carried for a long time as "verified manually, cannot be
automated": `pzi server` binding, `add --from-file` returning PARTIAL, and
`pzi check` exiting 5 with no network. Each blocker turned out to have a way
around it, recorded here so the checks stop depending on someone remembering to
run them:

* `pzi server` clones translation-server on start — unless
  ``PZI_SKIP_AUTO_START`` is set, which makes the backend session a no-op.
* `add` needs a translation-server — or a loopback stub (see
  ``tests/stub_translation_server.py``).
* "no network" needs an isolated machine — or ``unshare -rn``, which needs no
  root and is skipped cleanly where unavailable.

These spawn subprocesses and bind ports, so they are slower than the rest of the
suite; that is the price of covering the boundary the unit tests cannot reach.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pzi import exit_codes
from tests.stub_translation_server import stub_translation_server, translation_item

_GOOD_DOI = "10.1145/3372297"


def _write_config(tmp_path: Path, *, extra: str = "") -> Path:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'{extra}[[bibs]]\nname = "ml"\npath = "{bib_path}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n'
    )
    return config_path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_cli(argv: list[str], *, home: Path, env_extra: dict[str, str] | None = None,
             prefix: list[str] | None = None, timeout: int = 180):
    env = {**os.environ, "HOME": str(home), "PZI_SKIP_AUTO_START": "1"}
    env.update(env_extra or {})
    return subprocess.run(
        [*(prefix or []), sys.executable, "-c",
         "import sys; from pzi.cli import run_cli; sys.exit(run_cli(sys.argv[1:]))",
         *argv],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


# --- add --from-file: PARTIAL -------------------------------------------------


def test_add_from_file_exits_partial_on_a_mixed_batch(tmp_path: Path) -> None:
    """PARTIAL means *some* succeeded — so the test has to produce a real mix.

    An all-failed batch also exits 4, which is why it is not sufficient
    evidence: the stub resolves one identifier and refuses the other.
    """
    items = {_GOOD_DOI: translation_item(title="A Stub Paper", doi=_GOOD_DOI)}
    with stub_translation_server(items) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        from_file = tmp_path / "items.txt"
        from_file.write_text(f"{_GOOD_DOI}\nnot-resolvable-anywhere\n")

        proc = _run_cli(
            ["add", "--from-file", str(from_file), "--json",
             "--config", str(config_path)],
            home=tmp_path,
        )

    assert proc.returncode == exit_codes.PARTIAL, proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["counts"]["added"] == 1
    assert envelope["counts"]["failed"] == 1
    # A batch that captured something is not an "error": the exit code already
    # says "partly failed", and `.status` used to contradict it.
    assert envelope["status"] == "ok"
    # ...and the reasons belong in the documented failure channel, not only
    # inside items[].
    assert envelope["errors"], "failed items must be reported in errors[]"
    assert any("not-resolvable-anywhere" in e for e in envelope["errors"])


def test_add_from_file_reports_an_error_when_nothing_was_captured(tmp_path: Path) -> None:
    """Nothing captured *is* an error status, even though the exit code is 4."""
    with stub_translation_server({}) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        from_file = tmp_path / "items.txt"
        from_file.write_text("not-resolvable-anywhere\nalso-not-resolvable\n")

        proc = _run_cli(
            ["add", "--from-file", str(from_file), "--json",
             "--config", str(config_path)],
            home=tmp_path,
        )

    assert proc.returncode == exit_codes.PARTIAL
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "error"
    assert envelope["errors"]


# --- pzi server ---------------------------------------------------------------


def test_pzi_server_binds_serves_and_enforces_auth(tmp_path: Path) -> None:
    """The `server` command itself, not just the request handlers.

    Everything below `run_server` is covered by the in-process HTTP tests; what
    was never exercised is the command that builds the plan, reports the auth
    posture, and binds the socket.
    """
    port = _free_port()
    token = "test-token-not-a-real-secret"
    config_path = _write_config(
        tmp_path, extra=f'api_auth_token = "{token}"\napi_listen_port = {port}\n'
    )

    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from pzi.cli import run_cli; sys.exit(run_cli(sys.argv[1:]))",
         "server", "--port", str(port), "--config", str(config_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "HOME": str(tmp_path), "PZI_SKIP_AUTO_START": "1"},
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"server exited early: {proc.communicate()[1]}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("server never bound its port")

        def get(path: str, *, with_token: bool) -> tuple[int, str]:
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
            if with_token:
                request.add_header("X-Pzi-Token", token)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read().decode()

        assert get("/health", with_token=False)[0] == 401
        status, body = get("/health", with_token=True)
        assert status == 200, body
        status, body = get("/entries", with_token=True)
        assert status == 200, body
        assert json.loads(body)["status"] == "ok"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover — defensive
            proc.kill()


# --- offline check ------------------------------------------------------------


def _unshare_works() -> bool:
    if shutil.which("unshare") is None:
        return False
    try:
        return subprocess.run(
            ["unshare", "-rn", "true"], capture_output=True, timeout=15
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):  # pragma: no cover — defensive
        return False


@pytest.mark.skipif(
    not _unshare_works(), reason="needs unshare -rn for an isolated network namespace"
)
def test_check_exits_environment_with_no_network(tmp_path: Path) -> None:
    """An audit that reached no source audited nothing, and must not exit 0.

    Exiting 0 there reports a clean library, which is precisely the claim the
    run cannot make. `unshare -rn` gives a namespace with no route out, so every
    provider genuinely fails rather than being mocked into failing.
    """
    config_path = _write_config(tmp_path)
    (tmp_path / "ml.bib").write_text(
        "@article{smith2024,\n  title = {Graph Parsers},\n  doi = {10.1/foo}\n}\n"
    )

    proc = _run_cli(
        ["check", "--config", str(config_path)],
        home=tmp_path,
        prefix=["unshare", "-rn"],
        timeout=240,
    )

    assert proc.returncode == exit_codes.ENVIRONMENT, (proc.stdout, proc.stderr)
    combined = proc.stdout + proc.stderr
    assert "no source could be reached" in combined, combined
    # Every provider must be named as unreachable, not silently skipped.
    assert "crossref: unreachable" in combined
